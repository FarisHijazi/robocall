#!/bin/bash
# Bluetooth pairing with OnePlus 6T
# Handles confirmation on both PC (bluetoothctl agent) and phone (ADB tap)

PHONE_MAC="64:A2:F9:B8:21:94"

# Remove old pairing if any
bluetoothctl remove "$PHONE_MAC" 2>/dev/null

# Start bluetoothctl agent in background with auto-confirm
(
    sleep 1
    # Wait for pairing dialog on phone and tap PAIR
    for i in $(seq 1 15); do
        sleep 1
        # Dump UI and look for PAIR button
        sudo adb shell "uiautomator dump /sdcard/ui_p.xml" 2>/dev/null
        sudo adb pull /sdcard/ui_p.xml /tmp/ui_p.xml 2>/dev/null

        # Look for PAIR or Pair button
        BOUNDS=$(grep -oP 'text="PAIR"[^/]*bounds="\K[^"]*' /tmp/ui_p.xml 2>/dev/null)
        if [ -z "$BOUNDS" ]; then
            BOUNDS=$(grep -oP 'text="Pair"[^/]*bounds="\K[^"]*' /tmp/ui_p.xml 2>/dev/null)
        fi

        if [ -n "$BOUNDS" ]; then
            # Parse bounds [x1,y1][x2,y2] and tap center
            X1=$(echo "$BOUNDS" | grep -oP '^\[\K[0-9]+')
            Y1=$(echo "$BOUNDS" | grep -oP '^\[[0-9]+,\K[0-9]+')
            X2=$(echo "$BOUNDS" | grep -oP '\]\[\K[0-9]+')
            Y2=$(echo "$BOUNDS" | grep -oP '\]\[[0-9]+,\K[0-9]+')
            CX=$(( (X1 + X2) / 2 ))
            CY=$(( (Y1 + Y2) / 2 ))
            echo "PHONE: Found PAIR button at ($CX, $CY), tapping..."
            sudo adb shell "input tap $CX $CY"
            break
        fi
        echo "PHONE: Waiting for PAIR dialog... ($i/15)"
    done
) &
PHONE_PID=$!

# Run bluetoothctl with agent
expect << 'EXPECT_EOF'
set timeout 45
set mac "64:A2:F9:B8:21:94"

spawn bluetoothctl
expect "#"

send "agent on\r"
expect "#"

send "default-agent\r"
expect "#"

send "pairable on\r"
expect "#"

send "scan on\r"
expect {
    "OnePlus 6T" { }
    timeout { puts "ERROR: Phone not found"; exit 1 }
}
sleep 2

send "scan off\r"
expect "#"

send "trust $mac\r"
expect "trust succeeded"

send "pair $mac\r"
expect {
    "Confirm passkey" {
        send "yes\r"
        expect {
            "Pairing successful" { puts "\n=== PAIRING SUCCESSFUL ===" }
            "AuthenticationFailed" { puts "\n=== AUTH FAILED - phone didn't confirm ===" }
            timeout { puts "\n=== PAIRING TIMEOUT ===" }
        }
    }
    "Pairing successful" { puts "\n=== PAIRING SUCCESSFUL ===" }
    timeout { puts "ERROR: Pairing timed out"; exit 1 }
}

sleep 3
send "connect $mac\r"
expect {
    "Connection successful" { puts "\n=== CONNECTED ===" }
    "Failed" { puts "\n=== CONNECTION FAILED ===" }
    timeout { puts "\n=== CONNECTION TIMEOUT ===" }
}

sleep 2
send "info $mac\r"
expect "#"
sleep 1
send "quit\r"
expect eof
EXPECT_EOF

wait $PHONE_PID 2>/dev/null
echo ""
echo "=== Final status ==="
bluetoothctl info "$PHONE_MAC" 2>/dev/null | grep -E "Paired|Connected|Trusted"
