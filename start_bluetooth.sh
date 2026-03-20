#!/bin/bash
# Start the Bluetooth HFP audio stack for robocall
# Run this after a reboot or when bluetooth audio stops working

set -e

PHONE_MAC="64:A2:F9:B8:21:94"

echo "=== Robocall Bluetooth Setup ==="

# 1. Kill other users' WirePlumber instances that block HFP
echo "[1/5] Stopping other WirePlumber instances..."
for pid in $(ps aux | grep wireplumber | grep -v "faris\|grep" | awk '{print $2}'); do
    sudo kill "$pid" 2>/dev/null && echo "  Killed PID $pid"
done

# 2. Restart bluetooth with HFP plugins disabled
echo "[2/5] Restarting Bluetooth daemon..."
sudo systemctl restart bluetooth
sleep 2

# 3. Restart PipeWire stack
echo "[3/5] Restarting PipeWire stack..."
systemctl --user restart pipewire pipewire-pulse wireplumber
sleep 3

# 4. Check for errors
echo "[4/5] Checking for errors..."
ERRORS=$(journalctl --user -u wireplumber --since "5 sec ago" --no-pager 2>&1 | grep -i "error\|fail" | grep -v "libcamera" | head -5)
if [ -n "$ERRORS" ]; then
    echo "  WARNING: WirePlumber errors detected:"
    echo "$ERRORS"
else
    echo "  No errors!"
fi

# 5. Reconnect phone
echo "[5/5] Reconnecting phone..."
if bluetoothctl info "$PHONE_MAC" 2>/dev/null | grep -q "Connected: yes"; then
    echo "  Phone already connected!"
else
    echo "  Phone not connected. Please tap 'buzastation' in phone's Bluetooth settings."
    echo "  Or run: sudo adb shell 'am start -a android.settings.BLUETOOTH_SETTINGS'"
fi

echo ""
echo "=== Status ==="
wpctl status 2>/dev/null | head -25
