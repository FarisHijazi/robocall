# Robocall

Automated phone calling system using a real Android phone connected via ADB and Bluetooth HFP. Make calls, play audio, and record responses — all programmatically from Linux.

Comes with a **Twilio-compatible Python API** so you can use it as a drop-in replacement for Twilio's `client.calls.create()`.

## How it works

```
┌──────────┐   ADB (WiFi)   ┌──────────────┐   Cellular   ┌──────────┐
│  Linux   │ ◄────────────► │ Android Phone │ ◄──────────► │  Callee  │
│   PC     │   Bluetooth    │  (OnePlus 6T) │              │          │
│          │ ◄──── HFP ───► │               │              │          │
└──────────┘   SCO Audio    └──────────────┘              └──────────┘
```

- **Dialing**: ADB shell intents (`android.intent.action.CALL`)
- **Audio I/O**: Bluetooth HFP via PipeWire SCO nodes (mSBC codec, 16kHz)
- **Control**: ADB keyevents for hang up, screen tap, etc.

## Quick Start

### Prerequisites

- Linux with PipeWire and BlueZ
- Android phone paired via Bluetooth (HFP profile)
- Wireless ADB enabled on the phone
- `ffmpeg`, `pw-play`, `pw-record` available

### Low-level SDK

```python
from robocall import Robocall

rc = Robocall(phone_ip="192.168.0.29", phone_mac="64:A2:F9:B8:21:94")

# Make a call, play audio, record response
call = rc.call(
    to="+1234567890",
    audio_file="message.wav",
    record=True,
    extra_record_seconds=5,
)
call.hangup()

# Check recording
for rec in call.recordings:
    print(f"{rec.path}: {rec.duration}s, has_audio={rec.has_audio}")
```

### Twilio-compatible API

```python
from robocall_twilio import Client, VoiceResponse

client = Client()

# Simple call with audio file
call = client.calls.create(
    to="+1234567890",
    url="file:///path/to/message.wav",
)

# Call with TwiML
call = client.calls.create(
    to="+1234567890",
    twiml='<Response><Say>Hello from robocall</Say><Pause length="2"/></Response>',
)

# Using VoiceResponse builder
response = VoiceResponse()
response.say("Hello, this is an automated call.")
response.pause(length=2)
response.play("file:///path/to/audio.wav")
response.hangup()

call = client.calls.create(to="+1234567890", twiml=str(response))

# Check call status
call = client.calls(call.sid).fetch()
print(call.status, call.duration)

# Hang up
client.calls(call.sid).update(status="completed")
```

### CLI

```bash
# Low-level
python robocall.py +1234567890 --audio message.wav --record

# Twilio-compatible
python robocall_twilio.py +1234567890 --say "Hello world"
python robocall_twilio.py +1234567890 --play /path/to/audio.wav --record

# Shell script
./make_call.sh +1234567890 message.wav
```

## Setup

### 1. Phone Setup

- Enable **Developer Options** and **Wireless Debugging** on Android
- Pair phone via `adb pair <ip>:<port>`
- Connect: `adb connect <ip>:<port>`

### 2. Bluetooth Setup

- Pair phone with PC via `bluetoothctl`
- Configure BlueZ: `--noplugin=hfp-hf,hfp-ag` (let PipeWire handle HFP)
- Run `./start_bluetooth.sh` to initialize the audio stack

### 3. Audio Notes

- Audio files must be **mono WAV** for SCO playback (auto-converted if not)
- SCO nodes appear as PipeWire **Streams** during active calls (not Sinks/Sources)
- Use `pw-dump` to discover them, not `pactl list`

## Architecture

| Component | Role |
|-----------|------|
| `robocall.py` | Low-level SDK: ADB, PipeWire, call management |
| `robocall_twilio.py` | Twilio-compatible API wrapper + TwiML support |
| `make_call.sh` | Shell script for quick calls with retry logic |
| `start_bluetooth.sh` | Initialize Bluetooth HFP audio stack |

## TwiML Support

Supported verbs:
- `<Say>` — Text-to-speech (requires `piper` or `espeak-ng`)
- `<Play>` — Play audio file
- `<Pause>` — Wait N seconds
- `<Record>` — Record caller audio
- `<Hangup>` — End the call

## License

MIT
