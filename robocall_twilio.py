#!/usr/bin/env python3
"""
robocall_twilio.py - Twilio-compatible API wrapper around robocall.py.

Drop-in replacement for Twilio's Python SDK for making calls.
Uses a real Android phone via ADB + Bluetooth HFP instead of Twilio's cloud.

Usage (mirrors Twilio's API):

    from robocall_twilio import Client

    client = Client()

    # Simple call with TwiML
    call = client.calls.create(
        to="+1234567890",
        twiml='<Response><Say>Hello world</Say></Response>',
    )
    print(call.sid, call.status)

    # Call with audio file
    call = client.calls.create(
        to="+1234567890",
        url="file:///path/to/audio.wav",
    )

    # Check call status
    call = client.calls(call.sid).fetch()
    print(call.status, call.duration)

    # Hang up
    client.calls(call.sid).update(status="completed")
"""

import logging
import os
import re
import subprocess
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional
from xml.etree import ElementTree

from robocall import (
    Call as RawCall,
    CallStatus,
    Robocall,
    RobocallError,
    Recording,
)

log = logging.getLogger("robocall.twilio")


# === TwiML ===


class TwiMLError(Exception):
    pass


class VoiceResponse:
    """Build TwiML responses (subset compatible with Twilio's VoiceResponse)."""

    def __init__(self):
        self._verbs: list[dict] = []

    def say(self, text: str, voice: str = "default", language: str = "en-US", loop: int = 1) -> "VoiceResponse":
        self._verbs.append({"verb": "Say", "text": text, "voice": voice, "language": language, "loop": loop})
        return self

    def play(self, url: str, loop: int = 1) -> "VoiceResponse":
        self._verbs.append({"verb": "Play", "url": url, "loop": loop})
        return self

    def pause(self, length: int = 1) -> "VoiceResponse":
        self._verbs.append({"verb": "Pause", "length": length})
        return self

    def record(
        self,
        timeout: int = 5,
        max_length: int = 3600,
        play_beep: bool = True,
        trim: str = "trim-silence",
        recording_status_callback: Optional[str] = None,
    ) -> "VoiceResponse":
        self._verbs.append({
            "verb": "Record",
            "timeout": timeout,
            "max_length": max_length,
            "play_beep": play_beep,
            "trim": trim,
            "recording_status_callback": recording_status_callback,
        })
        return self

    def hangup(self) -> "VoiceResponse":
        self._verbs.append({"verb": "Hangup"})
        return self

    def to_xml(self) -> str:
        lines = ["<Response>"]
        for v in self._verbs:
            verb = v["verb"]
            if verb == "Say":
                attrs = f' voice="{v["voice"]}" language="{v["language"]}"'
                if v["loop"] > 1:
                    attrs += f' loop="{v["loop"]}"'
                lines.append(f"  <Say{attrs}>{v['text']}</Say>")
            elif verb == "Play":
                loop_attr = f' loop="{v["loop"]}"' if v["loop"] > 1 else ""
                lines.append(f"  <Play{loop_attr}>{v['url']}</Play>")
            elif verb == "Pause":
                lines.append(f'  <Pause length="{v["length"]}"/>')
            elif verb == "Record":
                attrs = f' timeout="{v["timeout"]}" maxLength="{v["max_length"]}"'
                if v["play_beep"]:
                    attrs += ' playBeep="true"'
                lines.append(f"  <Record{attrs}/>")
            elif verb == "Hangup":
                lines.append("  <Hangup/>")
        lines.append("</Response>")
        return "\n".join(lines)

    def __str__(self):
        return self.to_xml()


def _tts_to_wav(text: str, output: str, voice: str = "default", lang: str = "en-US") -> str:
    """Convert text to speech WAV file.

    Tries multiple TTS backends in order:
    1. Google Cloud TTS (if gcloud configured)
    2. piper (fast local TTS)
    3. espeak-ng (fallback)
    """
    # Try piper first (fast, good quality, local)
    try:
        result = subprocess.run(
            ["which", "piper"], capture_output=True, text=True
        )
        if result.returncode == 0:
            proc = subprocess.run(
                ["piper", "--model", "en_US-lessac-medium", "--output_file", output],
                input=text, capture_output=True, text=True, timeout=30,
            )
            if proc.returncode == 0 and os.path.exists(output):
                # Convert to mono 16kHz
                mono = output.replace(".wav", "_mono16k.wav")
                subprocess.run(
                    ["ffmpeg", "-y", "-i", output, "-ac", "1", "-ar", "16000", mono],
                    capture_output=True, timeout=30,
                )
                if os.path.exists(mono):
                    os.replace(mono, output)
                return output
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass

    # Try espeak-ng (always available on most Linux)
    try:
        subprocess.run(
            ["espeak-ng", "-w", output, "-v", lang, text],
            capture_output=True, timeout=30,
        )
        if os.path.exists(output):
            # Convert to mono 16kHz
            mono = output.replace(".wav", "_mono16k.wav")
            subprocess.run(
                ["ffmpeg", "-y", "-i", output, "-ac", "1", "-ar", "16000", mono],
                capture_output=True, timeout=30,
            )
            if os.path.exists(mono):
                os.replace(mono, output)
            return output
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass

    raise TwiMLError(f"No TTS engine available. Install piper or espeak-ng.")


# === Twilio-compatible Call resource ===


class CallInstance:
    """Twilio-compatible Call resource."""

    def __init__(self, raw_call: Optional[RawCall] = None):
        self._raw = raw_call
        self.sid: str = raw_call.sid if raw_call else str(uuid.uuid4())
        self.account_sid: str = "LOCAL"
        self.to: str = raw_call.number if raw_call else ""
        self.from_: str = "local-phone"
        self.status: str = raw_call.status.value if raw_call else "queued"
        self.direction: str = "outbound-api"
        self.start_time: Optional[datetime] = raw_call.start_time if raw_call else None
        self.end_time: Optional[datetime] = raw_call.end_time if raw_call else None
        self.duration: Optional[str] = str(int(raw_call.duration)) if raw_call and raw_call.duration else None
        self.price: Optional[str] = "0.00"
        self.price_unit: str = "USD"
        self.uri: str = f"/calls/{self.sid}"
        self.date_created: datetime = datetime.now()
        self.date_updated: datetime = datetime.now()
        self.recordings: list[Recording] = raw_call.recordings if raw_call else []

    def _sync(self):
        """Sync state from the raw call."""
        if self._raw:
            self.status = self._raw.status.value
            self.start_time = self._raw.start_time
            self.end_time = self._raw.end_time
            if self._raw.duration:
                self.duration = str(int(self._raw.duration))
            self.recordings = self._raw.recordings
            self.date_updated = datetime.now()

    def __repr__(self):
        return f"<CallInstance sid={self.sid[:8]}... to={self.to} status={self.status}>"


class CallContext:
    """Twilio-compatible call context for fetch/update operations."""

    def __init__(self, sid: str, calls_resource: "CallsResource"):
        self._sid = sid
        self._calls = calls_resource

    def fetch(self) -> CallInstance:
        """Fetch current call status."""
        raw = self._calls._call_map.get(self._sid)
        if not raw:
            raise RobocallError(f"Call {self._sid} not found")
        instance = CallInstance(raw)
        instance._sync()
        return instance

    def update(self, status: Optional[str] = None, **kwargs) -> CallInstance:
        """Update call (e.g., hang up by setting status='completed')."""
        raw = self._calls._call_map.get(self._sid)
        if not raw:
            raise RobocallError(f"Call {self._sid} not found")

        if status == "completed":
            raw.hangup()
        elif status == "canceled":
            raw.hangup()
            raw.status = CallStatus.CANCELED

        instance = CallInstance(raw)
        instance._sync()
        return instance


class CallsResource:
    """Twilio-compatible calls resource. Usage: client.calls.create(...)"""

    def __init__(self, robocall: Robocall):
        self._rc = robocall
        self._call_map: dict[str, RawCall] = {}

    def __call__(self, sid: str) -> CallContext:
        """Get a call context for fetch/update. Usage: client.calls(sid).fetch()"""
        return CallContext(sid, self)

    def create(
        self,
        to: str,
        from_: str = "local-phone",
        url: Optional[str] = None,
        twiml: Optional[str] = None,
        status_callback: Optional[str] = None,
        timeout: int = 90,
        record: bool = False,
        recording_status_callback: Optional[str] = None,
        **kwargs,
    ) -> CallInstance:
        """Create (make) a phone call.

        Args:
            to: Phone number to call (E.164 format).
            from_: Ignored (uses local phone). Kept for API compatibility.
            url: URL or file:// path to audio/TwiML to play.
            twiml: TwiML XML string defining call flow.
            status_callback: Ignored. Kept for API compatibility.
            timeout: Seconds to wait for answer.
            record: Whether to record the call.

        Returns:
            CallInstance with call details.
        """
        audio_file = None
        twiml_verbs = None
        should_record = record
        should_hangup = True

        # Parse TwiML if provided
        if twiml:
            if isinstance(twiml, VoiceResponse):
                twiml = str(twiml)
            twiml_verbs = self._parse_twiml(twiml)

        # Handle URL (file:// paths or audio URLs)
        if url and not twiml:
            if url.startswith("file://"):
                audio_file = url[7:]
            else:
                # Download the URL to a temp file
                audio_file = self._download_url(url)

        # Execute TwiML verbs or simple audio playback
        if twiml_verbs:
            return self._execute_twiml(to, twiml_verbs, timeout, should_record)
        else:
            raw_call = self._rc.call(
                to=to,
                audio_file=audio_file,
                record=should_record,
                timeout=timeout,
            )
            if audio_file:
                raw_call.hangup()
            self._call_map[raw_call.sid] = raw_call
            return CallInstance(raw_call)

    def _parse_twiml(self, twiml_str: str) -> list[dict]:
        """Parse TwiML XML into verb list."""
        try:
            root = ElementTree.fromstring(twiml_str)
        except ElementTree.ParseError as e:
            raise TwiMLError(f"Invalid TwiML: {e}")

        verbs = []
        for elem in root:
            tag = elem.tag
            if tag == "Say":
                verbs.append({
                    "verb": "Say",
                    "text": elem.text or "",
                    "voice": elem.get("voice", "default"),
                    "language": elem.get("language", "en-US"),
                    "loop": int(elem.get("loop", "1")),
                })
            elif tag == "Play":
                verbs.append({
                    "verb": "Play",
                    "url": elem.text or "",
                    "loop": int(elem.get("loop", "1")),
                })
            elif tag == "Pause":
                verbs.append({
                    "verb": "Pause",
                    "length": int(elem.get("length", "1")),
                })
            elif tag == "Record":
                verbs.append({
                    "verb": "Record",
                    "timeout": int(elem.get("timeout", "5")),
                    "max_length": int(elem.get("maxLength", "3600")),
                })
            elif tag == "Hangup":
                verbs.append({"verb": "Hangup"})
        return verbs

    def _execute_twiml(
        self, to: str, verbs: list[dict], timeout: int, record: bool
    ) -> CallInstance:
        """Execute TwiML verbs on a call."""
        # Make the call first (no audio, no record yet)
        raw_call = self._rc.call(to=to, timeout=timeout)
        self._call_map[raw_call.sid] = raw_call

        # Start call-level recording if requested
        if record:
            raw_call.record()

        try:
            for verb in verbs:
                if raw_call.status != CallStatus.IN_PROGRESS:
                    break

                if verb["verb"] == "Say":
                    wav_path = os.path.join(
                        tempfile.gettempdir(),
                        f"tts_{uuid.uuid4().hex[:8]}.wav",
                    )
                    _tts_to_wav(verb["text"], wav_path, verb["voice"], verb["language"])
                    for _ in range(verb.get("loop", 1)):
                        raw_call.play(wav_path)

                elif verb["verb"] == "Play":
                    url = verb["url"]
                    if url.startswith("file://"):
                        audio_path = url[7:]
                    elif url.startswith("/") or url.startswith("."):
                        audio_path = url
                    else:
                        audio_path = self._download_url(url)
                    for _ in range(verb.get("loop", 1)):
                        raw_call.play(audio_path)

                elif verb["verb"] == "Pause":
                    time.sleep(verb["length"])

                elif verb["verb"] == "Record":
                    raw_call.record(
                        duration=verb.get("max_length", 30),
                    )

                elif verb["verb"] == "Hangup":
                    raw_call.hangup()
                    break

        except Exception as e:
            log.error(f"TwiML execution error: {e}")
        finally:
            if raw_call.status == CallStatus.IN_PROGRESS:
                raw_call.hangup()

        instance = CallInstance(raw_call)
        instance._sync()
        return instance

    def _download_url(self, url: str) -> str:
        """Download a URL to a temp WAV file."""
        output = os.path.join(tempfile.gettempdir(), f"robocall_dl_{uuid.uuid4().hex[:8]}.wav")
        result = subprocess.run(
            ["curl", "-sL", "-o", output, url],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            raise RobocallError(f"Failed to download {url}: {result.stderr}")
        return output

    def list(self, status: Optional[str] = None, to: Optional[str] = None, limit: int = 50) -> list[CallInstance]:
        """List calls, optionally filtered by status or number."""
        results = []
        for raw in self._call_map.values():
            if status and raw.status.value != status:
                continue
            if to and raw.number != to:
                continue
            instance = CallInstance(raw)
            instance._sync()
            results.append(instance)
        results.sort(key=lambda c: c.date_created, reverse=True)
        return results[:limit]


# === Twilio-compatible Client ===


class Client:
    """Twilio-compatible client. Drop-in replacement for twilio.rest.Client.

    Usage:
        client = Client()
        call = client.calls.create(to="+1234567890", twiml="<Response><Say>Hello</Say></Response>")
    """

    def __init__(
        self,
        account_sid: Optional[str] = None,
        auth_token: Optional[str] = None,
        phone_ip: str = "192.168.0.29",
        phone_mac: str = "64:A2:F9:B8:21:94",
        **kwargs,
    ):
        # account_sid and auth_token are accepted but ignored (local phone, no cloud)
        self.account_sid = account_sid or "LOCAL"
        self._rc = Robocall(phone_ip=phone_ip, phone_mac=phone_mac, **kwargs)
        self.calls = CallsResource(self._rc)

    def __repr__(self):
        return f"<Client account_sid={self.account_sid}>"


# === CLI ===

if __name__ == "__main__":
    import argparse

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    parser = argparse.ArgumentParser(description="Twilio-compatible robocall CLI")
    parser.add_argument("number", help="Phone number to call")
    parser.add_argument("--say", help="Text to speak (TTS)")
    parser.add_argument("--play", help="Audio file to play")
    parser.add_argument("--record", action="store_true", help="Record the call")
    parser.add_argument("--twiml", help="TwiML XML string")
    args = parser.parse_args()

    client = Client()

    if args.twiml:
        call = client.calls.create(to=args.number, twiml=args.twiml)
    elif args.say:
        twiml = VoiceResponse().say(args.say)
        call = client.calls.create(to=args.number, twiml=str(twiml), record=args.record)
    elif args.play:
        call = client.calls.create(to=args.number, url=f"file://{args.play}", record=args.record)
    else:
        call = client.calls.create(to=args.number, record=args.record)

    print(f"Call SID: {call.sid}")
    print(f"Status: {call.status}")
    print(f"Duration: {call.duration}")
    if call.recordings:
        for r in call.recordings:
            print(f"Recording: {r.path} ({r.duration:.1f}s, {'has audio' if r.has_audio else 'silent'})")
