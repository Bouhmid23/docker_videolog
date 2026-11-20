"""Unit tests for telemetry-agent dynamic rooms, personalized athlete thresholds, and egress webhook events."""
import asyncio
import json
import os
import sys
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

# Required dummy environment variables for imports
for key, val in {
    "LIVEKIT_URL": "ws://localhost:7880",
    "LIVEKIT_API_KEY": "test-key",
    "LIVEKIT_API_SECRET": "test-secret-012345678901234567890123",
    "REDIS_URL": "redis://localhost:6379",
    "MINIO_ACCESS_KEY": "test-minio",
    "MINIO_SECRET_KEY": "test-secret",
    "MINIO_PUBLIC_URL": "http://minio:9000",
    "ALERT_HR_MAX": "185",
    "ALERT_HR_REST": "50",
    "ALERT_SPEED_MAX": "40",
}.items():
    os.environ.setdefault(key, val)

AGENT_DIR = Path(__file__).resolve().parent.parent
WORKSPACE_ROOT = AGENT_DIR.parent

if str(AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(AGENT_DIR))
if str(WORKSPACE_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_ROOT))

import agent
import egress.webhook as webhook


@pytest.fixture(autouse=True)
def reset_agent_state():
    """Reset global state between tests."""
    agent.active_rooms.clear()
    agent.session_starts.clear()
    agent.participant_counts.clear()
    agent._athlete_thresholds_cache.clear()
    agent.redis_client = AsyncMock()
    agent.users_col = None
    agent.ROOM_NAMES = []
    yield
    agent.active_rooms.clear()
    agent.session_starts.clear()
    agent.participant_counts.clear()
    agent._athlete_thresholds_cache.clear()


# ── 1. LiveKit URL Conversion Tests ──────────────────────────────────────────

def test_livekit_url_conversion():
    assert agent.get_http_livekit_url("ws://livekit:7880") == "http://livekit:7880"
    assert agent.get_http_livekit_url("wss://livekit.videolog.app") == "https://livekit.videolog.app"
    assert agent.get_http_livekit_url("http://localhost:7880") == "http://localhost:7880"
    assert agent.get_http_livekit_url("https://livekit.example.com") == "https://livekit.example.com"


# ── 2. Athlete Threshold Calculation Tests ───────────────────────────────────

@pytest.mark.asyncio
async def test_get_athlete_thresholds_consent_granted():
    mock_col = AsyncMock()
    mock_col.find_one.return_value = {
        "_id": "athlete_1",
        "athlete_profile": {"hr_max": 192, "hr_rest": 48},
        "health_consent": {"granted": True, "version": "2026-11-v1"},
    }
    agent.users_col = mock_col

    hr_max, hr_rest = await agent.get_athlete_thresholds("athlete_1")
    assert hr_max == 192
    assert hr_rest == 48
    mock_col.find_one.assert_awaited_once_with(
        {"_id": "athlete_1"},
        {"athlete_profile": 1, "health_consent": 1},
    )


@pytest.mark.asyncio
async def test_get_athlete_thresholds_consent_missing():
    mock_col = AsyncMock()
    mock_col.find_one.return_value = {
        "_id": "athlete_2",
        "athlete_profile": {"hr_max": 195, "hr_rest": 45},
        "health_consent": None,
    }
    agent.users_col = mock_col

    hr_max, hr_rest = await agent.get_athlete_thresholds("athlete_2")
    assert hr_max == 185  # Fallback to ALERT_HR_MAX
    assert hr_rest == 50  # Fallback to ALERT_HR_REST


@pytest.mark.asyncio
async def test_get_athlete_thresholds_consent_false():
    mock_col = AsyncMock()
    mock_col.find_one.return_value = {
        "_id": "athlete_3",
        "athlete_profile": {"hr_max": 195, "hr_rest": 45},
        "health_consent": {"granted": False},
    }
    agent.users_col = mock_col

    hr_max, hr_rest = await agent.get_athlete_thresholds("athlete_3")
    assert hr_max == 185
    assert hr_rest == 50


@pytest.mark.asyncio
async def test_get_athlete_thresholds_missing_user():
    mock_col = AsyncMock()
    mock_col.find_one.return_value = None
    agent.users_col = mock_col

    hr_max, hr_rest = await agent.get_athlete_thresholds("non_existent_uid")
    assert hr_max == 185
    assert hr_rest == 50


@pytest.mark.asyncio
async def test_get_athlete_thresholds_partial_profile():
    mock_col = AsyncMock()
    mock_col.find_one.return_value = {
        "_id": "athlete_partial",
        "athlete_profile": {"hr_max": 190, "hr_rest": None},
        "health_consent": {"granted": True},
    }
    agent.users_col = mock_col

    hr_max, hr_rest = await agent.get_athlete_thresholds("athlete_partial")
    assert hr_max == 190
    assert hr_rest == 50


@pytest.mark.asyncio
async def test_get_athlete_thresholds_null_values():
    mock_col = AsyncMock()
    mock_col.find_one.return_value = {
        "_id": "athlete_nulls",
        "athlete_profile": {"hr_max": None, "hr_rest": None},
        "health_consent": {"granted": True},
    }
    agent.users_col = mock_col

    hr_max, hr_rest = await agent.get_athlete_thresholds("athlete_nulls")
    assert hr_max == 185
    assert hr_rest == 50


@pytest.mark.asyncio
async def test_get_athlete_thresholds_empty_or_reserved_identity():
    mock_col = AsyncMock()
    agent.users_col = mock_col

    for reserved in ["", "   ", "unknown", "telemetry-agent"]:
        hr_max, hr_rest = await agent.get_athlete_thresholds(reserved)
        assert hr_max == 185
        assert hr_rest == 50

    assert mock_col.find_one.await_count == 0


@pytest.mark.asyncio
async def test_get_athlete_thresholds_l1_caching():
    mock_col = AsyncMock()
    mock_col.find_one.return_value = {
        "_id": "athlete_cached",
        "athlete_profile": {"hr_max": 188, "hr_rest": 52},
        "health_consent": {"granted": True},
    }
    agent.users_col = mock_col

    res1 = await agent.get_athlete_thresholds("athlete_cached")
    res2 = await agent.get_athlete_thresholds("athlete_cached")
    assert res1 == (188, 52)
    assert res2 == (188, 52)
    assert mock_col.find_one.await_count == 1


@pytest.mark.asyncio
async def test_get_athlete_thresholds_l2_redis_caching():
    agent.users_col = None  # Mongo unavailable
    mock_redis = AsyncMock()
    mock_redis.get.return_value = json.dumps({"hr_max": 194, "hr_rest": 46})
    agent.redis_client = mock_redis

    hr_max, hr_rest = await agent.get_athlete_thresholds("athlete_from_redis")
    assert hr_max == 194
    assert hr_rest == 46
    mock_redis.get.assert_awaited_once_with("athlete:thresholds:athlete_from_redis")


@pytest.mark.asyncio
async def test_get_athlete_thresholds_resilient_on_db_error():
    mock_col = AsyncMock()
    mock_col.find_one.side_effect = RuntimeError("MongoDB connection lost")
    agent.users_col = mock_col

    hr_max, hr_rest = await agent.get_athlete_thresholds("athlete_err")
    assert hr_max == 185
    assert hr_rest == 50


@pytest.mark.asyncio
async def test_get_athlete_thresholds_when_users_col_is_none():
    agent.users_col = None
    agent.redis_client = None

    hr_max, hr_rest = await agent.get_athlete_thresholds("athlete_offline")
    assert hr_max == 185
    assert hr_rest == 50


# ── 3. Alert Emission Tests ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_maybe_emit_alerts_triggers_personalized_hr_max():
    with patch("agent.get_athlete_thresholds", new_callable=AsyncMock) as mock_thresh, \
         patch("agent.emit_alert", new_callable=AsyncMock) as mock_emit:
        mock_thresh.return_value = (170, 48)

        event = {"heart_rate": "175", "speed": "20.0"}
        await agent.maybe_emit_alerts("room_test", "athlete_1", event, pts_ms=1000.0, client_ts=1700000000.0)

        assert mock_emit.await_count == 1
        args, kwargs = mock_emit.await_args
        assert args[4] == "hr_max"
        assert args[5] == 175
        assert kwargs.get("threshold") == 170


@pytest.mark.asyncio
async def test_maybe_emit_alerts_triggers_personalized_hr_low():
    with patch("agent.get_athlete_thresholds", new_callable=AsyncMock) as mock_thresh, \
         patch("agent.emit_alert", new_callable=AsyncMock) as mock_emit:
        mock_thresh.return_value = (190, 45)

        event = {"heart_rate": "42", "speed": "0.0"}
        await agent.maybe_emit_alerts("room_test", "athlete_1", event, pts_ms=1500.0, client_ts=1700000001.0)

        assert mock_emit.await_count == 1
        args, kwargs = mock_emit.await_args
        assert args[4] == "hr_low"
        assert args[5] == 42
        assert kwargs.get("threshold") == 45


@pytest.mark.asyncio
async def test_maybe_emit_alerts_respects_fallback_when_no_consent():
    with patch("agent.get_athlete_thresholds", new_callable=AsyncMock) as mock_thresh, \
         patch("agent.emit_alert", new_callable=AsyncMock) as mock_emit:
        mock_thresh.return_value = (185, 50)

        # 175 bpm is under default 185 -> no alert
        event_ok = {"heart_rate": "175", "speed": "20.0"}
        await agent.maybe_emit_alerts("room_test", "athlete_noconsent", event_ok, pts_ms=1000.0, client_ts=1700000000.0)
        assert mock_emit.await_count == 0

        # 190 bpm is above default 185 -> triggers alert
        event_alert = {"heart_rate": "190", "speed": "20.0"}
        await agent.maybe_emit_alerts("room_test", "athlete_noconsent", event_alert, pts_ms=2000.0, client_ts=1700000001.0)
        assert mock_emit.await_count == 1
        args, kwargs = mock_emit.await_args
        assert args[4] == "hr_max"
        assert args[5] == 190
        assert kwargs.get("threshold") == 185


@pytest.mark.asyncio
async def test_maybe_emit_alerts_speed_max():
    with patch("agent.get_athlete_thresholds", new_callable=AsyncMock) as mock_thresh, \
         patch("agent.emit_alert", new_callable=AsyncMock) as mock_emit:
        mock_thresh.return_value = (185, 50)

        event = {"heart_rate": "150", "speed": "48.5"}
        await agent.maybe_emit_alerts("room_test", "athlete_speed", event, pts_ms=3000.0, client_ts=1700000002.0)

        assert mock_emit.await_count == 1
        args, kwargs = mock_emit.await_args
        assert args[4] == "speed_max"
        assert args[5] == 48.5
        assert kwargs.get("threshold") == 40.0


@pytest.mark.asyncio
async def test_emit_alert_payload_and_threshold():
    mock_redis = AsyncMock()
    agent.redis_client = mock_redis

    await agent.emit_alert(
        room_name="test-room",
        identity="athlete-123",
        pts_ms=12500.0,
        ts=1700000010.5,
        alert_type="hr_max",
        value=195,
        message="FC 195bpm > seuil 190bpm",
        threshold=190,
    )

    mock_redis.xadd.assert_awaited_once()
    stream_key, alert_dict = mock_redis.xadd.call_args[0][:2]
    assert stream_key == "telemetry:alerts"
    assert alert_dict["room_name"] == "test-room"
    assert alert_dict["participant"] == "athlete-123"
    assert alert_dict["pts_ms"] == "12500.0"
    assert alert_dict["type"] == "hr_max"
    assert alert_dict["value"] == "195"
    assert alert_dict["threshold"] == "190"

    mock_redis.publish.assert_awaited_once()
    pub_channel, raw_json = mock_redis.publish.call_args[0]
    assert pub_channel == "alerts"
    data = json.loads(raw_json)
    assert data["threshold"] == "190"


# ── 4. Dynamic Room Task Management Tests ────────────────────────────────────

@pytest.mark.asyncio
async def test_start_and_stop_room_task():
    async def dummy_run_room(name: str):
        try:
            while True:
                await asyncio.sleep(0.01)
        except asyncio.CancelledError:
            pass

    with patch("agent.run_room", side_effect=dummy_run_room):
        task1 = agent.start_watching_room("room_alpha")
        assert "room_alpha" in agent.active_rooms
        assert not task1.done()

        # Idempotent second call returns existing active task
        task2 = agent.start_watching_room("room_alpha")
        assert task1 is task2

        # Stopping cancels task and releases resources
        await agent.stop_watching_room("room_alpha")
        assert "room_alpha" not in agent.active_rooms
        assert task1.done()


def test_start_watching_room_empty_name():
    assert agent.start_watching_room("") is None
    assert agent.start_watching_room("   ") is None
    assert len(agent.active_rooms) == 0


@pytest.mark.asyncio
async def test_startup_reconciliation_discovery():
    mock_lk = MagicMock()
    mock_lk.room.list_rooms = AsyncMock()
    mock_room_a = MagicMock(name="room-alpha")
    mock_room_a.name = "room-alpha"
    mock_room_b = MagicMock(name="room-beta")
    mock_room_b.name = "room-beta"
    mock_res = MagicMock()
    mock_res.rooms = [mock_room_a, mock_room_b]
    mock_lk.room.list_rooms.return_value = mock_res
    mock_lk.aclose = AsyncMock()

    agent.LIVEKIT_URL = "ws://livekit:7880"
    agent.ROOM_NAMES = []

    with patch("agent.api.LiveKitAPI", return_value=mock_lk) as mock_api_cls, \
         patch("agent.start_watching_room") as mock_start:
        await agent.reconcile_active_rooms()

        # Check URL scheme conversion passed to LiveKitAPI constructor
        mock_api_cls.assert_called_once_with(
            "http://livekit:7880",
            agent.API_KEY,
            agent.API_SECRET,
        )
        assert mock_start.call_count == 2
        mock_start.assert_any_call("room-alpha")
        mock_start.assert_any_call("room-beta")
        mock_lk.aclose.assert_awaited_once()


@pytest.mark.asyncio
async def test_startup_reconciliation_handles_livekit_failure():
    with patch("agent.api.LiveKitAPI", side_effect=ConnectionError("Cannot connect")):
        # Should not raise exception
        await agent.reconcile_active_rooms()


@pytest.mark.asyncio
async def test_listen_room_events_dispatch():
    events = [
        {"type": "message", "data": json.dumps({"event": "room_started", "room_name": "live-room-1"})},
        {"type": "message", "data": json.dumps({"event": "room_finished", "room_name": "live-room-1"})},
        {"type": "message", "data": json.dumps({"type": "participant_joined", "room_name": "live-room-1"})},  # Ignored
    ]

    async def mock_listen():
        for ev in events:
            yield ev
        # Once events are consumed, cancel task to exit loop
        raise asyncio.CancelledError()

    mock_pubsub = MagicMock()
    mock_pubsub.subscribe = AsyncMock()
    mock_pubsub.unsubscribe = AsyncMock()
    mock_pubsub.close = AsyncMock()
    mock_pubsub.listen = mock_listen

    mock_redis = MagicMock()
    mock_redis.pubsub.return_value = mock_pubsub
    agent.redis_client = mock_redis

    with patch("agent.start_watching_room") as mock_start, \
         patch("agent.stop_watching_room", new_callable=AsyncMock) as mock_stop:
        with pytest.raises(asyncio.CancelledError):
            await agent.listen_room_events()

        mock_start.assert_called_once_with("live-room-1")
        mock_stop.assert_awaited_once_with("live-room-1")


# ── 5. Egress Webhook Redis Publication Contract Tests ────────────────────────

@pytest.mark.asyncio
async def test_egress_webhook_publish_room_started():
    mock_r = AsyncMock()
    with patch.object(webhook, "r", mock_r), \
         patch.object(webhook, "_persist_room", new_callable=AsyncMock) as mock_persist:
        await webhook.handle_room_started({"room": {"name": "session-started-123"}})

        mock_persist.assert_awaited_once_with("session-started-123")
        mock_r.publish.assert_awaited_once()
        channel, raw = mock_r.publish.call_args[0]
        assert channel == "room:events"
        data = json.loads(raw)
        assert data["event"] == "room_started"
        assert data["type"] == "room_started"
        assert data["room_name"] == "session-started-123"
        assert isinstance(data["timestamp"], float)


@pytest.mark.asyncio
async def test_egress_webhook_publish_room_finished():
    mock_r = AsyncMock()
    with patch.object(webhook, "r", mock_r), \
         patch.object(webhook, "_cancel_stop", new_callable=AsyncMock), \
         patch.object(webhook, "_stop_egress", new_callable=AsyncMock), \
         patch.object(webhook, "_persist_room", new_callable=AsyncMock) as mock_persist:
        await webhook.handle_room_finished({"room": {"name": "session-finished-123"}})

        mock_persist.assert_awaited_once_with("session-finished-123", finished=True)
        mock_r.publish.assert_awaited_once()
        channel, raw = mock_r.publish.call_args[0]
        assert channel == "room:events"
        data = json.loads(raw)
        assert data["event"] == "room_finished"
        assert data["type"] == "room_finished"
        assert data["room_name"] == "session-finished-123"
        assert isinstance(data["timestamp"], float)


def test_decode_binary_packet_v2_and_v3():
    import struct
    import time

    now_ms = time.time() * 1000
    ts_mod = int(now_ms) % (1 << 32)

    # v2 (21 octets)
    pkt_v2 = struct.pack(
        ">BBHBBiihBI",
        2, 1, 3250, 162, 90, int(48.8566 * 1e7), int(2.3522 * 1e7), 3500, 180, ts_mod,
    )
    dec_v2 = agent.decode_binary_packet(pkt_v2, now_ms)
    assert dec_v2 is not None
    assert dec_v2["speed"] == 32.5
    assert dec_v2["heart_rate"] == 162
    assert dec_v2["bearing"] == 180
    assert "power_w" not in dec_v2

    # v3 (25 octets avec power_w et bearing > 255)
    pkt_v3 = struct.pack(
        ">BBHBBiihHHIB",
        3, 1, 3500, 170, 95, int(48.8566 * 1e7), int(2.3522 * 1e7), 3500, 315, 285, ts_mod, 0,
    )
    dec_v3 = agent.decode_binary_packet(pkt_v3, now_ms)
    assert dec_v3 is not None
    assert dec_v3["speed"] == 35.0
    assert dec_v3["bearing"] == 315
    assert dec_v3["power_w"] == 285
