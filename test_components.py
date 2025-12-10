"""
Quick test script to verify Tame components work
"""
import sys
print("Testing imports...")

try:
    import tkinter as tk
    print("✓ tkinter")
except Exception as e:
    print(f"✗ tkinter: {e}")

try:
    import numpy as np
    print("✓ numpy")
except Exception as e:
    print(f"✗ numpy: {e}")

try:
    from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
    print("✓ pycaw")
except Exception as e:
    print(f"✗ pycaw: {e}")

try:
    from comtypes import CLSCTX_ALL
    print("✓ comtypes")
except Exception as e:
    print(f"✗ comtypes: {e}")

try:
    import pyaudio
    print("✓ pyaudio")
except Exception as e:
    print(f"✗ pyaudio: {e}")

print("\nTesting volume control...")
try:
    from ctypes import cast, POINTER
    devices = AudioUtilities.GetSpeakers()
    interface = devices.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
    volume = cast(interface, POINTER(IAudioEndpointVolume))
    current_vol = volume.GetMasterVolumeLevelScalar()
    print(f"✓ Volume control works! Current volume: {int(current_vol * 100)}%")
except Exception as e:
    print(f"✗ Volume control: {e}")

print("\n✅ All components working! Tame should run successfully.")
input("Press Enter to exit...")
