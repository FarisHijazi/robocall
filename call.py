#!/usr/bin/env python3
"""Make a phone call via ADB, play audio into it, and record the call."""

import subprocess
import time
import sys
import re
import signal
import os

PHONE_IP = "192.168.0.29"
PHONE_MAC = "64:A2:F9:B8:21:94"
SIM1_TAP_COORDS = (540, 1755)

AUDIO_FILE = "/home/faris/robocall/when-will-you-learn-script.wav"
RECORDING_FILE = "/home/faris/robocall/call_recording.wav"


def adb(*args):
    """Run an ADB command and return output."""
    cmd = ["adb", *args]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
    if result.returncode != 0:
        print(f"  ADB error: {result.stderr.strip()}")
    return result.stdout.strip()


def check_adb_connected():
    """Ensure ADB is connected wirelessly."""
    devices = adb("devices")
    if PHONE_IP in devices and "device" in devices:
        print(f"  ADB connected to {PHONE_IP}")
        return True
    print(f"  ADB not connected. Trying to connect...")
    # Try common ports
    for port in range(37000, 38000, 100):
        result = adb("connect", f"{PHONE_IP}:{port}")
        if "connected" in result:
            print(f"  Connected on port {port}")
            return True
    print("  ERROR: Could not connect ADB. Check wireless debugging port.")
    return False


def check_bluetooth_connected():
    """Check if phone is connected via Bluetooth."""
    result = subprocess.run(
        ["bluetoothctl", "info", PHONE_MAC],
        capture_output=True, text=True
    )
    if "Connected: yes" in result.stdout:
        print("  Bluetooth connected")
        return True
    print("  Bluetooth not connected!")
    return False


def dial(number):
    """Dial a number via ADB intent."""
    print(f"  Dialing {number}...")
    adb("shell", f"am start -a android.intent.action.CALL -d tel:{number}")


def wait_for_hfp_audio(timeout=15):
    """Wait for HFP audio sink/source to appear in PipeWire."""
    print(f"  Waiting for HFP audio (up to {timeout}s)...")
    for i in range(timeout):
        result = subprocess.run(
            ["pactl", "list", "short", "sinks"],
            capture_output=True, text=True
        )
        sinks = result.stdout
        result2 = subprocess.run(
            ["pactl", "list", "short", "sources"],
            capture_output=True, text=True
        )
        sources = result2.stdout

        hfp_sink = None
        hfp_source = None

        for line in sinks.splitlines():
            if "bluez" in line.lower() and "headset" in line.lower():
                hfp_sink = line.split("\t")[1]
        for line in sources.splitlines():
            if "bluez" in line.lower() and "headset" in line.lower():
                hfp_source = line.split("\t")[1]

        if hfp_sink and hfp_source:
            print(f"  HFP sink: {hfp_sink}")
            print(f"  HFP source: {hfp_source}")
            return hfp_sink, hfp_source

        time.sleep(1)
        print(f"  ... ({i+1}/{timeout})")

    # Fallback: check for any bluez sink/source
    print("  No 'headset' nodes found, checking for any bluez nodes...")
    for line in sinks.splitlines():
        if "bluez" in line.lower():
            hfp_sink = line.split("\t")[1]
    for line in sources.splitlines():
        if "bluez" in line.lower():
            hfp_source = line.split("\t")[1]

    if hfp_sink and hfp_source:
        print(f"  Bluez sink: {hfp_sink}")
        print(f"  Bluez source: {hfp_source}")
        return hfp_sink, hfp_source

    print("  ERROR: HFP audio nodes did not appear")
    return None, None


def hangup():
    """End the call via ADB keyevent."""
    print("  Hanging up...")
    adb("shell", "input keyevent KEYCODE_ENDCALL")


def run_call(number, audio_file, recording_file):
    """Execute the full call flow."""
    print("\n=== ROBOCALL ===\n")

    # Pre-checks
    print("[1/6] Checking ADB...")
    if not check_adb_connected():
        sys.exit(1)

    print("[2/6] Checking Bluetooth...")
    if not check_bluetooth_connected():
        sys.exit(1)

    # Dial
    print(f"[3/6] Calling {number}...")
    dial(number)

    # Wait for HFP audio
    print("[4/6] Waiting for HFP audio nodes...")
    hfp_sink, hfp_source = wait_for_hfp_audio(timeout=20)
    if not hfp_sink or not hfp_source:
        print("  Aborting - no audio nodes. Hanging up.")
        hangup()
        sys.exit(1)

    # Give a moment for the call to fully establish
    time.sleep(1)

    # Start recording (background)
    print(f"[5/6] Starting recording + playback...")
    record_proc = subprocess.Popen(
        ["parecord", "--device", hfp_source,
         "--file-format=wav", "--rate=8000", "--channels=1",
         recording_file],
    )

    # Play audio into the call
    play_proc = subprocess.Popen(
        ["paplay", "--device", hfp_sink, audio_file],
    )

    try:
        # Wait for playback to finish
        play_proc.wait()
        print("  Playback finished")

        # Record a bit more to catch any response
        print("  Recording 3 more seconds...")
        time.sleep(3)

    except KeyboardInterrupt:
        print("\n  Interrupted!")
    finally:
        # Stop recording
        record_proc.send_signal(signal.SIGINT)
        try:
            record_proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            record_proc.kill()

        print(f"  Recording saved to {recording_file}")

        # Hang up
        print("[6/6] Ending call...")
        hangup()

    print("\n=== DONE ===")


if __name__ == "__main__":
    number = sys.argv[1] if len(sys.argv) > 1 else "+1234567890"
    audio = sys.argv[2] if len(sys.argv) > 2 else AUDIO_FILE
    recording = sys.argv[3] if len(sys.argv) > 3 else RECORDING_FILE
    run_call(number, audio, recording)
