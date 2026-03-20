"""Test SCO (Synchronous Connection-Oriented) audio link over Bluetooth HFP.

This script:
1. Opens a SCO socket to the paired phone
2. Records incoming audio to a file
3. Plays audio from a file into the call

SCO is the audio transport for Bluetooth HFP (phone calls).
"""
import socket
import struct
import sys
import time
import wave
import os

# Bluetooth SCO socket constants
BTPROTO_SCO = 2  # SCO protocol number
SOL_SCO = 17

PHONE_MAC = "64:A2:F9:B8:21:94"


def mac_to_bytes(mac: str) -> bytes:
    """Convert MAC address string to bytes in reverse order (BlueZ format)."""
    parts = mac.split(":")
    return bytes(int(p, 16) for p in reversed(parts))


def create_sco_socket():
    """Create and connect a SCO socket to the phone."""
    # AF_BLUETOOTH = 31, SOCK_SEQPACKET = 5, BTPROTO_SCO = 2
    sock = socket.socket(31, socket.SOCK_SEQPACKET, BTPROTO_SCO)

    # For SCO, bind/connect use a simple MAC address string
    # BlueZ Python bindings use str format "XX:XX:XX:XX:XX:XX"
    sock.bind("00:00:00:00:00:00")

    print(f"Connecting SCO to {PHONE_MAC}...")
    sock.connect(PHONE_MAC)
    print("SCO connected!")

    return sock


def record_audio(sock, filename, duration=5):
    """Record audio from SCO socket to a WAV file."""
    print(f"Recording {duration}s of audio to {filename}...")

    frames = []
    start = time.time()

    while time.time() - start < duration:
        try:
            data = sock.recv(1024)
            if data:
                frames.append(data)
        except socket.timeout:
            continue

    # SCO audio is typically 8kHz or 16kHz, 16-bit, mono
    # mSBC (wideband) = 16kHz, CVSD (narrowband) = 8kHz
    raw_data = b''.join(frames)
    print(f"Recorded {len(raw_data)} bytes ({len(frames)} frames)")

    # Save as raw PCM first
    raw_filename = filename.replace('.wav', '.raw')
    with open(raw_filename, 'wb') as f:
        f.write(raw_data)
    print(f"Raw PCM saved to {raw_filename}")

    # Try to save as WAV (8kHz mono 16-bit as default)
    try:
        with wave.open(filename, 'wb') as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)  # 16-bit
            wf.setframerate(8000)  # 8kHz for CVSD, 16kHz for mSBC
            wf.writeframes(raw_data)
        print(f"WAV saved to {filename}")
    except Exception as e:
        print(f"WAV save failed: {e}")

    return raw_data


def play_audio(sock, filename):
    """Play a WAV file into the SCO socket."""
    print(f"Playing {filename} into call...")

    with wave.open(filename, 'rb') as wf:
        data = wf.readframes(wf.getnframes())

    # Send in chunks matching SCO MTU (typically 48 or 60 bytes)
    chunk_size = 48
    sent = 0
    for i in range(0, len(data), chunk_size):
        chunk = data[i:i + chunk_size]
        try:
            sock.send(chunk)
            sent += len(chunk)
        except Exception as e:
            print(f"Send error at byte {sent}: {e}")
            break
        # Pace the sending to match real-time playback
        # 8000 Hz * 2 bytes = 16000 bytes/sec
        time.sleep(chunk_size / 16000.0)

    print(f"Sent {sent} bytes")


def main():
    if len(sys.argv) < 2:
        print("Usage:")
        print("  python3 test_sco.py record [duration]  - Record call audio")
        print("  python3 test_sco.py play <file.wav>     - Play audio into call")
        print("  python3 test_sco.py test                - Quick connection test")
        sys.exit(1)

    cmd = sys.argv[1]

    sock = create_sco_socket()
    sock.settimeout(1.0)

    try:
        if cmd == "test":
            print("SCO connection successful!")
            print(f"Socket: {sock.fileno()}")
            # Try to receive a few bytes
            try:
                data = sock.recv(256)
                print(f"Received {len(data)} bytes: {data[:20].hex()}")
            except socket.timeout:
                print("No data received (timeout) - call might not be active")

        elif cmd == "record":
            duration = int(sys.argv[2]) if len(sys.argv) > 2 else 5
            record_audio(sock, "/home/faris/robocall/recorded_call.wav", duration)

        elif cmd == "play":
            if len(sys.argv) < 3:
                print("Need filename: python3 test_sco.py play <file.wav>")
                sys.exit(1)
            play_audio(sock, sys.argv[2])

    finally:
        sock.close()
        print("SCO socket closed")


if __name__ == "__main__":
    main()
