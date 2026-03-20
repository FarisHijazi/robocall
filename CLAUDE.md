# Robocall Project

Automated phone calling system using a real Android phone (OnePlus 6T) connected via USB (ADB) and Bluetooth (HFP audio).

## Setup

### Prerequisites
- OnePlus 6T connected via wireless ADB (IP: 192.168.0.29, port varies)
- Wireless debugging enabled in Developer Options (Android 11+)
- Already paired — just need `adb connect 192.168.0.29:<port>` after reboot
- Bluetooth paired between phone (64:A2:F9:B8:21:94) and PC (buzastation)
- BlueZ configured with `--noplugin=hfp-hf,hfp-ag`
- WirePlumber configured with logind/seat-monitoring disabled
- Other users' WirePlumber instances killed
- SIM 2 disabled — calls go directly through SIM 1 (mobily1), no SIM picker

### Start
```bash
./start_bluetooth.sh
```

### Make a call (full pipeline)
```bash
./make_call.sh [number] [wav_file]
```

### Make a call (manual)
```bash
# Dial
adb shell "am start -a android.intent.action.CALL -d tel:+966XXXXXXXXX"

# Wait for call to connect, then SCO nodes appear:
# Play audio to caller:
pw-play --target=bluez_output.64_A2_F9_B8_21_94.1 file_mono16k.wav

# Record from caller:
pw-record --target=bluez_input.64_A2_F9_B8_21_94.0 --rate=16000 --channels=1 recording.wav

# Hang up:
adb shell "input keyevent KEYCODE_ENDCALL"
```

## Audio routing

- SCO nodes appear as PipeWire **Streams** during active calls (not Sinks/Sources)
- Use `pw-dump` to find them (not `pactl list`)
- SCO sink (PC→phone): `bluez_output.64_A2_F9_B8_21_94.1` (factory: api.bluez5.sco.sink)
- SCO source (phone→PC): `bluez_input.64_A2_F9_B8_21_94.0` (factory: api.bluez5.sco.source)
- Codec: mSBC at 16kHz
- Audio files MUST be mono for pw-play to work with SCO sink
- Convert: `ffmpeg -y -i input.wav -ac 1 -ar 16000 output.wav`

## Architecture
- **Dialing**: ADB intents
- **Audio**: Bluetooth HFP via PipeWire SCO nodes (native backend, mSBC codec)
- **TTS/STT**: Google Cloud (planned)

## Important notes
- SIM signal (mobily1) is flaky — scripts must retry with 30s waits
- NEVER toggle airplane mode — kills wireless ADB connection
- Call state tracked via `dumpsys telecom` TC@ID entries

## Key Files
- `make_call.sh` - Full call pipeline: dial, play audio, record, hang up (with retries)
- `start_bluetooth.sh` - Initialize bluetooth audio stack
- `bt_connect_hfp.expect` - Connect phone via bluetooth with agent
- `call.py` - Original call script (uses pactl, partially outdated)
- `test_sco.py` - SCO audio test script
- `docs/devlog/` - Development logs
