# Tame - Audio Volume Limiter

Tame automatically protects your ears by reducing system volume when audio gets too loud.

## Features

- 🎧 Automatic volume limiting to prevent hearing damage
- 🎮 Perfect for gaming (e.g., CS:GO footsteps vs gunshots)
- 🎵 Real-time audio monitoring
- ⚙️ Customizable loudness cap
- 🪟 System tray support
- 💾 Persistent settings
- 🚀 Run at Windows startup

## Quick Start

### Option 1: Run from Source

1. Run `setup.bat` to install dependencies
2. Run `python tame.py` to start the application

### Option 2: Build Executable

1. Run `setup.bat` to install dependencies
2. Run `build.bat` to create standalone exe
3. Find `Tame.exe` in the `dist` folder

## Requirements

- Windows 10/11
- Python 3.8+ (for running from source)
- "Stereo Mix" or similar audio loopback enabled in Windows (optional but recommended)

## Enabling Stereo Mix (for better audio monitoring)

1. Right-click speaker icon in system tray
2. Select "Sounds" → "Recording" tab
3. Right-click in empty space → "Show Disabled Devices"
4. Right-click "Stereo Mix" → "Enable"

## How It Works

Tame monitors your system's audio output in real-time. When it detects audio that exceeds your configured loudness cap, it instantly reduces the system volume to a safe level. Once the loud audio stops, the volume gradually returns to normal.

The app respects manual volume changes - if you adjust the volume yourself, Tame pauses for 2 seconds before resuming automatic control.

## Usage

1. Launch Tame
2. Set your desired "Volume Cap" using the slider (20% is a good starting point)
3. The app runs in the background and automatically manages volume
4. Close the window to minimize to tray (app keeps running)

## Settings

- **Volume Cap**: Maximum allowed output volume (0-100%)
- **Run at Windows startup**: Auto-start Tame when Windows boots
- **Enable/Disable**: Toggle the limiter on/off

Settings are automatically saved to: `%APPDATA%\tame\settings.json`

## Building from Source

```bash
# Install dependencies
pip install -r requirements.txt

# Run directly
python tame.py

# Build executable
pyinstaller --onefile --windowed --name Tame tame.py
```

## Technical Details

- **Audio Monitoring**: PyAudio for capturing system audio
- **Volume Control**: pycaw for Windows volume management
- **GUI**: tkinter (included with Python)
- **Packaging**: PyInstaller for standalone executable

## Credits

Based on the original C# Mufflr by [John Tringham](https://github.com/johntringham/Mufflr)

- Original: [Avalonia](https://avaloniaui.net/) + [NAudio](https://github.com/naudio/NAudio)
- Tame: tkinter + pycaw

## License

Free and open source. No tracking, telemetry, or data collection.
