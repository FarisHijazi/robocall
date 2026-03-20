# Audio Routing Working via PipeWire SCO Nodes

Date: 2026-03-21

## Summary

Full bidirectional audio during Bluetooth HFP calls is now working.

## What works

1. **Dialing**: `adb shell "am start -a android.intent.action.CALL -d tel:+NUMBER"`
2. **Audio to caller**: `pw-play --target=bluez_output.64_A2_F9_B8_21_94.1 file.wav`
3. **Audio from caller**: `pw-record --target=bluez_input.64_A2_F9_B8_21_94.0 --rate=16000 --channels=1 recording.wav`
4. **Hang up**: `adb shell "input keyevent KEYCODE_ENDCALL"`

## Key discoveries

### SCO nodes are Streams, not Sinks/Sources
- During an active call, PipeWire creates SCO nodes but they appear as **Streams** (Stream/Input/Audio, Stream/Output/Audio), NOT as regular Sinks/Sources
- `pactl list short sinks/sources` does NOT show them
- `pw-dump` reveals them with `factory.name: api.bluez5.sco.sink` and `api.bluez5.sco.source`
- They use mSBC codec at 16kHz

### Node names
- SCO sink (PC → phone): `bluez_output.64_A2_F9_B8_21_94.1`
- SCO source (phone → PC): `bluez_input.64_A2_F9_B8_21_94.0`

### Audio format requirements
- WAV files must be **mono** for pw-play to work with SCO sink
- The original stereo 48kHz file failed with "given channels (1) don't match file channels (2)"
- Convert with: `ffmpeg -y -i input.wav -ac 1 -ar 16000 output.wav`
- Don't force `--channels` or `--format` on pw-play — let it auto-detect from the file

### Signal reliability
- SIM signal (mobily1) is very flaky — calls fail often with "Mobile network not available"
- Script needs to retry with 30s waits between attempts
- Signal check `mVoiceRegState=0(IN_SERVICE)` can pass but call still fails
- NEVER toggle airplane mode — it kills wireless ADB

### Call detection
- Track calls by TC@ID number in `dumpsys telecom`
- Active call: `startTime: [1-9]` and `endTime: 0`
- Failed call: `endTime: [1-9]` with `callTerminationReason`

## Working script
`make_call.sh` — handles signal retries, call detection, SCO node discovery, audio playback and recording.

## SIM configuration
- SIM 2 disabled — no SIM picker dialog needed
- Calls go directly through SIM 1 (mobily1)
