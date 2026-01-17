"""
Dolphin - Audio Volume Limiter
Automatically reduces system volume when audio gets too loud to protect your ears.
"""

import sys
import threading
import time
import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk
import json
import os
from pathlib import Path
import winreg
import ctypes
from ctypes import wintypes
from comtypes import CLSCTX_ALL
from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume, IAudioMeterInformation
from ctypes import cast, POINTER

try:
    import pystray
    from PIL import Image, ImageDraw
    TRAY_AVAILABLE = True
except ImportError:
    TRAY_AVAILABLE = False


class GlobalHotkeyListener:
    """Windows-only global hotkey listener using RegisterHotKey + message loop."""

    WM_HOTKEY = 0x0312
    WM_QUIT = 0x0012

    MOD_ALT = 0x0001
    MOD_CONTROL = 0x0002
    MOD_SHIFT = 0x0004
    MOD_WIN = 0x0008

    VK_UP = 0x26
    VK_DOWN = 0x28
    VK_OEM_PLUS = 0xBB   # + key
    VK_OEM_MINUS = 0xBD  # - key
    VK_ADD = 0x6B        # Numpad +
    VK_SUBTRACT = 0x6D   # Numpad -
    VK_Y = 0x59

    def __init__(self, on_increase_threshold, on_decrease_threshold, on_toggle_enabled):
        self._on_inc = on_increase_threshold
        self._on_dec = on_decrease_threshold
        self._on_toggle = on_toggle_enabled

        self._stop = threading.Event()
        self._thread = None
        self._thread_id = None
        self._registered_ids = []

    def start(self):
        if sys.platform != "win32":
            return
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        if sys.platform != "win32":
            return
        self._stop.set()
        try:
            if self._thread_id:
                ctypes.windll.user32.PostThreadMessageW(self._thread_id, self.WM_QUIT, 0, 0)
        except Exception:
            pass
        if self._thread:
            self._thread.join(timeout=1.0)

    def _run(self):
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32

        self._thread_id = kernel32.GetCurrentThreadId()

        # Default hotkeys:
        # - Ctrl+Alt+Plus / Ctrl+Alt+Numpad+ : increase Volume Cap
        # - Ctrl+Alt+Minus / Ctrl+Alt+Numpad- : decrease Volume Cap
        # - Ctrl+Alt+Y : toggle limiter enabled/disabled
        hotkeys = [
            (1, self.MOD_CONTROL | self.MOD_ALT, self.VK_OEM_PLUS),
            (2, self.MOD_CONTROL | self.MOD_ALT, self.VK_OEM_MINUS),
            (3, self.MOD_CONTROL | self.MOD_ALT, self.VK_ADD),
            (4, self.MOD_CONTROL | self.MOD_ALT, self.VK_SUBTRACT),
            (5, self.MOD_CONTROL | self.MOD_ALT, self.VK_Y),
        ]

        self._registered_ids.clear()
        for hotkey_id, modifiers, vk in hotkeys:
            try:
                if user32.RegisterHotKey(None, hotkey_id, modifiers, vk):
                    self._registered_ids.append(hotkey_id)
            except Exception:
                pass

        msg = wintypes.MSG()
        try:
            while not self._stop.is_set():
                ret = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
                if ret == 0 or ret == -1:
                    break

                if msg.message == self.WM_HOTKEY:
                    hotkey_id = msg.wParam
                    try:
                        if hotkey_id in (1, 3):
                            self._on_inc()
                        elif hotkey_id in (2, 4):
                            self._on_dec()
                        elif hotkey_id == 5:
                            self._on_toggle()
                    except Exception:
                        pass

                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
        finally:
            for hotkey_id in self._registered_ids:
                try:
                    user32.UnregisterHotKey(None, hotkey_id)
                except Exception:
                    pass
            self._registered_ids.clear()


class Settings:
    """User settings management"""
    
    def __init__(self):
        self.app_data = Path(os.getenv('APPDATA')) / 'dolphin'
        self.app_data.mkdir(exist_ok=True)
        self.settings_file = self.app_data / 'settings.json'
        self.load()
    
    def load(self):
        if self.settings_file.exists():
            try:
                with open(self.settings_file, 'r') as f:
                    data = json.load(f)
                    self.volume_cap = data.get('volume_cap', 0.2)
                    self.show_close_notifications = data.get('show_close_notifications', True)
                    self.run_at_startup = data.get('run_at_startup', False)
                    # New settings with defaults
                    self.attack_time = data.get('attack_time', 0.05)  # 50ms - sustained peak
                    self.release_time = data.get('release_time', 0.5)  # 500ms
                    self.hold_time = data.get('hold_time', 0.15)  # 150ms
                    self.user_cooldown = data.get('user_cooldown', 2.0)  # 2s
                    self.leeway_db = data.get('leeway_db', 3.0)  # 3dB leeway
                    self.dampening = data.get('dampening', 1.0)  # 1x (no dampening by default)
                    self.dampening_speed = data.get('dampening_speed', 0.0)  # 0s (instant) by default
                    # Stabilizer settings
                    self.stabilizer_enabled = data.get('stabilizer_enabled', False)
                    self.stabilizer_window = data.get('stabilizer_window', 5.0)  # 5s time window
                    self.stabilizer_threshold = data.get('stabilizer_threshold', 5)  # 5 changes trigger
                    self.stabilizer_max_leeway = data.get('stabilizer_max_leeway', 12.0)  # Max leeway increase
                    self.stabilizer_step = data.get('stabilizer_step', 1.0)  # dB step per adjustment
                    self.stabilizer_change_threshold = data.get('stabilizer_change_threshold', 0.05)  # 5% change
                    self.dark_mode = data.get('dark_mode', True)  # Dark mode by default
                    self.mini_mode = data.get('mini_mode', False)
            except (OSError, ValueError):
                self.set_defaults()
        else:
            self.set_defaults()
    
    def set_defaults(self):
        self.volume_cap = 0.2
        self.show_close_notifications = True
        self.run_at_startup = False
        self.attack_time = 0.05   # 50ms - wait for sustained peak
        self.release_time = 0.5   # 500ms release
        self.hold_time = 0.15     # 150ms hold
        self.user_cooldown = 2.0  # 2s user cooldown
        self.leeway_db = 3.0      # 3dB leeway above threshold
        self.dampening = 1.0      # 1x (no dampening by default)
        self.dampening_speed = 0.0  # 0s (instant) by default
        # Stabilizer settings
        self.stabilizer_enabled = False
        self.stabilizer_window = 5.0   # 5s time window
        self.stabilizer_threshold = 5  # 5 changes trigger adjustment
        self.stabilizer_max_leeway = 12.0  # Max leeway (dB)
        self.stabilizer_step = 1.0     # dB step per adjustment
        self.stabilizer_change_threshold = 0.05  # 5% volume change counts
        self.dark_mode = True  # Dark mode by default
        self.mini_mode = False
    
    def save(self):
        data = {
            'volume_cap': self.volume_cap,
            'show_close_notifications': self.show_close_notifications,
            'run_at_startup': self.run_at_startup,
            'attack_time': self.attack_time,
            'release_time': self.release_time,
            'hold_time': self.hold_time,
            'user_cooldown': self.user_cooldown,
            'leeway_db': self.leeway_db,
            'dampening': self.dampening,
            'dampening_speed': self.dampening_speed,
            'stabilizer_enabled': self.stabilizer_enabled,
            'stabilizer_window': self.stabilizer_window,
            'stabilizer_threshold': self.stabilizer_threshold,
            'stabilizer_max_leeway': self.stabilizer_max_leeway,
            'stabilizer_step': self.stabilizer_step,
            'stabilizer_change_threshold': self.stabilizer_change_threshold,
            'dark_mode': self.dark_mode,
            'mini_mode': self.mini_mode
        }
        with open(self.settings_file, 'w') as f:
            json.dump(data, f, indent=2)


class ToggleSwitch(tk.Canvas):
    """Custom toggle switch widget"""
    
    def __init__(self, parent, variable=None, command=None, text="", 
                 width=50, height=26, on_color="#4ade80", off_color="#505050", bg="#1e1e1e", fg="#ffffff"):
        
        # --- FIX START: Calculate dynamic width based on text length ---
        # We create a font object matching the one used in _draw ('Arial', 14)
        f = tkfont.Font(family='Arial', size=14)
        text_width = f.measure(text)
        
        # The total width is the switch (width) + padding (15) + the text length
        total_width = width + 15 + text_width
        # --- FIX END ---

        super().__init__(parent, width=total_width, height=max(height, 32), 
                         bg=bg, highlightthickness=0)
        
        self.width = width
        self.height = height
        self.on_color = on_color
        self.off_color = off_color
        self.fg_color = fg
        self.variable = variable
        self.command = command
        self.text = text
        
        # Draw the switch
        self._draw()
        
        # Bind click
        self.bind("<Button-1>", self._toggle)
        
        # Track variable changes
        if self.variable:
            self.variable.trace_add("write", lambda *args: self._draw())
    
    def _draw(self):
        self.delete("all")
        
        is_on = self.variable.get() if self.variable else False
        
        # Track background (rounded rectangle)
        radius = self.height // 2
        color = self.on_color if is_on else self.off_color
        
        # Draw rounded track
        self.create_oval(0, 0, self.height, self.height, fill=color, outline=color)
        self.create_oval(self.width - self.height, 0, self.width, self.height, fill=color, outline=color)
        self.create_rectangle(radius, 0, self.width - radius, self.height, fill=color, outline=color)
        
        # Draw thumb (circle)
        thumb_x = self.width - self.height + 3 if is_on else 3
        self.create_oval(thumb_x, 3, thumb_x + self.height - 6, self.height - 3, 
                         fill="white", outline="#ddd")
        
        # Draw label text
        self.create_text(self.width + 10, self.height // 2, 
                text=self.text, anchor=tk.W, font=('Arial', 14), fill=self.fg_color)
    
    def _toggle(self, event=None):
        if self.variable:
            self.variable.set(not self.variable.get())
        if self.command:
            self.command()


class AudioController:
    """Controls and monitors Windows system volume using cached interfaces"""
    
    def __init__(self):
        # Get audio device once and cache interfaces
        devices = AudioUtilities.GetSpeakers()
        
        # pycaw >=20251023 wraps IMMDevice in AudioDevice, so access _dev for COM interface
        imm_device = devices._dev if hasattr(devices, '_dev') else devices
        
        # Volume control interface
        vol_interface = imm_device.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
        self._volume_ctrl = cast(vol_interface, POINTER(IAudioEndpointVolume))
        
        # Audio meter interface for real peak levels
        meter_interface = imm_device.Activate(IAudioMeterInformation._iid_, CLSCTX_ALL, None)
        self._meter = cast(meter_interface, POINTER(IAudioMeterInformation))
        
        self._cached_volume = self._volume_ctrl.GetMasterVolumeLevelScalar()
        self._last_set_volume = self._cached_volume
        self.user_set_time = None
        self.user_set_volume = self._cached_volume
    
    def get_peak(self):
        """Get current audio peak level (0.0 to 1.0) - FAST"""
        try:
            return self._meter.GetPeakValue()
        except Exception:
            return 0.0
    
    def get_raw_peak(self):
        """Get audio peak normalized to 0-1 as if volume were 100%"""
        try:
            peak = self._meter.GetPeakValue()
            vol = self._cached_volume
            if vol > 0.01:
                # Normalize: if volume is 50%, peak of 0.25 means raw audio is 0.5
                return min(1.0, peak / vol)
            return peak
        except Exception:
            return 0.0
    
    def get_volume(self):
        """Get current system volume (0.0 to 1.0)"""
        try:
            self._cached_volume = self._volume_ctrl.GetMasterVolumeLevelScalar()
            return self._cached_volume
        except Exception:
            return self._cached_volume
    
    def set_volume(self, level):
        """Set system volume (0.0 to 1.0)"""
        try:
            level = max(0.0, min(1.0, level))
            self._volume_ctrl.SetMasterVolumeLevelScalar(level, None)
            self._last_set_volume = level
            self._cached_volume = level
        except Exception:
            pass
    
    def check_user_changed(self):
        """Check if user manually changed volume - returns True if changed"""
        current = self.get_volume()
        if abs(current - self._last_set_volume) > 0.01:
            self.user_set_time = time.time()
            self.user_set_volume = current
            self._last_set_volume = current
            return True
        return False


class VolumeLimiter:
    """Audio limiter with sustained peak detection"""
    
    def __init__(self, settings, audio_ctrl):
        self.settings = settings
        self.audio = audio_ctrl
        self.is_running = True
        
        # State
        self.volume_cap = settings.volume_cap
        self.original_volume = audio_ctrl.get_volume()  # Volume before limiting started
        self.current_peak = 0.0
        self.current_volume = self.original_volume
        
        # Limiter state
        self.is_limiting = False
        self.last_over_threshold_time = 0
        self.time_over_threshold = 0.0  # How long audio has been over threshold
        self.peak_start_time = 0.0      # When peak started
        
        # Timing parameters (loaded from settings)
        self.attack_time = settings.attack_time    # How long peak must sustain before limiting
        self.release_time = settings.release_time  # Release time in seconds
        self.hold_time = settings.hold_time        # Hold before release
        self.user_cooldown = settings.user_cooldown
        self.leeway_db = settings.leeway_db        # dB leeway above threshold
        self.dampening = settings.dampening        # Max dampening factor for sustained peaks
        self.dampening_speed = settings.dampening_speed  # Multiplier of attack_time to reach max dampening
        
        # Stabilizer mode
        self.stabilizer_enabled = settings.stabilizer_enabled
        self.stabilizer_window = settings.stabilizer_window
        self.stabilizer_threshold = settings.stabilizer_threshold
        self.stabilizer_max_leeway = settings.stabilizer_max_leeway
        self.stabilizer_step = settings.stabilizer_step
        self.stabilizer_change_threshold = settings.stabilizer_change_threshold
        self.base_leeway_db = settings.leeway_db  # Original leeway to restore to
        self.current_leeway_db = settings.leeway_db  # Dynamic leeway
        self.volume_change_times = []  # Timestamps of significant volume changes
        self.last_set_volume = None  # Track volume changes
        self.stabilizer_adjust_interval = 1.0  # Check every 1 second
        self.last_stabilizer_check = 0
        
        # Computed release rate (volume units per second)
        self._update_release_rate()
        
        # Threading
        self._stop = threading.Event()
        self._thread = None
        self._lock = threading.Lock()  # Protects shared state from GUI/hotkey threads
        
        # UI data (updated atomically)
        self.ui_peak = 0.0
        self.ui_volume = self.original_volume
    
    def _update_release_rate(self):
        """Calculate release rate from release time"""
        if self.release_time > 0:
            self.release_rate = 1.0 / self.release_time  # Full volume restore in release_time
        else:
            self.release_rate = 10.0  # Very fast
    
    def _track_volume_change(self, new_volume):
        """Track significant volume changes for stabilizer"""
        if not self.stabilizer_enabled:
            return
        
        if self.last_set_volume is not None:
            # Check if change is significant (configurable threshold)
            change = abs(new_volume - self.last_set_volume)
            if change > self.stabilizer_change_threshold:
                self.volume_change_times.append(time.time())
        
        self.last_set_volume = new_volume
    
    def _update_stabilizer(self, now):
        """Adjust leeway based on volume change frequency"""
        # Only check periodically
        if now - self.last_stabilizer_check < self.stabilizer_adjust_interval:
            return
        self.last_stabilizer_check = now
        
        # Remove old timestamps outside the window
        cutoff = now - self.stabilizer_window
        self.volume_change_times = [t for t in self.volume_change_times if t > cutoff]
        
        change_count = len(self.volume_change_times)
        
        if change_count >= self.stabilizer_threshold:
            # Too many changes - increase leeway to reduce limiting frequency
            new_leeway = self.current_leeway_db + self.stabilizer_step
            new_leeway = min(new_leeway, self.stabilizer_max_leeway)
            if new_leeway != self.current_leeway_db:
                self.current_leeway_db = new_leeway
                self.leeway_db = new_leeway
        elif change_count < self.stabilizer_threshold // 2:
            # Few changes - gradually restore to base leeway
            if self.current_leeway_db > self.base_leeway_db:
                new_leeway = self.current_leeway_db - (self.stabilizer_step * 0.5)
                new_leeway = max(new_leeway, self.base_leeway_db)
                self.current_leeway_db = new_leeway
                self.leeway_db = new_leeway
    
    def start(self):
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
    
    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=1)
    
    def _run(self):
        """Main limiter loop - runs at high frequency"""
        last_time = time.time()
        
        while not self._stop.is_set():
            try:
                if not self.is_running:
                    time.sleep(0.05)
                    self.time_over_threshold = 0.0  # Reset when disabled
                    continue
                
                now = time.time()
                dt = now - last_time
                last_time = now
                
                # Check for user volume changes - let user freely adjust
                if self.audio.check_user_changed():
                    new_user_vol = self.audio.user_set_volume
                    self.original_volume = new_user_vol
                    self.is_limiting = False
                    self.time_over_threshold = 0.0
                
                # Skip if in user cooldown
                if self.audio.user_set_time and (now - self.audio.user_set_time) < self.user_cooldown:
                    time.sleep(0.02)
                    continue
                
                # Get current peak level
                raw_peak = self.audio.get_raw_peak()
                self.current_peak = raw_peak
                self.current_volume = self.audio.get_volume()
                
                # Calculate what the output would be at original volume
                potential_output = raw_peak * self.original_volume
                
                # Read settings atomically (GUI/hotkeys can modify these)
                with self._lock:
                    volume_cap = self.volume_cap
                    leeway_db = self.leeway_db
                    attack_time = self.attack_time
                    dampening = self.dampening
                    dampening_speed = self.dampening_speed
                
                # Calculate effective threshold with leeway
                # leeway_db of 3 means allow ~1.41x (√2) over threshold before full limiting
                # Convert dB to linear: 10^(dB/20)
                leeway_factor = 10 ** (leeway_db / 20)
                soft_threshold = volume_cap * leeway_factor  # Upper limit (hard cap)
                
                if potential_output > volume_cap and raw_peak > 0.001:
                    # Audio is over threshold - accumulate time
                    self.time_over_threshold += dt
                    self.last_over_threshold_time = now
                    
                    if self.time_over_threshold >= attack_time:
                        # Sustained peak detected - start or continue limiting
                        if not self.is_limiting:
                            self.is_limiting = True
                        
                        # Calculate how far into the leeway zone we are (0 to 1)
                        # 0 = at volume_cap, 1 = at soft_threshold (max leeway)
                        if potential_output >= soft_threshold:
                            # Beyond leeway - full limiting
                            leeway_ratio = 1.0
                        else:
                            # In leeway zone - partial limiting
                            leeway_ratio = (potential_output - volume_cap) / (soft_threshold - volume_cap)
                        
                        # Reduction is proportional to how long over threshold
                        # sustained_factor goes from 1.0 at attack_time to dampening over dampening_speed seconds
                        time_since_attack = self.time_over_threshold - attack_time
                        if dampening_speed > 0.001:
                            # Ramp from 1.0 to dampening over dampening_speed seconds
                            ramp_progress = min(1.0, time_since_attack / dampening_speed)
                        else:
                            # Instant dampening
                            ramp_progress = 1.0
                        sustained_factor = 1.0 + (dampening - 1.0) * ramp_progress
                        sustained_factor = max(1.0, min(dampening, sustained_factor))
                        
                        # Target volume: softer reduction in leeway zone
                        # At volume_cap: minimal reduction, at soft_threshold: full reduction
                        # Prevent division by zero with minimum threshold
                        safe_peak = max(raw_peak, 0.01)
                        base_target = volume_cap / safe_peak
                        
                        # Blend between original volume and base_target based on leeway_ratio
                        target_volume = self.original_volume * (1 - leeway_ratio) + base_target * leeway_ratio
                        
                        # Apply sustained factor for longer peaks (divide = more reduction)
                        target_volume = target_volume / sustained_factor
                        target_volume = max(0.01, min(1.0, target_volume))
                        
                        self._track_volume_change(target_volume)
                        self.audio.set_volume(target_volume)
                else:
                    # Audio is under threshold
                    self.time_over_threshold = 0.0  # Reset accumulator
                    
                    if self.is_limiting:
                        time_since_loud = now - self.last_over_threshold_time
                        
                        if time_since_loud > self.hold_time:
                            # RELEASE: Gradually return to original volume
                            current = self.audio.get_volume()
                            target = self.original_volume
                            
                            if current < target - 0.005:
                                # Increase volume gradually
                                new_vol = current + self.release_rate * dt
                                new_vol = min(new_vol, target)
                                self._track_volume_change(new_vol)
                                self.audio.set_volume(new_vol)
                            else:
                                # Reached original volume, stop limiting
                                self._track_volume_change(target)
                                self.audio.set_volume(target)
                                self.is_limiting = False
                
                # Update UI data
                self.ui_peak = self.audio.get_raw_peak()
                self.ui_volume = self.audio.get_volume()
                
                # Stabilizer: adjust leeway based on volume change frequency
                if self.stabilizer_enabled:
                    self._update_stabilizer(now)
            except Exception:
                # Keep the thread alive; transient COM/tk errors shouldn't kill the loop.
                time.sleep(0.05)
            
            # Sleep for ~50Hz update rate
            time.sleep(0.02)
    
    def save_settings(self):
        with self._lock:
            self.settings.volume_cap = self.volume_cap
            self.settings.attack_time = self.attack_time
            self.settings.release_time = self.release_time
            self.settings.hold_time = self.hold_time
            self.settings.user_cooldown = self.user_cooldown
            self.settings.leeway_db = self.base_leeway_db  # Save base leeway, not dynamic
            self.settings.dampening = self.dampening
            self.settings.dampening_speed = self.dampening_speed
            self.settings.stabilizer_enabled = self.stabilizer_enabled
            self.settings.stabilizer_window = self.stabilizer_window
            self.settings.stabilizer_threshold = self.stabilizer_threshold
            self.settings.stabilizer_max_leeway = self.stabilizer_max_leeway
            self.settings.stabilizer_step = self.stabilizer_step
            self.settings.stabilizer_change_threshold = self.stabilizer_change_threshold
        self.settings.save()


class DolphinGUI:
    """Lightweight GUI"""

    VOLUME_CAP_MIN = 0.05
    VOLUME_CAP_MAX = 1.0
    VOLUME_CAP_HOTKEY_STEP = 0.01  # 1% per press

    MINI_MODE_SIZE = (520, 260)
    
    # Dark mode color scheme
    DARK_BG = '#1e1e1e'
    DARK_FG = '#ffffff'
    DARK_ACCENT = '#3d3d3d'
    DARK_SLIDER_BG = '#2d2d2d'
    DARK_SLIDER_FG = '#4a9eff'
    DARK_TROUGH = '#404040'
    DARK_GRAPH_BG = '#1a1a1a'
    
    # Light mode color scheme
    LIGHT_BG = '#f5f5f5'
    LIGHT_FG = '#1a1a1a'
    LIGHT_ACCENT = '#e0e0e0'
    LIGHT_SLIDER_BG = '#ffffff'
    LIGHT_SLIDER_FG = '#2196f3'
    LIGHT_TROUGH = '#cccccc'
    LIGHT_GRAPH_BG = '#ffffff'
    
    def __init__(self, root, start_minimized=False):
        self.root = root
        self.root.title("Dolphin")
        self.root.geometry("1200x900")
        self.root.resizable(False, False)
        
        # Initialize audio and limiter first (needed for dark_mode setting)
        self.settings = Settings()
        self.audio = AudioController()
        self.limiter = VolumeLimiter(self.settings, self.audio)
        
        # Track dark mode state
        self.is_dark_mode = self.settings.dark_mode
        
        # Store slider and toggle references for theme updates
        self.all_sliders = []
        self.all_toggles = []
        
        # Apply theme
        self._apply_theme()
        
        # Audio history for graph
        self.peak_history = [0.0] * 100
        
        # System tray
        self.tray_icon = None
        self._setup_tray()
        
        self._create_widgets()

        # Global hotkeys (Windows): Ctrl+Alt+Plus/Minus adjust Volume Cap, Ctrl+Alt+Y toggles enabled.
        self._hotkeys = None
        self._setup_hotkeys()
        
        self.root.protocol("WM_DELETE_WINDOW", self._on_closing)
        
        # Start limiter
        self.limiter.start()
        
        # Position window
        self._position_window()
        
        # Start minimized to tray if requested
        self._exiting = False
        if start_minimized and self.tray_icon:
            self.root.withdraw()
        
        # Start UI updates (slower rate)
        self._schedule_ui_update()

        # Apply persisted mini mode after widgets exist
        self._normal_geometry = None
        self._apply_mini_mode(bool(getattr(self.settings, 'mini_mode', False)), remember_geometry=True)

    def _toggle_mini_mode(self):
        enabled = bool(self.mini_mode_var.get())
        self.settings.mini_mode = enabled
        self.settings.save()
        self._apply_mini_mode(enabled, remember_geometry=True)

    def _apply_mini_mode(self, enabled, remember_geometry):
        if enabled:
            if remember_geometry and not self._normal_geometry:
                # Keep full geometry including position
                self._normal_geometry = self.root.geometry()

            try:
                self.root.attributes('-topmost', True)
            except Exception:
                pass

            if hasattr(self, 'cap_frame'):
                self.cap_frame.pack_forget()
            if hasattr(self, 'columns_frame'):
                self.columns_frame.pack_forget()
            if hasattr(self, 'graph_frame'):
                self.graph_frame.pack_forget()

            # Swap detailed level labels for abbreviated A/S/T when in mini mode.
            if hasattr(self, 'audio_label_title'):
                self.audio_label_title.pack_forget()
            if hasattr(self, 'peak_label'):
                self.peak_label.pack_forget()
            if hasattr(self, 'system_label_title'):
                self.system_label_title.pack_forget()
            if hasattr(self, 'vol_label'):
                self.vol_label.pack_forget()

            # Mini mode UI: hide extra buttons/toggles (leave Enable/Disable + Mini Mode).
            if hasattr(self, 'reset_btn'):
                self.reset_btn.pack_forget()
            if hasattr(self, 'dark_mode_toggle'):
                self.dark_mode_toggle.pack_forget()
            if hasattr(self, 'startup_toggle'):
                self.startup_toggle.pack_forget()
            if hasattr(self, 'minimize_toggle'):
                self.minimize_toggle.pack_forget()

            # Ensure mini mode toggle is visible.
            if hasattr(self, 'mini_mode_toggle') and not self.mini_mode_toggle.winfo_manager():
                self.mini_mode_toggle.pack(side=tk.LEFT, padx=0)

            if hasattr(self, 'mini_info_frame'):
                self._update_mini_threshold_label()
                if not self.mini_info_frame.winfo_manager():
                    self.mini_info_frame.pack(side=tk.LEFT, padx=0)

            w, h = self.MINI_MODE_SIZE
            try:
                x = self.root.winfo_x()
                y = self.root.winfo_y()
                self.root.geometry(f"{w}x{h}+{x}+{y}")
            except Exception:
                self.root.geometry(f"{w}x{h}")
        else:
            try:
                self.root.attributes('-topmost', False)
            except Exception:
                pass

            # Restore packed widgets in a deterministic order.
            # This avoids fragile pack(before/after=...) usage that can fail depending on state.
            if hasattr(self, 'header_frame'):
                self.header_frame.pack_forget()
            if hasattr(self, 'cap_frame'):
                self.cap_frame.pack_forget()
            if hasattr(self, 'columns_frame'):
                self.columns_frame.pack_forget()
            if hasattr(self, 'levels_frame'):
                self.levels_frame.pack_forget()
            if hasattr(self, 'graph_frame'):
                self.graph_frame.pack_forget()
            if hasattr(self, 'bottom_frame'):
                self.bottom_frame.pack_forget()

            if hasattr(self, 'header_frame'):
                self.header_frame.pack(fill=tk.X, pady=(0, 12))
            if hasattr(self, 'cap_frame'):
                self.cap_frame.pack(fill=tk.X, pady=0)
            if hasattr(self, 'columns_frame'):
                self.columns_frame.pack(fill=tk.X, pady=10)
            if hasattr(self, 'levels_frame'):
                self.levels_frame.pack(fill=tk.X, pady=10)
            if hasattr(self, 'graph_frame'):
                self.graph_frame.pack(fill=tk.BOTH, expand=True, pady=10)
            if hasattr(self, 'bottom_frame'):
                self.bottom_frame.pack(fill=tk.X, pady=5)

            # Restore detailed labels, hide mini info bar.
            if hasattr(self, 'mini_info_frame'):
                self.mini_info_frame.pack_forget()
            if hasattr(self, 'audio_label_title'):
                self.audio_label_title.pack(side=tk.LEFT)
            if hasattr(self, 'peak_label'):
                self.peak_label.pack(side=tk.LEFT, padx=(8, 40))
            if hasattr(self, 'system_label_title'):
                self.system_label_title.pack(side=tk.LEFT)
            if hasattr(self, 'vol_label'):
                self.vol_label.pack(side=tk.LEFT, padx=8)

            # Restore bottom-row widgets in order.
            if hasattr(self, 'btn_frame'):
                for widget in (
                    getattr(self, 'toggle_btn', None),
                    getattr(self, 'reset_btn', None),
                    getattr(self, 'dark_mode_toggle', None),
                    getattr(self, 'startup_toggle', None),
                    getattr(self, 'minimize_toggle', None),
                    getattr(self, 'mini_mode_toggle', None),
                ):
                    if widget is not None:
                        try:
                            widget.pack_forget()
                        except Exception:
                            pass

                if hasattr(self, 'toggle_btn'):
                    self.toggle_btn.pack(side=tk.LEFT, padx=5)
                if hasattr(self, 'reset_btn'):
                    self.reset_btn.pack(side=tk.LEFT, padx=5)
                if hasattr(self, 'dark_mode_toggle'):
                    self.dark_mode_toggle.pack(side=tk.LEFT, padx=30)
                if hasattr(self, 'startup_toggle'):
                    self.startup_toggle.pack(side=tk.LEFT, padx=0)
                if hasattr(self, 'minimize_toggle'):
                    self.minimize_toggle.pack(side=tk.LEFT, padx=30)
                if hasattr(self, 'mini_mode_toggle'):
                    self.mini_mode_toggle.pack(side=tk.LEFT, padx=0)

            if self._normal_geometry:
                try:
                    self.root.geometry(self._normal_geometry)
                except Exception:
                    pass

    def _setup_hotkeys(self):
        if sys.platform != "win32":
            return

        def inc():
            self.root.after(0, lambda: self._adjust_volume_cap(self.VOLUME_CAP_HOTKEY_STEP))

        def dec():
            self.root.after(0, lambda: self._adjust_volume_cap(-self.VOLUME_CAP_HOTKEY_STEP))

        def toggle():
            self.root.after(0, self._toggle)

        self._hotkeys = GlobalHotkeyListener(inc, dec, toggle)
        self._hotkeys.start()

    def _adjust_volume_cap(self, delta):
        with self.limiter._lock:
            new_cap = float(self.limiter.volume_cap) + float(delta)
            new_cap = max(self.VOLUME_CAP_MIN, min(self.VOLUME_CAP_MAX, new_cap))
            self.limiter.volume_cap = new_cap

        if hasattr(self, 'slider_volume_cap'):
            slider, var, label, unit, mult = self.slider_volume_cap
            var.set(new_cap)
            if unit == "%":
                label.config(text=f"{int(new_cap * mult)}%")

        self._update_mini_threshold_label()
    
    def _apply_theme(self):
        """Apply dark or light theme based on is_dark_mode"""
        if self.is_dark_mode:
            bg = self.DARK_BG
            fg = self.DARK_FG
            accent = self.DARK_ACCENT
            trough = self.DARK_TROUGH
            slider_fg = self.DARK_SLIDER_FG
            graph_bg = self.DARK_GRAPH_BG
            toggle_off = '#505050'
            toggle_label = '#ffffff'
        else:
            bg = self.LIGHT_BG
            fg = self.LIGHT_FG
            accent = self.LIGHT_ACCENT
            trough = self.LIGHT_TROUGH
            slider_fg = self.LIGHT_SLIDER_FG
            graph_bg = self.LIGHT_GRAPH_BG
            toggle_off = '#cccccc'
            toggle_label = '#1a1a1a'
        
        # Store current theme colors for use by other methods
        self.theme_bg = bg
        self.theme_fg = fg
        self.theme_accent = accent
        self.theme_trough = trough
        self.theme_slider_fg = slider_fg
        self.theme_graph_bg = graph_bg
        self.theme_toggle_off = toggle_off
        self.theme_toggle_label = toggle_label
        
        self.root.configure(bg=bg)
        
        style = ttk.Style()
        style.theme_use('clam')
        
        style.configure('.', background=bg, foreground=fg, fieldbackground=accent, troughcolor=trough)
        style.configure('TFrame', background=bg)
        style.configure('TLabel', background=bg, foreground=fg)
        style.configure('TLabelframe', background=bg, foreground=fg)
        style.configure('TLabelframe.Label', background=bg, foreground=fg)
        style.configure('Big.TLabelframe', background=bg, foreground=fg)
        style.configure('Big.TLabelframe.Label', font=('Arial', 16), background=bg, foreground=fg)
        style.configure('TButton', background=accent, foreground=fg)
        style.map('TButton', background=[('active', '#505050' if self.is_dark_mode else '#d0d0d0')])
    
    def _toggle_dark_mode(self):
        """Toggle between dark and light mode"""
        self.is_dark_mode = not self.is_dark_mode
        self.settings.dark_mode = self.is_dark_mode
        self.settings.save()
        self._apply_theme()
        
        # Update graph canvas background
        if hasattr(self, 'graph_canvas'):
            self.graph_canvas.configure(bg=self.theme_graph_bg)
        
        # Update all sliders with new theme colors
        for slider in self.all_sliders:
            slider.configure(
                bg=self.theme_bg, fg=self.theme_fg,
                troughcolor=self.theme_trough, activebackground=self.theme_slider_fg
            )
        
        # Update all toggles with new theme colors
        for toggle in self.all_toggles:
            toggle.configure(bg=self.theme_bg)
            toggle.off_color = self.theme_toggle_off
            toggle.fg_color = self.theme_fg
            toggle._draw()
        
        self._draw_graph()
    
    def _create_widgets(self):
        self.main_frame = ttk.Frame(self.root, padding="15")
        self.main_frame.pack(fill=tk.BOTH, expand=True)
        
        # Title and Status row
        self.header_frame = ttk.Frame(self.main_frame)
        self.header_frame.pack(fill=tk.X, pady=(0, 12))
        
        ttk.Label(self.header_frame, text="Dolphin", font=('Arial', 28, 'bold')).pack(side=tk.LEFT)
        
        status_frame = ttk.Frame(self.header_frame)
        status_frame.pack(side=tk.RIGHT)
        ttk.Label(status_frame, text="Status:", font=('Arial', 16)).pack(side=tk.LEFT)
        self.status_label = ttk.Label(status_frame, text="Running", foreground="#4ade80", font=('Arial', 16, 'bold'))
        self.status_label.pack(side=tk.LEFT, padx=5)
        
        # === Volume Cap Slider ===
        self.cap_frame = ttk.Frame(self.main_frame)
        self.cap_frame.pack(fill=tk.X, pady=0)
        self._create_slider(self.cap_frame, "Volume Cap:", 0.05, 1.0, 0.01,
                   self.limiter.volume_cap, self._on_cap_change, "%")
        
        # === Side-by-side container for Advanced Settings and Stabilizer ===
        self.columns_frame = ttk.Frame(self.main_frame)
        self.columns_frame.pack(fill=tk.X, pady=10)
        
        # Left column: Advanced Settings
        adv_frame = ttk.LabelFrame(self.columns_frame, text="Advanced Settings", padding="10", style='Big.TLabelframe')
        adv_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 6))
        
        # Attack Time (1ms to 100ms)
        self._create_slider_compact(adv_frame, "Attack:", 0.001, 0.1, 0.001,
                           self.limiter.attack_time, self._on_attack_change, "ms", 1000)
        
        # Release Time (100ms to 3s)
        self._create_slider_compact(adv_frame, "Release:", 0.1, 3.0, 0.05,
                           self.limiter.release_time, self._on_release_change, "ms", 1000)
        
        # Hold Time (0 to 500ms)
        self._create_slider_compact(adv_frame, "Hold:", 0.0, 0.5, 0.01,
                           self.limiter.hold_time, self._on_hold_change, "ms", 1000)
        
        # User Cooldown (0.5s to 5s)
        self._create_slider_compact(adv_frame, "Cooldown:", 0.5, 5.0, 0.1,
                           self.limiter.user_cooldown, self._on_cooldown_change, "s", 1)
        
        # Leeway (0 to 12 dB)
        self._create_slider_compact(adv_frame, "Leeway:", 0.0, 12.0, 0.5,
                           self.limiter.leeway_db, self._on_leeway_change, "dB", 1)
        
        # Dampening (1x to 5x)
        self._create_slider_compact(adv_frame, "Dampening:", 1.0, 5.0, 0.1,
                           self.limiter.dampening, self._on_dampening_change, "x", 1)
        
        # Dampening Speed (0 to 2 seconds)
        self._create_slider_compact(adv_frame, "Damp Spd:", 0.0, 2.0, 0.05,
                           self.limiter.dampening_speed, self._on_dampening_speed_change, "s", 1)
        
        # Right column: Stabilizer Settings
        stab_frame = ttk.LabelFrame(self.columns_frame, text="Stabilizer", padding="10", style='Big.TLabelframe')
        stab_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(6, 0))
        
        # Stabilizer enable toggle
        self.stabilizer_var = tk.BooleanVar(value=self.limiter.stabilizer_enabled)
        stabilizer_toggle = ToggleSwitch(
            stab_frame, text="Enable",
            variable=self.stabilizer_var, command=self._on_stabilizer_change,
            width=55, height=28,
            bg=self.theme_bg, off_color=self.theme_toggle_off, fg=self.theme_fg
        )
        stabilizer_toggle.pack(anchor=tk.W, pady=6)
        self.all_toggles.append(stabilizer_toggle)
        
        # Stabilizer window (1s to 30s)
        self._create_slider_compact(stab_frame, "Window:", 1.0, 30.0, 1.0,
                           self.limiter.stabilizer_window, self._on_stab_window_change, "s", 1)
        
        # Stabilizer threshold (2 to 20 changes)
        self._create_slider_compact(stab_frame, "Count:", 2, 20, 1,
                           self.limiter.stabilizer_threshold, self._on_stab_threshold_change, "chg", 1)
        
        # Stabilizer change threshold (1% to 20%)
        self._create_slider_compact(stab_frame, "Change:", 0.01, 0.20, 0.01,
                           self.limiter.stabilizer_change_threshold, self._on_stab_change_threshold, "%", 100)
        
        # Stabilizer max leeway (base to 20 dB)
        self._create_slider_compact(stab_frame, "Max:", 3.0, 20.0, 0.5,
                           self.limiter.stabilizer_max_leeway, self._on_stab_max_leeway_change, "dB", 1)
        
        # Stabilizer step (0.5 to 3 dB per adjustment)
        self._create_slider_compact(stab_frame, "Step:", 0.5, 3.0, 0.25,
                           self.limiter.stabilizer_step, self._on_stab_step_change, "dB", 1)
        
        # Current dynamic leeway display
        stab_status_frame = ttk.Frame(stab_frame)
        stab_status_frame.pack(fill=tk.X, pady=6)
        ttk.Label(stab_status_frame, text="Current:", font=('Arial', 15)).pack(side=tk.LEFT)
        self.dynamic_leeway_label = ttk.Label(stab_status_frame, text=f"{self.limiter.current_leeway_db:.1f}dB", foreground="#4a9eff", font=('Arial', 15, 'bold'))
        self.dynamic_leeway_label.pack(side=tk.LEFT, padx=5)

        # Audio level display
        self.levels_frame = ttk.Frame(self.main_frame)
        self.levels_frame.pack(fill=tk.X, pady=10)
        
        self.audio_label_title = ttk.Label(self.levels_frame, text="Audio Level:", font=('Arial', 16))
        self.audio_label_title.pack(side=tk.LEFT)
        self.peak_label = ttk.Label(self.levels_frame, text="0%", width=6, font=('Arial', 16, 'bold'))
        self.peak_label.pack(side=tk.LEFT, padx=(8, 40))
        
        self.system_label_title = ttk.Label(self.levels_frame, text="System Vol:", font=('Arial', 16))
        self.system_label_title.pack(side=tk.LEFT)
        self.vol_label = ttk.Label(self.levels_frame, text="0%", width=6, font=('Arial', 16, 'bold'))
        self.vol_label.pack(side=tk.LEFT, padx=8)

        # Mini-mode info bar (abbreviated A/S/T), displayed in place of the level titles when mini mode is on.
        self.mini_info_frame = ttk.Frame(self.levels_frame)
        self.mini_audio_label = ttk.Label(self.mini_info_frame, text="A: 0%", font=('Arial', 16, 'bold'))
        self.mini_system_label = ttk.Label(self.mini_info_frame, text="S: 0%", font=('Arial', 16, 'bold'))
        self.mini_mode_threshold_label = ttk.Label(self.mini_info_frame, text="T: 0%", font=('Arial', 16, 'bold'))
        for label in (self.mini_audio_label, self.mini_system_label, self.mini_mode_threshold_label):
            label.pack(side=tk.LEFT, padx=8)
        self.mini_info_frame.pack_forget()
        
        # Audio level graph - larger now
        self.graph_frame = ttk.Frame(self.main_frame)
        self.graph_frame.pack(fill=tk.BOTH, expand=True, pady=10)
        
        self.graph_canvas = tk.Canvas(self.graph_frame, width=1050, height=140, bg=self.theme_graph_bg, 
                                      highlightthickness=1, highlightbackground='#333')
        self.graph_canvas.pack(fill=tk.BOTH, expand=True)
        
        # Bottom buttons and toggles frame
        self.bottom_frame = ttk.Frame(self.main_frame)
        self.bottom_frame.pack(fill=tk.X, pady=5)
        
        # Left side: buttons
        self.btn_frame = ttk.Frame(self.bottom_frame)
        self.btn_frame.pack(side=tk.LEFT)
        
        self.toggle_btn = ttk.Button(self.btn_frame, text="Disable", command=self._toggle)
        self.toggle_btn.pack(side=tk.LEFT, padx=3)
        
        self.reset_btn = ttk.Button(self.btn_frame, text="Reset", command=self._reset_defaults)
        self.reset_btn.pack(side=tk.LEFT, padx=3)
        
        # All toggles in a single frame, packed left together
        self.dark_mode_var = tk.BooleanVar(value=self.is_dark_mode)
        self.dark_mode_toggle = ToggleSwitch(
            self.btn_frame, text="Dark Mode",
            variable=self.dark_mode_var, command=self._toggle_dark_mode,
            width=55, height=28,
            bg=self.theme_bg, off_color=self.theme_toggle_off, fg=self.theme_fg
        )
        self.dark_mode_toggle.pack(side=tk.LEFT, padx=10)
        self.all_toggles.append(self.dark_mode_toggle)
        
        self.startup_var = tk.BooleanVar(value=self.settings.run_at_startup)
        self.startup_toggle = ToggleSwitch(
            self.btn_frame, text="Run at startup",
            variable=self.startup_var, command=self._on_startup_change,
            width=55, height=28,
            bg=self.theme_bg, off_color=self.theme_toggle_off, fg=self.theme_fg
        )
        self.startup_toggle.pack(side=tk.LEFT, padx=0)
        self.all_toggles.append(self.startup_toggle)
        
        self.minimize_var = tk.BooleanVar(value=self.settings.show_close_notifications)
        self.minimize_toggle = ToggleSwitch(
            self.btn_frame, text="Minimize to tray",
            variable=self.minimize_var, command=self._on_minimize_change,
            width=55, height=28,
            bg=self.theme_bg, off_color=self.theme_toggle_off, fg=self.theme_fg
        )
        self.minimize_toggle.pack(side=tk.LEFT, padx=10)
        self.all_toggles.append(self.minimize_toggle)

        self.mini_mode_var = tk.BooleanVar(value=bool(getattr(self.settings, 'mini_mode', False)))
        self.mini_mode_toggle = ToggleSwitch(
            self.btn_frame, text="Mini Mode",
            variable=self.mini_mode_var, command=self._toggle_mini_mode,
            width=55, height=28,
            bg=self.theme_bg, off_color=self.theme_toggle_off, fg=self.theme_fg
        )
        self.mini_mode_toggle.pack(side=tk.LEFT, padx=0)
        self.all_toggles.append(self.mini_mode_toggle)
    
    def _create_slider_compact(self, parent, label_text, from_, to, resolution, initial, callback, unit, multiplier=100):
        """Create a compact labeled slider with value display"""
        frame = ttk.Frame(parent)
        frame.pack(fill=tk.X, pady=3)
        
        ttk.Label(frame, text=label_text, width=9, font=('Arial', 15)).pack(side=tk.LEFT)
        
        # Format value based on unit
        val_text = self._format_value(initial, unit, multiplier)
        
        val_label = ttk.Label(frame, text=val_text, width=7, font=('Arial', 15))
        val_label.pack(side=tk.RIGHT)
        
        var = tk.DoubleVar(value=initial)
        slider = tk.Scale(
            frame, from_=from_, to=to,
            variable=var, orient=tk.HORIZONTAL,
            resolution=resolution, showvalue=False, length=170,
            sliderlength=30, width=22,
            bg=self.theme_bg, fg=self.theme_fg,
            troughcolor=self.theme_trough, activebackground=self.theme_slider_fg,
            highlightthickness=0,
            command=lambda v, cb=callback, lbl=val_label, u=unit, m=multiplier: 
                self._slider_callback(v, cb, lbl, u, m)
        )
        slider.pack(side=tk.RIGHT, padx=3)
        
        # Store reference for theme updates
        self.all_sliders.append(slider)
        
        # Store reference for resetting
        setattr(self, f"slider_{label_text.replace(':', '').replace(' ', '_').lower()}", 
                (slider, var, val_label, unit, multiplier))
        
        return val_label
    
    def _format_value(self, v, unit, multiplier):
        """Format a value based on unit type"""
        if unit == "%":
            return f"{int(v * multiplier)}%"
        elif unit == "ms":
            return f"{int(v * multiplier)}ms"
        elif unit == "dB":
            return f"{v:.1f}dB"
        elif unit == "x":
            return f"{v:.1f}x"
        elif unit == "chg":
            return f"{int(v)}"
        else:
            return f"{v:.1f}s"
    
    def _create_slider(self, parent, label_text, from_, to, resolution, initial, callback, unit, multiplier=100):
        """Create a labeled slider with value display"""
        frame = ttk.Frame(parent)
        frame.pack(fill=tk.X, pady=6)
        
        ttk.Label(frame, text=label_text, width=12, font=('Arial', 16)).pack(side=tk.LEFT)
        
        # Format value based on unit
        if unit == "%":
            val_text = f"{int(initial * multiplier)}%"
        elif unit == "ms":
            val_text = f"{int(initial * multiplier)}ms"
        elif unit == "dB":
            val_text = f"{initial:.1f}dB"
        elif unit == "x":
            val_text = f"{initial:.1f}x"
        elif unit == "chg":
            val_text = f"{int(initial)} chg"
        else:
            val_text = f"{initial:.1f}s"
        
        val_label = ttk.Label(frame, text=val_text, width=8, font=('Arial', 16))
        val_label.pack(side=tk.RIGHT)
        
        var = tk.DoubleVar(value=initial)
        slider = tk.Scale(
            frame, from_=from_, to=to,
            variable=var, orient=tk.HORIZONTAL,
            resolution=resolution, showvalue=False, length=320,
            sliderlength=36, width=26,
            bg=self.theme_bg, fg=self.theme_fg,
            troughcolor=self.theme_trough, activebackground=self.theme_slider_fg,
            highlightthickness=0,
            command=lambda v, cb=callback, lbl=val_label, u=unit, m=multiplier: 
                self._slider_callback(v, cb, lbl, u, m)
        )
        slider.pack(side=tk.RIGHT, padx=8)
        
        # Store reference for theme updates
        self.all_sliders.append(slider)
        
        # Store reference for resetting
        setattr(self, f"slider_{label_text.replace(':', '').replace(' ', '_').lower()}", 
                (slider, var, val_label, unit, multiplier))
        
        return val_label
    
    def _slider_callback(self, val, callback, label, unit, multiplier):
        """Generic slider callback"""
        v = float(val)
        callback(v)
        if unit == "%":
            label.config(text=f"{int(v * multiplier)}%")
        elif unit == "ms":
            label.config(text=f"{int(v * multiplier)}ms")
        elif unit == "dB":
            label.config(text=f"{v:.1f}dB")
        elif unit == "x":
            label.config(text=f"{v:.1f}x")
        elif unit == "chg":
            label.config(text=f"{int(v)} chg")
        else:
            label.config(text=f"{v:.1f}s")
    
    def _on_cap_change(self, val):
        with self.limiter._lock:
            self.limiter.volume_cap = float(val)

        self._update_mini_threshold_label()
    
    def _on_attack_change(self, val):
        with self.limiter._lock:
            self.limiter.attack_time = float(val)
    
    def _on_release_change(self, val):
        with self.limiter._lock:
            self.limiter.release_time = float(val)
            self.limiter._update_release_rate()
    
    def _on_hold_change(self, val):
        with self.limiter._lock:
            self.limiter.hold_time = float(val)
    
    def _on_cooldown_change(self, val):
        with self.limiter._lock:
            self.limiter.user_cooldown = float(val)
    
    def _on_leeway_change(self, val):
        with self.limiter._lock:
            self.limiter.leeway_db = float(val)
            self.limiter.base_leeway_db = float(val)  # Update base for stabilizer
            self.limiter.current_leeway_db = float(val)  # Reset current
    
    def _on_dampening_change(self, val):
        with self.limiter._lock:
            self.limiter.dampening = float(val)
    
    def _on_dampening_speed_change(self, val):
        with self.limiter._lock:
            self.limiter.dampening_speed = float(val)
    
    def _on_stabilizer_change(self):
        with self.limiter._lock:
            enabled = self.stabilizer_var.get()
            self.limiter.stabilizer_enabled = enabled
            # Reset dynamic leeway to base when disabled
            if not enabled:
                self.limiter.current_leeway_db = self.limiter.base_leeway_db
                self.limiter.leeway_db = self.limiter.base_leeway_db
                self.limiter.volume_change_times.clear()
    
    def _on_stab_window_change(self, val):
        with self.limiter._lock:
            self.limiter.stabilizer_window = float(val)
    
    def _on_stab_threshold_change(self, val):
        with self.limiter._lock:
            self.limiter.stabilizer_threshold = int(float(val))
    
    def _on_stab_max_leeway_change(self, val):
        with self.limiter._lock:
            self.limiter.stabilizer_max_leeway = float(val)
    
    def _on_stab_step_change(self, val):
        with self.limiter._lock:
            self.limiter.stabilizer_step = float(val)
    
    def _on_stab_change_threshold(self, val):
        with self.limiter._lock:
            self.limiter.stabilizer_change_threshold = float(val)
    
    def _update_slider_displays(self):
        """Update all slider positions and labels to match current limiter values"""
        sliders = [
            ('slider_volume_cap', self.limiter.volume_cap),
            ('slider_attack', self.limiter.attack_time),
            ('slider_release', self.limiter.release_time),
            ('slider_hold', self.limiter.hold_time),
            ('slider_cooldown', self.limiter.user_cooldown),
            ('slider_leeway', self.limiter.leeway_db),
            ('slider_dampening', self.limiter.dampening),
            ('slider_damp_speed', self.limiter.dampening_speed),
        ]
        for attr, value in sliders:
            if hasattr(self, attr):
                slider, var, label, unit, mult = getattr(self, attr)
                var.set(value)
                if unit == "%":
                    label.config(text=f"{int(value * mult)}%")
                elif unit == "ms":
                    label.config(text=f"{int(value * mult)}ms")
                elif unit == "dB":
                    label.config(text=f"{value:.1f}dB")
                elif unit == "x":
                    label.config(text=f"{value:.1f}x")
                elif unit == "chg":
                    label.config(text=f"{int(value)} chg")
                else:
                    label.config(text=f"{value:.1f}s")

            self._update_mini_threshold_label()

    def _update_mini_threshold_label(self):
        if hasattr(self, 'mini_mode_threshold_label'):
            pct = int(self.limiter.volume_cap * 100)
            self.mini_mode_threshold_label.config(text=f"T: {pct}%")
    
    def _reset_defaults(self):
        """Reset advanced settings to defaults (preserves volume cap)"""
        with self.limiter._lock:
            self.limiter.attack_time = 0.05  # 50ms
            self.limiter.release_time = 0.5
            self.limiter.hold_time = 0.15
            self.limiter.user_cooldown = 2.0
            self.limiter.leeway_db = 3.0     # 3dB leeway
            self.limiter.base_leeway_db = 3.0
            self.limiter.current_leeway_db = 3.0
            self.limiter.dampening = 1.0     # 1x (no dampening by default)
            self.limiter.dampening_speed = 0.0  # 0s (instant) by default
            self.limiter._update_release_rate()
        
        # Reset stabilizer
        self.limiter.stabilizer_enabled = False
        self.limiter.stabilizer_window = 5.0
        self.limiter.stabilizer_threshold = 5
        self.limiter.stabilizer_max_leeway = 12.0
        self.limiter.stabilizer_step = 1.0
        self.limiter.stabilizer_change_threshold = 0.05
        self.limiter.volume_change_times.clear()
        self.stabilizer_var.set(False)
        
        self._update_slider_displays()
    
    def _toggle(self):
        with self.limiter._lock:
            self.limiter.is_running = not self.limiter.is_running
            is_running = self.limiter.is_running
        
        if is_running:
            self.toggle_btn.config(text="Disable")
            self.status_label.config(text="Running", foreground="green")
        else:
            self.toggle_btn.config(text="Enable")
            self.status_label.config(text="Stopped", foreground="red")
    
    def _on_startup_change(self):
        enabled = self.startup_var.get()
        self.settings.run_at_startup = enabled
        self._update_startup_registry()
    
    def _update_startup_registry(self):
        """Update Windows startup registry with correct flags"""
        key_path = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Run"
        try:
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE)
            if self.settings.run_at_startup:
                # Add --minimized flag if minimize to tray is also enabled
                exe_path = f'"{sys.executable}"'
                if self.settings.show_close_notifications and self.tray_icon:
                    exe_path = f"{exe_path} --minimized"
                winreg.SetValueEx(key, "Dolphin", 0, winreg.REG_SZ, exe_path)
            else:
                try:
                    winreg.DeleteValue(key, "Dolphin")
                except FileNotFoundError:
                    pass
            winreg.CloseKey(key)
        except OSError:
            pass
    
    def _on_minimize_change(self):
        """Handle minimize to tray checkbox change"""
        requested = bool(self.minimize_var.get())
        if requested and not self.tray_icon:
            # Tray not available; prevent the app from becoming inaccessible.
            self.minimize_var.set(False)
            requested = False
        self.settings.show_close_notifications = requested
        self.settings.save()
        # Update registry if startup is enabled (to add/remove --minimized flag)
        if self.settings.run_at_startup:
            self._update_startup_registry()
    
    def _schedule_ui_update(self):
        """Update UI at 10Hz - much less CPU intensive"""
        if self._exiting:
            return

        try:
            peak = self.limiter.ui_peak  # Raw audio level (0-1)
            vol = self.limiter.ui_volume

            # Show raw peak as percentage (this is the audio level relative to system volume)
            peak_pct = int(peak * 100)
            vol_pct = int(vol * 100)
            self.peak_label.config(text=f"{peak_pct}%")
            self.vol_label.config(text=f"{vol_pct}%")

            if hasattr(self, 'mini_audio_label'):
                self.mini_audio_label.config(text=f"A: {peak_pct}%")
            if hasattr(self, 'mini_system_label'):
                self.mini_system_label.config(text=f"S: {vol_pct}%")

            # Update dynamic leeway display for stabilizer
            current_leeway = self.limiter.current_leeway_db
            base_leeway = self.limiter.base_leeway_db
            if current_leeway > base_leeway:
                self.dynamic_leeway_label.config(
                    text=f"{current_leeway:.1f}dB (+{current_leeway - base_leeway:.1f})",
                    foreground="#ffa500",
                )
            else:
                self.dynamic_leeway_label.config(text=f"{current_leeway:.1f}dB", foreground="#4a9eff")

            # Update graph with raw peak level
            self.peak_history.pop(0)
            self.peak_history.append(peak)
            self._draw_graph()
        except Exception:
            # Window shutting down or transient COM/audio errors - don't break the loop
            if self._exiting:
                return
        self.root.after(100, self._schedule_ui_update)
    
    def _draw_graph(self):
        """Draw the audio level graph with threshold and dynamic leeway indicators"""
        canvas = self.graph_canvas
        canvas.delete("all")
        
        # Get actual canvas size
        w = canvas.winfo_width()
        h = canvas.winfo_height()
        if w < 10 or h < 10:  # Canvas not yet realized
            w = 650
            h = 100
        
        num_points = len(self.peak_history)
        
        # Calculate threshold as peak level
        # Limiting starts when peak * original_volume > volume_cap
        # So threshold peak = volume_cap / original_volume
        original_vol = self.limiter.original_volume if self.limiter.original_volume > 0.001 else 1.0
        threshold = min(1.0, self.limiter.volume_cap / original_vol)
        threshold_y = h - (threshold * h)
        
        # Calculate dynamic leeway threshold (stabilizer)
        # Current leeway in dB -> linear factor: 10^(dB/20)
        leeway_factor = 10 ** (self.limiter.current_leeway_db / 20)
        dynamic_threshold = min(1.0, threshold * leeway_factor)
        dynamic_threshold_y = h - (dynamic_threshold * h)
        
        # Draw dynamic leeway zone (shaded area between threshold and dynamic threshold)
        if self.limiter.stabilizer_enabled and self.limiter.current_leeway_db > self.limiter.base_leeway_db:
            canvas.create_rectangle(0, dynamic_threshold_y, w, threshold_y, 
                                   fill='#4a3000' if self.is_dark_mode else '#ffe4b3', outline='')
        
        # Draw threshold line (red, solid)
        canvas.create_line(0, threshold_y, w, threshold_y, fill='#ff4444', width=2)
        
        # Draw dynamic leeway line (orange, dashed) if stabilizer is active
        if self.limiter.stabilizer_enabled:
            canvas.create_line(0, dynamic_threshold_y, w, dynamic_threshold_y, 
                              fill='#ffa500', width=1, dash=(6, 3))
        
        # Draw waveform
        if num_points < 2:
            return
        
        step = w / (num_points - 1)
        
        # Collect points for below and above threshold
        below_segments = []
        above_segments = []
        current_segment = []
        last_above = None
        
        for i, peak in enumerate(self.peak_history):
            x = i * step
            y = h - (peak * h)
            is_above = peak > threshold
            
            if last_above is None:
                last_above = is_above
                current_segment = [(x, y)]
            elif is_above == last_above:
                current_segment.append((x, y))
            else:
                # Transition point - interpolate crossing
                prev_peak = self.peak_history[i - 1]
                if threshold != prev_peak and peak != prev_peak:
                    t = (threshold - prev_peak) / (peak - prev_peak)
                    cross_x = (i - 1 + t) * step
                    cross_y = h - (threshold * h)
                    current_segment.append((cross_x, cross_y))
                
                if last_above:
                    above_segments.append(current_segment)
                else:
                    below_segments.append(current_segment)
                
                current_segment = [(cross_x, cross_y) if threshold != prev_peak and peak != prev_peak else (x, y), (x, y)]
                last_above = is_above
        
        # Add final segment
        if current_segment:
            if last_above:
                above_segments.append(current_segment)
            else:
                below_segments.append(current_segment)
        
        # Draw filled area under below-threshold segments
        fill_color = '#2d5a2d' if self.is_dark_mode else '#c8e6c9'
        line_color = '#44ff44' if self.is_dark_mode else '#2e7d32'
        above_line_color = '#ff6b6b' if self.is_dark_mode else '#d32f2f'
        
        for segment in below_segments:
            if len(segment) >= 2:
                points = []
                for x, y in segment:
                    points.extend([x, y])
                # Fill area
                fill_pts = [segment[0][0], h] + points + [segment[-1][0], h]
                canvas.create_polygon(fill_pts, fill=fill_color, outline='')
                # Draw solid line
                canvas.create_line(points, fill=line_color, width=2, smooth=True)
        
        # Draw above-threshold segments as dashed lines (no fill)
        for segment in above_segments:
            if len(segment) >= 2:
                points = []
                for x, y in segment:
                    points.extend([x, y])
                # Draw dashed line for above threshold
                canvas.create_line(points, fill=above_line_color, width=2, dash=(4, 4))
    
    def _position_window(self):
        self.root.update_idletasks()
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        ww = self.root.winfo_width()
        wh = self.root.winfo_height()
        self.root.geometry(f"+{sw - ww - 20}+{sh - wh - 80}")
    
    def _setup_tray(self):
        """Setup system tray icon"""
        if not TRAY_AVAILABLE:
            return
        
        # Create a simple icon (green circle)
        def create_icon():
            img = Image.new('RGBA', (64, 64), (0, 0, 0, 0))
            draw = ImageDraw.Draw(img)
            draw.ellipse([8, 8, 56, 56], fill=(76, 175, 80, 255))
            draw.text((22, 18), "D", fill=(255, 255, 255, 255))
            return img
        
        menu = pystray.Menu(
            pystray.MenuItem("Show", self._show_window, default=True),
            pystray.MenuItem("Exit", self._exit_app)
        )
        
        self.tray_icon = pystray.Icon("Dolphin", create_icon(), "Dolphin - Volume Limiter", menu)
        
        # Run tray icon in separate thread
        tray_thread = threading.Thread(target=self.tray_icon.run, daemon=True)
        tray_thread.start()
    
    def _show_window(self, icon=None, item=None):
        """Show the main window"""
        self.root.after(0, self._do_show_window)
    
    def _do_show_window(self):
        """Actually show the window (must be called from main thread)"""
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()
    
    def _exit_app(self, icon=None, item=None):
        """Exit the application"""
        self.root.after(0, self._do_exit)
    
    def _do_exit(self):
        """Actually exit (must be called from main thread)"""
        self._exiting = True
        if self._hotkeys:
            self._hotkeys.stop()
        self.limiter.save_settings()
        self.limiter.stop()
        if self.tray_icon:
            self.tray_icon.stop()
        self.root.destroy()
    
    def _on_closing(self):
        if self.settings.show_close_notifications and self.tray_icon:
            # Minimize to tray instead of closing
            self.root.withdraw()
            return
        
        self._do_exit()


def main():
    # Check for --minimized flag (used when starting at login)
    start_minimized = "--minimized" in sys.argv
    
    root = tk.Tk()
    DolphinGUI(root, start_minimized=start_minimized)
    root.mainloop()


if __name__ == "__main__":
    main()
