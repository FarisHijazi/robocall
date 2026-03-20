# Bluetooth HFP Audio Setup for Robocall Project

**Date**: 2026-03-19
**Status**: Bluetooth paired and recognized by PipeWire. HFP audio routing pending call test.

## What Was Accomplished

### 1. Phone Call via ADB (Working)
- OnePlus 6T connected via USB, ADB working
- Can dial numbers: `sudo adb shell "am start -a android.intent.action.CALL -d tel:+966XXXXXXXXX"`
- SIM selection dialog appears (dual SIM) - must tap SIM 1 at coordinates (540, 1755)
- Call successfully connected and verified with live audio

### 2. Bluetooth HFP Setup (Working)
- Phone paired with PC "buzastation" via Bluetooth
- MAC: `64:A2:F9:B8:21:94`
- HFP Audio Gateway profile detected by PipeWire
- PipeWire device: `bluez_card.64_A2_F9_B8_21_94`

## Key Configuration Changes

### BlueZ (Bluetooth Daemon)
**File**: `/etc/systemd/system/bluetooth.service.d/override.conf`
```ini
[Service]
ExecStart=
ExecStart=/usr/libexec/bluetooth/bluetoothd --noplugin=hfp-hf,hfp-ag
```
**Why**: BlueZ's internal HFP plugin conflicts with PipeWire's native HFP backend.
Both try to register HFP profiles via D-Bus, causing `RegisterProfile() failed: NotPermitted`.
Disabling BlueZ's plugins lets PipeWire handle HFP.

### WirePlumber Configuration
**File**: `~/.config/wireplumber/wireplumber.conf.d/50-bluetooth.conf`
```
wireplumber.profiles = {
  main = {
    support.logind = disabled
    monitor.bluez.seat-monitoring = disabled
  }
}

monitor.bluez.properties = {
    bluez5.roles = [ hfp_hf hfp_ag a2dp_sink a2dp_source ]
    bluez5.hfphsp-backend = native
    bluez5.enable-msbc = true
    bluez5.enable-hw-volume = true
}
```
**Why**:
- `support.logind = disabled` and `monitor.bluez.seat-monitoring = disabled`:
  On this headless server (SSH access, GDM greeter only), logind reports seat as "closing".
  The bluetooth monitor's `bluez.lua` script checks seat state and refuses to start if not "active".
  Disabling seat monitoring makes the monitor start unconditionally.
- `bluez5.hfphsp-backend = native`: PipeWire registers HFP profiles directly (not via oFono).

### Other Users' WirePlumber
**Critical**: Other user accounts (sana, hasan, sadia, gdm) run their own WirePlumber instances.
One of them was registering HFP profiles before ours, causing `listen(): Address already in use`.
**Fix**: Kill those instances before starting ours: `sudo kill <PIDs>`
**TODO**: Permanently disable WirePlumber for those users or configure them to skip bluetooth.

### oFono
- Installed but **disabled** (`sudo systemctl disable ofono`)
- Was causing `UUID already registered` conflicts with BlueZ
- Not needed since PipeWire's native backend handles HFP

## Startup Sequence (Order Matters)
1. Kill other users' WirePlumber instances
2. Restart bluetooth: `sudo systemctl restart bluetooth`
3. Restart PipeWire stack: `systemctl --user restart pipewire pipewire-pulse wireplumber`
4. Reconnect phone via Bluetooth (from phone side: Settings > Bluetooth > buzastation)

## Bluetooth Pairing (One-time)
- Phone must initiate pairing from Settings > Bluetooth > "Pair new device"
- PC must have agent running to auto-confirm: `bluetoothctl agent on; default-agent; pairable on; discoverable on`
- Phone shows passkey confirmation dialog - must tap "PAIR" (uiautomator can find it)

## Next Steps
- [ ] Test HFP audio during an active call (SCO nodes should appear in PipeWire)
- [ ] Record call audio to file
- [ ] Play audio file into call
- [ ] Add Google TTS/STT
- [ ] Build the full robocall Python service
