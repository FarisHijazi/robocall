"""Tests for robocall.py - the low-level SDK."""

import json
import os
import signal
import subprocess
import tempfile
import wave
import struct
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

# Add parent to path
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from robocall import (
    Robocall,
    Call,
    CallStatus,
    Recording,
    SCONodes,
    RobocallError,
    NoSignalError,
    CallFailedError,
    ADBError,
    AudioError,
    _adb,
    _run,
)


# === Fixtures ===

@pytest.fixture
def tmp_wav(tmp_path):
    """Create a test WAV file (mono 16kHz)."""
    path = str(tmp_path / "test.wav")
    w = wave.open(path, "wb")
    w.setnchannels(1)
    w.setsampwidth(2)
    w.setframerate(16000)
    # 1 second of 440Hz sine wave
    import math
    samples = [int(16000 * math.sin(2 * math.pi * 440 * t / 16000)) for t in range(16000)]
    w.writeframes(struct.pack(f"<{len(samples)}h", *samples))
    w.close()
    return path


@pytest.fixture
def stereo_wav(tmp_path):
    """Create a stereo 48kHz WAV file (needs conversion)."""
    path = str(tmp_path / "stereo.wav")
    w = wave.open(path, "wb")
    w.setnchannels(2)
    w.setsampwidth(2)
    w.setframerate(48000)
    samples = [0] * 96000  # 1 second silence, stereo
    w.writeframes(struct.pack(f"<{len(samples)}h", *samples))
    w.close()
    return path


@pytest.fixture
def mock_adb():
    """Mock ADB commands."""
    with patch("robocall._run") as mock_run:
        yield mock_run


@pytest.fixture
def robocall_client():
    """Create a Robocall client."""
    return Robocall(phone_ip="192.168.0.29", phone_mac="64:A2:F9:B8:21:94")


# === Recording tests ===

def test_recording_analyze(tmp_wav):
    rec = Recording(path=tmp_wav)
    rec.analyze()
    assert rec.duration == pytest.approx(1.0)
    assert rec.max_amplitude > 0
    assert rec.avg_amplitude > 0


def test_recording_has_audio(tmp_wav):
    rec = Recording(path=tmp_wav)
    assert rec.has_audio is True


def test_recording_silence(tmp_path):
    path = str(tmp_path / "silence.wav")
    w = wave.open(path, "wb")
    w.setnchannels(1)
    w.setsampwidth(2)
    w.setframerate(16000)
    w.writeframes(struct.pack("<16000h", *([0] * 16000)))
    w.close()

    rec = Recording(path=path)
    assert rec.has_audio is False


# === Call tests ===

def test_call_init():
    call = Call(number="+1234567890", phone_mac="AA:BB:CC:DD:EE:FF", call_id="TC@1")
    assert call.number == "+1234567890"
    assert call.status == CallStatus.QUEUED
    assert call.direction == "outbound"
    assert call.tc_id == "TC@1"
    assert len(call.sid) == 36  # UUID format


def test_call_repr():
    call = Call(number="+1234567890", phone_mac="AA:BB:CC:DD:EE:FF", call_id="TC@1")
    r = repr(call)
    assert "+1234567890" in r
    assert "queued" in r


def test_call_ensure_mono_wav_already_mono(tmp_wav):
    call = Call(number="+1", phone_mac="AA:BB:CC:DD:EE:FF", call_id="TC@1")
    result = call._ensure_mono_wav(tmp_wav)
    assert result == tmp_wav  # no conversion needed


@patch("robocall._run")
def test_call_ensure_mono_wav_converts_stereo(mock_run, stereo_wav):
    mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
    call = Call(number="+1", phone_mac="AA:BB:CC:DD:EE:FF", call_id="TC@1")

    result = call._ensure_mono_wav(stereo_wav)
    assert "mono16k" in result


def test_call_play_wrong_status():
    call = Call(number="+1", phone_mac="AA:BB:CC:DD:EE:FF", call_id="TC@1")
    call.status = CallStatus.QUEUED
    with pytest.raises(RobocallError, match="Cannot play audio"):
        call.play("/nonexistent.wav")


def test_call_record_wrong_status():
    call = Call(number="+1", phone_mac="AA:BB:CC:DD:EE:FF", call_id="TC@1")
    call.status = CallStatus.COMPLETED
    with pytest.raises(RobocallError, match="Cannot record"):
        call.record()


@patch("robocall._run")
def test_call_find_sco_nodes(mock_run):
    pw_dump = json.dumps([
        {
            "id": 97,
            "type": "PipeWire:Interface:Node",
            "info": {"props": {
                "factory.name": "api.bluez5.sco.sink",
                "node.name": "bluez_output.AA_BB_CC_DD_EE_FF.1",
            }},
        },
        {
            "id": 98,
            "type": "PipeWire:Interface:Node",
            "info": {"props": {
                "factory.name": "api.bluez5.sco.source",
                "node.name": "bluez_input.AA_BB_CC_DD_EE_FF.0",
            }},
        },
    ])
    mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout=pw_dump, stderr="")

    call = Call(number="+1", phone_mac="AA:BB:CC:DD:EE:FF", call_id="TC@1")
    nodes = call._find_sco_nodes(timeout=1)

    assert nodes.sink == "bluez_output.AA_BB_CC_DD_EE_FF.1"
    assert nodes.source == "bluez_input.AA_BB_CC_DD_EE_FF.0"


@patch("robocall._run")
def test_call_find_sco_nodes_timeout(mock_run):
    mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="[]", stderr="")

    call = Call(number="+1", phone_mac="AA:BB:CC:DD:EE:FF", call_id="TC@1")
    with pytest.raises(AudioError, match="SCO audio nodes did not appear"):
        call._find_sco_nodes(timeout=1)


def test_call_hangup():
    call = Call(number="+1", phone_mac="AA:BB:CC:DD:EE:FF", call_id="TC@1")
    call.status = CallStatus.IN_PROGRESS
    call.start_time = __import__("datetime").datetime.now()

    with patch("robocall._adb"):
        call.hangup()

    assert call.status == CallStatus.COMPLETED
    assert call.end_time is not None
    assert call.duration is not None


# === Call.wait_for_connection tests ===

@patch("robocall._adb")
def test_wait_for_connection_success(mock_adb):
    mock_adb.return_value = """
    Call TC@5: {
        startTime: 1774012698199
        endTime: 0
        direction: OUTGOING
    }
    """
    call = Call(number="+1", phone_mac="AA:BB:CC:DD:EE:FF", call_id="TC@5")
    result = call.wait_for_connection(timeout=5)
    assert result is True
    assert call.status == CallStatus.IN_PROGRESS


@patch("robocall._adb")
def test_wait_for_connection_failed_signal(mock_adb):
    mock_adb.return_value = """
    Call TC@5: {
        startTime: 0
        endTime: 1774012698199
        direction: OUTGOING
        callTerminationReason: DisconnectCause [ Code: (ERROR) Reason: (ServiceState.STATE_OUT_OF_SERVICE) ]
    }
    """
    call = Call(number="+1", phone_mac="AA:BB:CC:DD:EE:FF", call_id="TC@5")
    with pytest.raises(NoSignalError):
        call.wait_for_connection(timeout=2)


@patch("robocall._adb")
def test_wait_for_connection_timeout(mock_adb):
    mock_adb.return_value = "nothing relevant"
    call = Call(number="+1", phone_mac="AA:BB:CC:DD:EE:FF", call_id="TC@5")
    with pytest.raises(CallFailedError, match="not answered"):
        call.wait_for_connection(timeout=2)
    assert call.status == CallStatus.NO_ANSWER


# === Robocall client tests ===

@patch("robocall._adb")
def test_check_adb(mock_adb):
    mock_adb.return_value = "List of devices attached\n192.168.0.29:39897\tdevice"
    rc = Robocall()
    assert rc.check_adb() is True


@patch("robocall._adb")
def test_check_adb_disconnected(mock_adb):
    mock_adb.return_value = "List of devices attached\n"
    rc = Robocall()
    assert rc.check_adb() is False


@patch("robocall._run")
def test_check_bluetooth(mock_run):
    mock_run.return_value = subprocess.CompletedProcess(
        args=[], returncode=0, stdout="Connected: yes\n", stderr=""
    )
    rc = Robocall()
    assert rc.check_bluetooth() is True


@patch("robocall._adb")
def test_check_signal(mock_adb):
    mock_adb.return_value = "mVoiceRegState=0(IN_SERVICE)"
    rc = Robocall()
    assert rc.check_signal() is True


@patch("robocall._adb")
def test_check_signal_out_of_service(mock_adb):
    mock_adb.return_value = "mVoiceRegState=1(OUT_OF_SERVICE)"
    rc = Robocall()
    assert rc.check_signal() is False


@patch("robocall._adb")
def test_get_latest_tc_id(mock_adb):
    mock_adb.return_value = """
    Call TC@10: { ... }
    Call TC@11: { ... }
    Call TC@12: { ... }
    """
    rc = Robocall()
    assert rc._get_latest_tc_id() == 12


# === CallStatus tests ===

def test_call_status_values():
    assert CallStatus.QUEUED.value == "queued"
    assert CallStatus.IN_PROGRESS.value == "in-progress"
    assert CallStatus.COMPLETED.value == "completed"
    assert CallStatus.FAILED.value == "failed"


# === _adb helper tests ===

@patch("robocall._run")
def test_adb_success(mock_run):
    mock_run.return_value = subprocess.CompletedProcess(
        args=[], returncode=0, stdout="OK\n", stderr=""
    )
    result = _adb("devices")
    assert result == "OK"


@patch("robocall._run")
def test_adb_failure(mock_run):
    mock_run.return_value = subprocess.CompletedProcess(
        args=[], returncode=1, stdout="", stderr="error: no devices"
    )
    with pytest.raises(ADBError):
        _adb("devices")
