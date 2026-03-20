#!/bin/bash
# Make a call, wait for it to be ACTIVE, then play audio and record

NUMBER="+1234567890"
WAV="/home/faris/robocall/when-will-you-learn-script.wav"
REC="/home/faris/robocall/call_recording_test.wav"
USB_SINK="alsa_output.usb-C-Media_Electronics_Inc._USB_Audio_Device-00.analog-stereo"
USB_MONITOR="alsa_output.usb-C-Media_Electronics_Inc._USB_Audio_Device-00.analog-stereo.monitor"

echo "=== Dialing $NUMBER ==="
adb shell "am start -a android.intent.action.CALL -d tel:$NUMBER"

echo "=== Waiting for call to become ACTIVE ==="
for i in $(seq 1 60); do
    state=$(adb shell "dumpsys telecom" 2>&1 | grep -c "state=ACTIVE")
    if [ "$state" -gt 0 ]; then
        echo "Call is ACTIVE after ${i}s"
        break
    fi
    if [ "$i" -eq 60 ]; then
        echo "Timed out waiting for call to connect"
        adb shell "input keyevent KEYCODE_ENDCALL"
        exit 1
    fi
    sleep 1
done

# Give a moment for audio to settle
sleep 2

echo "=== Starting recording from USB monitor ==="
parecord --device="$USB_MONITOR" \
    --file-format=wav --rate=16000 --channels=1 \
    "$REC" &
REC_PID=$!

echo "=== Playing audio into call ==="
paplay --device="$USB_SINK" "$WAV"
echo "=== Playback done, recording 5 more seconds ==="
sleep 5

kill $REC_PID 2>/dev/null
echo "=== Recording saved to $REC ==="

echo "=== Hanging up ==="
adb shell "input keyevent KEYCODE_ENDCALL"
echo "=== DONE ==="
