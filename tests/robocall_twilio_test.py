"""Tests for robocall_twilio.py - the Twilio-compatible wrapper."""

import os
import subprocess
import sys
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from robocall import Call, CallStatus, Recording
from robocall_twilio import (
    Client,
    CallsResource,
    CallInstance,
    CallContext,
    VoiceResponse,
    TwiMLError,
    _tts_to_wav,
)


# === VoiceResponse (TwiML builder) tests ===

def test_voice_response_say():
    vr = VoiceResponse()
    vr.say("Hello world")
    xml = vr.to_xml()
    assert "<Say" in xml
    assert "Hello world" in xml
    assert "<Response>" in xml
    assert "</Response>" in xml


def test_voice_response_play():
    vr = VoiceResponse()
    vr.play("file:///tmp/audio.wav")
    xml = vr.to_xml()
    assert "<Play>" in xml
    assert "file:///tmp/audio.wav" in xml


def test_voice_response_pause():
    vr = VoiceResponse()
    vr.pause(length=3)
    xml = vr.to_xml()
    assert 'Pause length="3"' in xml


def test_voice_response_record():
    vr = VoiceResponse()
    vr.record(timeout=10, max_length=60)
    xml = vr.to_xml()
    assert "<Record" in xml
    assert 'timeout="10"' in xml
    assert 'maxLength="60"' in xml


def test_voice_response_hangup():
    vr = VoiceResponse()
    vr.hangup()
    xml = vr.to_xml()
    assert "<Hangup/>" in xml


def test_voice_response_chaining():
    vr = VoiceResponse()
    vr.say("Hello").pause(2).play("file:///tmp/a.wav").hangup()
    xml = vr.to_xml()
    assert "<Say" in xml
    assert "<Pause" in xml
    assert "<Play>" in xml
    assert "<Hangup/>" in xml


def test_voice_response_str():
    vr = VoiceResponse()
    vr.say("Test")
    assert str(vr) == vr.to_xml()


def test_voice_response_loop():
    vr = VoiceResponse()
    vr.say("Repeat", loop=3)
    xml = vr.to_xml()
    assert 'loop="3"' in xml


def test_voice_response_no_loop_attr_when_1():
    vr = VoiceResponse()
    vr.say("Once", loop=1)
    xml = vr.to_xml()
    assert "loop" not in xml


# === CallInstance tests ===

def test_call_instance_from_raw():
    raw = Call(number="+1234567890", phone_mac="AA:BB:CC:DD:EE:FF", call_id="TC@1")
    raw.status = CallStatus.IN_PROGRESS
    instance = CallInstance(raw)
    assert instance.to == "+1234567890"
    assert instance.status == "in-progress"
    assert instance.direction == "outbound-api"
    assert instance.account_sid == "LOCAL"
    assert instance.price == "0.00"


def test_call_instance_sync():
    raw = Call(number="+1", phone_mac="AA:BB:CC:DD:EE:FF", call_id="TC@1")
    raw.status = CallStatus.COMPLETED
    raw.duration = 45.7
    instance = CallInstance(raw)
    instance._sync()
    assert instance.status == "completed"
    assert instance.duration == "45"


def test_call_instance_repr():
    raw = Call(number="+1234567890", phone_mac="AA:BB:CC:DD:EE:FF", call_id="TC@1")
    instance = CallInstance(raw)
    r = repr(instance)
    assert "CallInstance" in r
    assert "+1234567890" in r


# === CallsResource tests ===

def test_calls_resource_call_returns_context():
    rc = MagicMock()
    calls = CallsResource(rc)
    ctx = calls("some-sid")
    assert isinstance(ctx, CallContext)


def test_calls_resource_list_empty():
    rc = MagicMock()
    calls = CallsResource(rc)
    result = calls.list()
    assert result == []


def test_calls_resource_list_with_calls():
    rc = MagicMock()
    calls = CallsResource(rc)

    raw = Call(number="+1", phone_mac="AA:BB:CC:DD:EE:FF", call_id="TC@1")
    raw.status = CallStatus.COMPLETED
    calls._call_map[raw.sid] = raw

    result = calls.list()
    assert len(result) == 1
    assert result[0].to == "+1"


def test_calls_resource_list_filter_status():
    rc = MagicMock()
    calls = CallsResource(rc)

    raw1 = Call(number="+1", phone_mac="AA:BB:CC:DD:EE:FF", call_id="TC@1")
    raw1.status = CallStatus.COMPLETED
    calls._call_map[raw1.sid] = raw1

    raw2 = Call(number="+2", phone_mac="AA:BB:CC:DD:EE:FF", call_id="TC@2")
    raw2.status = CallStatus.FAILED
    calls._call_map[raw2.sid] = raw2

    result = calls.list(status="completed")
    assert len(result) == 1
    assert result[0].to == "+1"


# === CallContext tests ===

def test_call_context_fetch():
    rc = MagicMock()
    calls = CallsResource(rc)

    raw = Call(number="+1", phone_mac="AA:BB:CC:DD:EE:FF", call_id="TC@1")
    raw.status = CallStatus.IN_PROGRESS
    calls._call_map[raw.sid] = raw

    ctx = calls(raw.sid)
    instance = ctx.fetch()
    assert instance.status == "in-progress"


def test_call_context_fetch_not_found():
    rc = MagicMock()
    calls = CallsResource(rc)

    ctx = calls("nonexistent")
    with pytest.raises(Exception, match="not found"):
        ctx.fetch()


def test_call_context_update_completed():
    rc = MagicMock()
    calls = CallsResource(rc)

    raw = Call(number="+1", phone_mac="AA:BB:CC:DD:EE:FF", call_id="TC@1")
    raw.status = CallStatus.IN_PROGRESS
    calls._call_map[raw.sid] = raw

    with patch("robocall._adb"):
        ctx = calls(raw.sid)
        instance = ctx.update(status="completed")
        assert instance.status == "completed"


# === TwiML parsing tests ===

def test_parse_twiml_say():
    rc = MagicMock()
    calls = CallsResource(rc)
    verbs = calls._parse_twiml('<Response><Say voice="alice">Hello</Say></Response>')
    assert len(verbs) == 1
    assert verbs[0]["verb"] == "Say"
    assert verbs[0]["text"] == "Hello"
    assert verbs[0]["voice"] == "alice"


def test_parse_twiml_play():
    rc = MagicMock()
    calls = CallsResource(rc)
    verbs = calls._parse_twiml("<Response><Play>/tmp/audio.wav</Play></Response>")
    assert len(verbs) == 1
    assert verbs[0]["verb"] == "Play"
    assert verbs[0]["url"] == "/tmp/audio.wav"


def test_parse_twiml_multiple_verbs():
    rc = MagicMock()
    calls = CallsResource(rc)
    twiml = """<Response>
        <Say>Hello</Say>
        <Pause length="2"/>
        <Play>file:///tmp/a.wav</Play>
        <Hangup/>
    </Response>"""
    verbs = calls._parse_twiml(twiml)
    assert len(verbs) == 4
    assert verbs[0]["verb"] == "Say"
    assert verbs[1]["verb"] == "Pause"
    assert verbs[1]["length"] == 2
    assert verbs[2]["verb"] == "Play"
    assert verbs[3]["verb"] == "Hangup"


def test_parse_twiml_invalid():
    rc = MagicMock()
    calls = CallsResource(rc)
    with pytest.raises(TwiMLError, match="Invalid TwiML"):
        calls._parse_twiml("not xml at all <>>")


# === Client tests ===

def test_client_init():
    client = Client()
    assert client.account_sid == "LOCAL"
    assert isinstance(client.calls, CallsResource)


def test_client_init_with_creds():
    client = Client(account_sid="ACxxx", auth_token="tok123")
    assert client.account_sid == "ACxxx"


def test_client_repr():
    client = Client()
    assert "Client" in repr(client)
    assert "LOCAL" in repr(client)


# === Integration-style test (all mocked) ===

@patch("robocall._adb")
@patch("robocall._run")
def test_full_flow_mocked(mock_run, mock_adb, tmp_path):
    """Test the full Twilio-compatible flow with all external calls mocked."""
    import json

    # Mock ADB responses
    def adb_side_effect(*args, **kwargs):
        cmd = " ".join(args)
        if "devices" in cmd:
            return "List of devices attached\n192.168.0.29:39897\tdevice"
        if "telephony.registry" in cmd:
            return "mVoiceRegState=0(IN_SERVICE)"
        if "dumpsys telecom" in cmd:
            return """
            Call TC@99: {
                startTime: 1774012698199
                endTime: 0
                direction: OUTGOING
            }
            Call TC@100: {
                startTime: 1774012698199
                endTime: 0
                direction: OUTGOING
            }
            """
        if "am start" in cmd:
            return "Starting: Intent { ... }"
        if "KEYCODE_ENDCALL" in cmd:
            return ""
        return ""

    mock_adb.side_effect = adb_side_effect

    # Mock pw-dump for SCO nodes
    pw_dump_data = json.dumps([
        {
            "id": 97,
            "type": "PipeWire:Interface:Node",
            "info": {"props": {
                "factory.name": "api.bluez5.sco.sink",
                "node.name": "bluez_output.64_A2_F9_B8_21_94.1",
            }},
        },
        {
            "id": 98,
            "type": "PipeWire:Interface:Node",
            "info": {"props": {
                "factory.name": "api.bluez5.sco.source",
                "node.name": "bluez_input.64_A2_F9_B8_21_94.0",
            }},
        },
    ])

    mock_run.return_value = subprocess.CompletedProcess(
        args=[], returncode=0, stdout=pw_dump_data, stderr=""
    )

    client = Client()

    # Verify checks
    assert client._rc.check_adb() is True
    assert client._rc.check_signal() is True
