# Research: HFP Audio Gateway - No Sinks/Sources & SCO Routing

Date: 2026-03-20

## Problem Statement

- OnePlus 6T connected via Bluetooth, profile "audio-gateway" active
- `pactl list cards` shows: `audio-gateway: ... (sinks: 0, sources: 0)`
- No PipeWire sink/source nodes created for the phone
- When a call is made via ADB, SCO stays INACTIVE, audio goes to phone earpiece

## Root Cause Analysis

### Why "audio-gateway" shows sinks: 0, sources: 0

The "audio-gateway" profile in PipeWire represents the **AG (Audio Gateway) role** - this is the role a phone normally plays. When your PC connects with the `hfp_ag` role, it acts as the Audio Gateway (like a phone), and the remote device is expected to be the Hands-Free unit (like a headset/car kit).

**The problem**: Your PC is acting as `hfp_ag` (Audio Gateway) while the phone is ALSO an Audio Gateway. You have two AGs trying to talk to each other. The phone needs to see the PC as an HFP Hands-Free device (`hfp_hf` role on PC side).

When the PC uses `hfp_hf` (Hands-Free role), it presents itself to the phone like a Bluetooth headset/car kit. The phone then routes call audio to it via SCO, just like it would to a car stereo.

### Why SCO stays INACTIVE

1. The `audio-gateway` profile has no transport endpoints (sinks: 0, sources: 0) because HFP AG nodes are only created when an HF device initiates the SCO connection
2. With `--noplugin=hfp-hf,hfp-ag` on bluetoothd, BlueZ's own HFP handling is disabled (correct - lets PipeWire handle it)
3. But PipeWire's native HFP backend with `hfp_hf` role needs the phone to initiate the call and route audio to BT

### The HFP role model

- **HFP HF (Hands-Free)** = PC acts like a headset/car kit. Phone sends call audio here.
- **HFP AG (Audio Gateway)** = PC acts like a phone. A headset would connect to it.
- For your use case (receive call audio from phone), PC must be **hfp_hf**.

## Findings & Concrete Solutions

### Solution 1: Ensure PC is using hfp_hf role properly (PipeWire side)

The WirePlumber config already has `hfp_hf` in roles. But the card is showing "audio-gateway" profile, meaning the phone connected using the AG role (phone is AG, PC is HF). This is actually CORRECT for your use case.

The issue is that **SCO audio nodes are only created dynamically when a call is active and the phone initiates SCO**. The phone must route audio to Bluetooth for SCO to activate.

Check if there's a `headset-head-unit` profile available (there isn't currently - only `off` and `audio-gateway`). The `audio-gateway` profile with 0 sinks/sources is expected when idle - nodes should appear when SCO activates during a call.

### Solution 2: Force Android to route call audio to Bluetooth (ADB)

The phone needs to be told to use the BT headset for call audio. Try these commands:

```bash
# Method 1: Use Android's media/audio service to start SCO
sudo adb shell "cmd media.audio_policy forceUseForCommunication bt_sco"

# Method 2: Use dumpsys to check current audio routing
sudo adb shell "dumpsys audio" | grep -i "sco\|bluetooth\|routing"

# Method 3: Tap the Bluetooth audio button in the dialer UI during a call
# First, find the button coordinates by screenshotting during a call:
sudo adb shell screencap -p /sdcard/call_screen.png
sudo adb pull /sdcard/call_screen.png
# Then tap the bluetooth button (coordinates vary by phone/dialer)
sudo adb shell "input tap X Y"

# Method 4: Use Android's AudioManager via app_process
sudo adb shell "am start -a android.intent.action.CALL -d tel:+966XXXXXXXXX"
sleep 5  # wait for call to connect
sudo adb shell "input tap 540 1755"  # tap SIM selection
sleep 3
# Try to enable bluetooth SCO via key event
sudo adb shell "input keyevent 79"  # KEYCODE_HEADSETHOOK - may toggle BT audio

# Method 5: Use content provider / settings
sudo adb shell "settings put system bluetooth_sco 1"
```

### Solution 3: Use oFono instead of native backend

The Sipfront blog and PipeWire telephony article show that **oFono** is the proper way to get full HFP telephony working. oFono acts as the bridge between HFP AT commands and the modem.

```bash
# Install oFono
sudo apt install ofono

# oFono will register as the HFP backend, replacing the native one
# Change WirePlumber config:
# bluez5.hfphsp-backend = ofono   (instead of native)

# After pairing, verify oFono sees the phone:
/usr/share/ofono/scripts/list-modems

# Make a call:
/usr/share/ofono/scripts/dial-number +966XXXXXXXXX

# Hang up:
/usr/share/ofono/scripts/hangup-active-calls
```

With oFono, PipeWire nodes (sink + source) are created dynamically when a call starts, and `wpctl status` will show them.

### Solution 4: Use PipeWire 1.4+ telephony D-Bus API (if available)

PipeWire 1.4+ has built-in telephony support without oFono:

```bash
# Check PipeWire version
pipewire --version

# If >= 1.3.82, the telephony D-Bus API is available:
# Service: org.pipewire.Telephony
# Methods: Dial(s), Answer(), Hangup(), SendTones(s), etc.

# Make a call via D-Bus:
dbus-send --session --dest=org.pipewire.Telephony \
  /org/pipewire/Telephony/device_XX \
  org.pipewire.Telephony.AudioGateway1.Dial \
  string:"+966XXXXXXXXX"
```

### Solution 5: Force SCO connection from Linux side

```bash
# Check if SCO socket can be opened (requires active call on phone)
# The test_sco.py script tries this but needs the phone to have SCO enabled

# Check bluetooth SCO state:
sudo btmgmt info | grep -i sco

# Monitor HCI for SCO events:
sudo btmon &
# Then make a call and watch for SCO Setup commands
```

### Solution 6: Android UI automation to tap Bluetooth button

```bash
# During an active call, capture the screen and find the BT button:
sudo adb shell screencap -p /sdcard/screen.png
sudo adb pull /sdcard/screen.png /tmp/screen.png

# On OnePlus 6T dialer, the Bluetooth button is typically in the call options
# You may need to first tap the "Audio" or speaker icon to get routing options
# Common coordinates for OnePlus dialer during call:
# Audio/Speaker button: around (270, 1500) area
# Bluetooth option: appears after tapping audio button

# Alternative: Use uiautomator to find the button:
sudo adb shell "uiautomator dump /sdcard/ui.xml"
sudo adb pull /sdcard/ui.xml /tmp/ui.xml
grep -i "bluetooth\|audio\|speaker" /tmp/ui.xml
```

## Diagnostic Commands

```bash
# Check what profiles/transports BlueZ sees:
bluetoothctl info 64:A2:F9:B8:21:94

# Check PipeWire bluetooth details:
pw-dump | jq '.[] | select(.info.props["device.name"] == "bluez_card.64_A2_F9_B8_21_94")'

# Monitor PipeWire for new node creation:
pw-mon &

# Check Android side:
sudo adb shell "dumpsys bluetooth_manager" | grep -A5 -i "sco\|hfp\|audio"
sudo adb shell "dumpsys audio" | grep -i "bluetooth\|sco\|routing"

# Check WirePlumber bluetooth logs:
journalctl --user -u wireplumber -f | grep -i "bluez\|bluetooth\|sco\|hfp"
```

## Recommended Action Plan

1. **First**: Run diagnostics during a call to understand current state
2. **Quick win**: Try ADB UI automation to tap the Bluetooth audio button during a call
3. **Proper fix**: Install oFono and switch backend from `native` to `ofono`
4. **Long term**: Upgrade to PipeWire 1.4+ for native telephony D-Bus API
