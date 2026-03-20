#!/bin/bash
set -e

NUMBER="${1:-+1234567890}"
WAV="${2:-/home/faris/robocall/when-will-you-learn-script-mono16k.wav}"
REC="/home/faris/robocall/call_recording_$(date +%Y%m%d_%H%M%S).wav"

echo "=== ROBOCALL ==="
echo "Number: $NUMBER"
echo "Audio:  $WAV"
echo "Record: $REC"

# Helper: get the latest call ID number
get_latest_call_id() {
    adb shell "dumpsys telecom" 2>&1 | grep "Call TC@" | tail -1 | sed 's/.*TC@\([0-9]*\).*/\1/'
}

# Step 1: Wait for signal
echo ""
echo "[1] Checking signal..."
for attempt in $(seq 1 20); do
    voice=$(adb shell "dumpsys telephony.registry" 2>&1 | grep -c "mVoiceRegState=0(IN_SERVICE)" || true)
    if [ "$voice" -gt 0 ]; then
        echo "  Signal OK"
        break
    fi
    echo "  No signal, waiting 30s... (attempt $attempt/20)"
    sleep 30
done

# Step 2: Dial and verify connection
echo ""
echo "[2] Dialing..."
PREV_ID=$(get_latest_call_id)
echo "  Previous call ID: TC@$PREV_ID"

adb shell "am start -a android.intent.action.CALL -d tel:$NUMBER" 2>&1
NEXT_ID=$((PREV_ID + 1))
echo "  Expecting call TC@$NEXT_ID"

echo ""
echo "[3] Waiting for call to connect (you need to pick up)..."
CONNECTED=false
for i in $(seq 1 90); do
    call_info=$(adb shell "dumpsys telecom" 2>&1 | grep -A5 "Call TC@${NEXT_ID}:")

    if echo "$call_info" | grep -q "startTime: [1-9]"; then
        if echo "$call_info" | grep -q "endTime: 0"; then
            echo "  Call CONNECTED after ${i}s"
            CONNECTED=true
            break
        fi
    fi

    # Check if call failed
    if echo "$call_info" | grep -q "endTime: [1-9]"; then
        reason=$(adb shell "dumpsys telecom" 2>&1 | grep -A12 "Call TC@${NEXT_ID}:" | grep "callTerminationReason" | head -1)
        echo "  Call FAILED: $reason"

        if echo "$reason" | grep -qi "network\|radio\|service"; then
            echo "  Signal issue, waiting 30s and retrying..."
            sleep 30
            exec "$0" "$@"
        else
            echo "  Non-signal failure, retrying in 10s..."
            sleep 10
            exec "$0" "$@"
        fi
    fi

    sleep 1
done

if [ "$CONNECTED" = false ]; then
    echo "  TIMEOUT waiting for connection"
    adb shell "input keyevent KEYCODE_ENDCALL" 2>&1 || true
    echo "  Retrying in 30s..."
    sleep 30
    exec "$0" "$@"
fi

# Step 4: Wait for SCO nodes
echo ""
echo "[4] Waiting for SCO nodes..."
sleep 2
SCO_SINK_NAME=""
SCO_SOURCE_NAME=""
for i in $(seq 1 15); do
    # Use pw-dump which we know works
    SCO_SINK_NAME=$(pw-dump 2>&1 | python3 -c "
import json, sys
data = json.load(sys.stdin)
for obj in data:
    p = obj.get('info', {}).get('props', {})
    if p.get('factory.name') == 'api.bluez5.sco.sink':
        print(p.get('node.name', ''))
        break
" 2>/dev/null)
    SCO_SOURCE_NAME=$(pw-dump 2>&1 | python3 -c "
import json, sys
data = json.load(sys.stdin)
for obj in data:
    p = obj.get('info', {}).get('props', {})
    if p.get('factory.name') == 'api.bluez5.sco.source':
        print(p.get('node.name', ''))
        break
" 2>/dev/null)

    if [ -n "$SCO_SINK_NAME" ] && [ -n "$SCO_SOURCE_NAME" ]; then
        echo "  SCO sink:   $SCO_SINK_NAME"
        echo "  SCO source: $SCO_SOURCE_NAME"
        break
    fi
    echo "  Waiting for SCO nodes... ($i/15)"
    sleep 1
done

if [ -z "$SCO_SINK_NAME" ] || [ -z "$SCO_SOURCE_NAME" ]; then
    echo "  WARNING: SCO nodes not found, trying anyway with expected names"
    SCO_SINK_NAME="bluez_output.64_A2_F9_B8_21_94.1"
    SCO_SOURCE_NAME="bluez_input.64_A2_F9_B8_21_94.0"
fi

# Step 5: Play and record
echo ""
echo "[5] Playing audio + recording..."

# Record from SCO source
pw-record --target="$SCO_SOURCE_NAME" --rate=16000 --channels=1 "$REC" &
REC_PID=$!
echo "  Recording started (PID: $REC_PID)"

sleep 1

# Play into SCO sink
echo "  Playing audio into call..."
pw-play --target="$SCO_SINK_NAME" "$WAV" 2>&1 || {
    echo "  pw-play failed, trying paplay..."
    paplay --device="$SCO_SINK_NAME" "$WAV" 2>&1 || echo "  paplay also failed"
}
echo "  Playback finished"

echo "  Recording 5 more seconds for response..."
sleep 5

kill $REC_PID 2>/dev/null || true
wait $REC_PID 2>/dev/null || true

# Step 6: Hang up
echo ""
echo "[6] Hanging up..."
adb shell "input keyevent KEYCODE_ENDCALL" 2>&1

echo ""
echo "=== DONE ==="
echo "Recording: $REC"

# Analyze recording
python3 -c "
import wave, struct
w = wave.open('$REC', 'rb')
frames = w.getnframes()
rate = w.getframerate()
dur = frames / rate
data = w.readframes(frames)
samples = struct.unpack(f'<{frames}h', data)
max_amp = max(abs(s) for s in samples)
avg_amp = sum(abs(s) for s in samples) / len(samples)
print(f'Recording: {dur:.1f}s, max_amp={max_amp}, avg_amp={avg_amp:.1f}')
if max_amp > 500:
    print('Audio detected!')
else:
    print('Recording appears silent.')
w.close()
" 2>/dev/null || true
