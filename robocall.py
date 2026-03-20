#!/usr/bin/env python3
"""
robocall.py - Low-level SDK for making phone calls via ADB + Bluetooth HFP.

Uses an Android phone connected via wireless ADB and Bluetooth to make calls,
play audio to the callee, and record their responses.

Usage:
    from robocall import Robocall

    rc = Robocall()
    call = rc.call("+1234567890")
    call.play("message.wav")
    recording = call.record(duration=10)
    call.hangup()
"""

import json
import logging
import os
import re
import signal
import subprocess
import tempfile
import threading
import time
import uuid
import wave
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Optional

log = logging.getLogger("robocall")


class CallStatus(Enum):
    QUEUED = "queued"
    DIALING = "dialing"
    RINGING = "ringing"
    IN_PROGRESS = "in-progress"
    COMPLETED = "completed"
    FAILED = "failed"
    BUSY = "busy"
    NO_ANSWER = "no-answer"
    CANCELED = "canceled"


class RobocallError(Exception):
    pass


class NoSignalError(RobocallError):
    pass


class CallFailedError(RobocallError):
    pass


class ADBError(RobocallError):
    pass


class AudioError(RobocallError):
    pass


def _run(cmd: list[str], timeout: int = 10) -> subprocess.CompletedProcess:
    """Run a command and return the result."""
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def _adb(*args: str, timeout: int = 10) -> str:
    """Run an ADB command and return stdout."""
    result = _run(["adb", *args], timeout=timeout)
    if result.returncode != 0:
        raise ADBError(f"adb {' '.join(args)}: {result.stderr.strip()}")
    return result.stdout.strip()


@dataclass
class SCONodes:
    """PipeWire SCO node names discovered during a call."""
    sink: str  # PC → phone (what the callee hears)
    source: str  # phone → PC (what the callee says)


@dataclass
class Recording:
    """A call recording."""
    path: str
    duration: float = 0.0
    max_amplitude: int = 0
    avg_amplitude: float = 0.0

    def analyze(self):
        """Analyze the recording for audio content."""
        try:
            import struct
            w = wave.open(self.path, "rb")
            frames = w.getnframes()
            rate = w.getframerate()
            self.duration = frames / rate
            data = w.readframes(frames)
            channels = w.getnchannels()
            sample_width = w.getsampwidth()
            w.close()

            if sample_width == 2:
                samples = struct.unpack(f"<{frames * channels}h", data)
            else:
                return

            self.max_amplitude = max(abs(s) for s in samples) if samples else 0
            self.avg_amplitude = sum(abs(s) for s in samples) / len(samples) if samples else 0
        except Exception as e:
            log.warning(f"Failed to analyze recording: {e}")

    @property
    def has_audio(self) -> bool:
        """Whether the recording contains non-silence."""
        if self.max_amplitude == 0:
            self.analyze()
        return self.max_amplitude > 500


class Call:
    """Represents an active or completed phone call."""

    def __init__(self, number: str, phone_mac: str, call_id: str):
        self.sid = str(uuid.uuid4())
        self.number = number
        self.phone_mac = phone_mac
        self.tc_id = call_id  # Android telecom call ID (e.g. "TC@35")
        self.status = CallStatus.QUEUED
        self.direction = "outbound"
        self.start_time: Optional[datetime] = None
        self.end_time: Optional[datetime] = None
        self.duration: Optional[float] = None
        self.recordings: list[Recording] = []
        self.sco_nodes: Optional[SCONodes] = None
        self._record_proc: Optional[subprocess.Popen] = None

    def _find_sco_nodes(self, timeout: int = 15) -> SCONodes:
        """Find PipeWire SCO nodes for this Bluetooth device."""
        mac_underscored = self.phone_mac.replace(":", "_")

        for i in range(timeout):
            try:
                result = _run(["pw-dump"], timeout=5)
                data = json.loads(result.stdout)
            except (json.JSONDecodeError, subprocess.TimeoutExpired):
                time.sleep(1)
                continue

            sink_name = None
            source_name = None
            for obj in data:
                props = obj.get("info", {}).get("props", {})
                factory = props.get("factory.name", "")
                node_name = props.get("node.name", "")
                if factory == "api.bluez5.sco.sink" and mac_underscored in node_name:
                    sink_name = node_name
                elif factory == "api.bluez5.sco.source" and mac_underscored in node_name:
                    source_name = node_name

            if sink_name and source_name:
                nodes = SCONodes(sink=sink_name, source=source_name)
                log.info(f"SCO nodes found: sink={sink_name}, source={source_name}")
                return nodes

            log.debug(f"Waiting for SCO nodes... ({i + 1}/{timeout})")
            time.sleep(1)

        raise AudioError("SCO audio nodes did not appear")

    def wait_for_connection(self, timeout: int = 90) -> bool:
        """Wait for the call to be answered."""
        self.status = CallStatus.RINGING
        tc_num = self.tc_id.replace("TC@", "")

        for i in range(timeout):
            try:
                dump = _adb("shell", "dumpsys telecom", timeout=5)
            except ADBError:
                time.sleep(1)
                continue

            # Find our call entry
            pattern = rf"Call TC@{tc_num}:\s*\{{(.*?)\}}"
            match = re.search(pattern, dump, re.DOTALL)
            if not match:
                time.sleep(1)
                continue

            block = match.group(1)

            # Check if connected (startTime > 0, endTime = 0)
            if re.search(r"startTime:\s+[1-9]", block) and re.search(r"endTime:\s+0\b", block):
                self.status = CallStatus.IN_PROGRESS
                self.start_time = datetime.now()
                log.info(f"Call connected after {i + 1}s")
                return True

            # Check if failed (endTime > 0)
            if re.search(r"endTime:\s+[1-9]", block):
                reason_match = re.search(r"callTerminationReason:.*?Reason:\s*\(([^)]*)\)", dump)
                reason = reason_match.group(1) if reason_match else "unknown"
                self.status = CallStatus.FAILED

                if any(kw in reason.lower() for kw in ("network", "radio", "service", "out_of_service")):
                    raise NoSignalError(f"Call failed: {reason}")
                elif "busy" in reason.lower():
                    self.status = CallStatus.BUSY
                    raise CallFailedError(f"Line busy: {reason}")
                else:
                    raise CallFailedError(f"Call failed: {reason}")

            time.sleep(1)

        self.status = CallStatus.NO_ANSWER
        raise CallFailedError("Call was not answered within timeout")

    def play(self, audio_file: str, block: bool = True) -> subprocess.Popen:
        """Play an audio file to the callee.

        The audio file should be mono WAV. If it's not mono 16kHz,
        it will be auto-converted.
        """
        if self.status != CallStatus.IN_PROGRESS:
            raise RobocallError(f"Cannot play audio: call is {self.status.value}")

        if not self.sco_nodes:
            self.sco_nodes = self._find_sco_nodes()

        audio_file = self._ensure_mono_wav(audio_file)

        log.info(f"Playing {audio_file} into call...")
        proc = subprocess.Popen(
            ["pw-play", f"--target={self.sco_nodes.sink}", audio_file],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        if block:
            proc.wait()
            if proc.returncode != 0:
                stderr = proc.stderr.read().decode() if proc.stderr else ""
                raise AudioError(f"pw-play failed (rc={proc.returncode}): {stderr}")
            log.info("Playback finished")

        return proc

    def record(self, duration: Optional[float] = None, output: Optional[str] = None) -> Recording:
        """Record audio from the callee.

        Args:
            duration: Seconds to record. If None, records until stop_recording().
            output: Output file path. Auto-generated if not provided.
        """
        if self.status != CallStatus.IN_PROGRESS:
            raise RobocallError(f"Cannot record: call is {self.status.value}")

        if not self.sco_nodes:
            self.sco_nodes = self._find_sco_nodes()

        if output is None:
            output = os.path.join(
                tempfile.gettempdir(),
                f"robocall_rec_{datetime.now().strftime('%Y%m%d_%H%M%S')}.wav",
            )

        log.info(f"Recording to {output}...")
        self._record_proc = subprocess.Popen(
            ["pw-record", f"--target={self.sco_nodes.source}",
             "--rate=16000", "--channels=1", output],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        recording = Recording(path=output)
        self.recordings.append(recording)

        if duration is not None:
            time.sleep(duration)
            self.stop_recording()
            recording.analyze()

        return recording

    def stop_recording(self) -> Optional[Recording]:
        """Stop the current recording."""
        if self._record_proc and self._record_proc.poll() is None:
            self._record_proc.send_signal(signal.SIGINT)
            try:
                self._record_proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self._record_proc.kill()
            self._record_proc = None
            log.info("Recording stopped")

            if self.recordings:
                self.recordings[-1].analyze()
                return self.recordings[-1]
        return None

    def play_and_record(
        self,
        audio_file: str,
        extra_record_seconds: float = 5.0,
        output: Optional[str] = None,
    ) -> Recording:
        """Play audio and record the response simultaneously."""
        recording = self.record(output=output)
        time.sleep(0.5)  # let recording start
        self.play(audio_file, block=True)
        time.sleep(extra_record_seconds)
        self.stop_recording()
        return recording

    def hangup(self):
        """End the call."""
        self.stop_recording()
        try:
            _adb("shell", "input keyevent KEYCODE_ENDCALL")
        except ADBError:
            pass
        self.status = CallStatus.COMPLETED
        self.end_time = datetime.now()
        if self.start_time:
            self.duration = (self.end_time - self.start_time).total_seconds()
        log.info(f"Call ended (duration: {self.duration:.1f}s)" if self.duration else "Call ended")

    def _ensure_mono_wav(self, audio_file: str) -> str:
        """Convert audio to mono 16kHz WAV if needed."""
        try:
            w = wave.open(audio_file, "rb")
            channels = w.getnchannels()
            rate = w.getframerate()
            w.close()
        except Exception:
            channels, rate = 2, 48000  # assume needs conversion

        if channels == 1 and rate == 16000:
            return audio_file

        # Convert
        base = Path(audio_file)
        converted = str(base.parent / f"{base.stem}_mono16k.wav")
        if os.path.exists(converted):
            return converted

        log.info(f"Converting {audio_file} to mono 16kHz...")
        result = _run(
            ["ffmpeg", "-y", "-i", audio_file, "-ac", "1", "-ar", "16000", converted],
            timeout=30,
        )
        if result.returncode != 0:
            raise AudioError(f"ffmpeg conversion failed: {result.stderr}")
        return converted

    def __repr__(self):
        return f"Call(sid={self.sid[:8]}..., to={self.number}, status={self.status.value})"


class Robocall:
    """Main client for making robocalls via ADB + Bluetooth HFP."""

    def __init__(
        self,
        phone_ip: str = "192.168.0.29",
        phone_mac: str = "64:A2:F9:B8:21:94",
        max_signal_retries: int = 20,
        signal_retry_delay: float = 30.0,
        max_call_retries: int = 5,
    ):
        self.phone_ip = phone_ip
        self.phone_mac = phone_mac
        self.max_signal_retries = max_signal_retries
        self.signal_retry_delay = signal_retry_delay
        self.max_call_retries = max_call_retries
        self.calls: dict[str, Call] = {}

    def check_adb(self) -> bool:
        """Check if ADB is connected."""
        try:
            output = _adb("devices")
            return self.phone_ip in output and "device" in output
        except (ADBError, subprocess.TimeoutExpired):
            return False

    def check_bluetooth(self) -> bool:
        """Check if the phone is connected via Bluetooth."""
        result = _run(["bluetoothctl", "info", self.phone_mac])
        return "Connected: yes" in result.stdout

    def check_signal(self) -> bool:
        """Check if the phone has cellular signal."""
        try:
            dump = _adb("shell", "dumpsys telephony.registry")
            return "mVoiceRegState=0(IN_SERVICE)" in dump
        except (ADBError, subprocess.TimeoutExpired):
            return False

    def wait_for_signal(self) -> bool:
        """Wait until the phone has cellular signal."""
        for i in range(self.max_signal_retries):
            if self.check_signal():
                return True
            log.info(f"No signal, waiting {self.signal_retry_delay}s... ({i + 1}/{self.max_signal_retries})")
            time.sleep(self.signal_retry_delay)
        return False

    def _get_latest_tc_id(self) -> int:
        """Get the latest telecom call ID number."""
        try:
            dump = _adb("shell", "dumpsys telecom")
            matches = re.findall(r"Call TC@(\d+)", dump)
            return int(matches[-1]) if matches else 0
        except (ADBError, IndexError):
            return 0

    def call(
        self,
        to: str,
        audio_file: Optional[str] = None,
        record: bool = False,
        record_duration: Optional[float] = None,
        extra_record_seconds: float = 5.0,
        recording_output: Optional[str] = None,
        timeout: int = 90,
    ) -> Call:
        """Make a phone call.

        Args:
            to: Phone number to call (E.164 format).
            audio_file: WAV file to play when call connects. If None, no audio is played.
            record: Whether to record the call.
            record_duration: How long to record (seconds). If None, records until hangup.
            extra_record_seconds: Extra recording time after audio playback finishes.
            recording_output: Output path for recording.
            timeout: Seconds to wait for the call to be answered.

        Returns:
            Call object.

        Raises:
            ADBError: If ADB is not connected.
            NoSignalError: If there's no cellular signal after retries.
            CallFailedError: If the call fails for non-signal reasons.
        """
        if not self.check_adb():
            raise ADBError("ADB not connected")

        for attempt in range(self.max_call_retries):
            try:
                return self._attempt_call(
                    to, audio_file, record, record_duration,
                    extra_record_seconds, recording_output, timeout,
                )
            except NoSignalError:
                if attempt < self.max_call_retries - 1:
                    log.warning(f"Signal lost, waiting {self.signal_retry_delay}s before retry "
                                f"({attempt + 1}/{self.max_call_retries})")
                    time.sleep(self.signal_retry_delay)
                    self.wait_for_signal()
                else:
                    raise

        raise CallFailedError("Max retries exceeded")  # unreachable but satisfies type checker

    def _attempt_call(
        self,
        to: str,
        audio_file: Optional[str],
        record: bool,
        record_duration: Optional[float],
        extra_record_seconds: float,
        recording_output: Optional[str],
        timeout: int,
    ) -> Call:
        """Single attempt to make a call."""
        # Wait for signal
        if not self.wait_for_signal():
            raise NoSignalError("No cellular signal after waiting")

        # Get current call ID to detect the new one
        prev_id = self._get_latest_tc_id()
        next_id = prev_id + 1

        # Dial
        log.info(f"Dialing {to}...")
        _adb("shell", f"am start -a android.intent.action.CALL -d tel:{to}")

        call = Call(number=to, phone_mac=self.phone_mac, call_id=f"TC@{next_id}")
        call.status = CallStatus.DIALING
        self.calls[call.sid] = call

        # Wait for connection
        call.wait_for_connection(timeout=timeout)

        # Wait for SCO nodes
        time.sleep(2)
        call.sco_nodes = call._find_sco_nodes()

        # Play and/or record
        if audio_file and record:
            call.play_and_record(audio_file, extra_record_seconds, recording_output)
        elif audio_file:
            call.play(audio_file)
        elif record:
            call.record(duration=record_duration, output=recording_output)

        return call

    def hangup_all(self):
        """Hang up all active calls."""
        try:
            _adb("shell", "input keyevent KEYCODE_ENDCALL")
        except ADBError:
            pass
        for call in self.calls.values():
            if call.status == CallStatus.IN_PROGRESS:
                call.status = CallStatus.COMPLETED
                call.end_time = datetime.now()

    def __repr__(self):
        return f"Robocall(phone={self.phone_ip}, mac={self.phone_mac})"


# === CLI ===

if __name__ == "__main__":
    import argparse

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    parser = argparse.ArgumentParser(description="Make a robocall via ADB + Bluetooth HFP")
    parser.add_argument("number", nargs="?", default="+1234567890", help="Phone number to call")
    parser.add_argument("-a", "--audio", default=None, help="WAV file to play")
    parser.add_argument("-r", "--record", action="store_true", help="Record the call")
    parser.add_argument("-d", "--duration", type=float, default=None, help="Record duration (seconds)")
    parser.add_argument("-o", "--output", default=None, help="Recording output path")
    parser.add_argument("--extra", type=float, default=5.0, help="Extra recording time after playback")
    parser.add_argument("--hangup", action="store_true", default=True, help="Hang up after playback")
    parser.add_argument("--no-hangup", action="store_false", dest="hangup", help="Don't hang up after playback")
    args = parser.parse_args()

    rc = Robocall()

    print(f"ADB: {'OK' if rc.check_adb() else 'NOT CONNECTED'}")
    print(f"Bluetooth: {'OK' if rc.check_bluetooth() else 'NOT CONNECTED'}")
    print(f"Signal: {'OK' if rc.check_signal() else 'NO SIGNAL'}")

    call = rc.call(
        to=args.number,
        audio_file=args.audio,
        record=args.record,
        record_duration=args.duration,
        recording_output=args.output,
        extra_record_seconds=args.extra,
    )

    if not args.audio and not args.record:
        print(f"Call connected: {call}")
        print("Press Enter to hang up...")
        input()

    if args.hangup:
        call.hangup()

    if call.recordings:
        for rec in call.recordings:
            print(f"Recording: {rec.path} ({rec.duration:.1f}s, "
                  f"{'has audio' if rec.has_audio else 'silent'})")
