# Call Pipeline Verified Working

**Date**: 2026-03-21
**Status**: Full call pipeline confirmed working end-to-end.

## What Was Verified

- Wireless ADB dialing (no USB needed)
- Bluetooth HFP audio routing via PipeWire SCO nodes
- Audio playback into call (`pw-play` → SCO sink)
- Call recording (`pw-record` ← SCO source)
- Auto-conversion of stereo 48kHz WAV to mono 16kHz
- Signal retry logic (auto-retries on signal loss)
- Clean hangup after playback + extra recording time

## Test Call

- Number: +966505501494
- Audio played: `when-will-you-learn-script.wav` (19.7s)
- Recording: `call_recording.wav` (25.5s, has audio)
- Total call duration: 30.5s
- User confirmed audio was heard on receiving end

## Working Configuration

- ADB: wireless (192.168.0.29, port varies per session)
- Bluetooth: OnePlus 6T (64:A2:F9:B8:21:94) paired to buzastation
- Audio: PipeWire native HFP backend, SCO nodes auto-discovered
- One SIM disabled (no SIM selector tap needed)
