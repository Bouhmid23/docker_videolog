"""Adversarial challenge test suite for Milestone 1:
1. LiveKit scheme conversion (ws, wss, http, https, custom ports, query parameters, malformed inputs).
2. Redis payload validation and disconnect resilience in listen_room_events.
3. L1/L2 threshold cache isolation, TTL expiration, and cross-athlete bleed prevention.
"""
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


# ==============================================================================
# SECTION 1: LIVEKIT URL SCHEME CONVERSION STRESS TESTS
# ==============================================================================

@pytest.mark.parametrize(
    "input_url, expected_url",
    [
        # Standard default ports
        ("ws://livekit:7880", "http://livekit:7880"),
        ("wss://livekit.domain.org", "https://livekit.domain.org"),
        # HTTP/HTTPS should remain untouched
        ("http://livekit:7880", "http://livekit:7880"),
        ("https://livekit.domain.org", "https://livekit.domain.org"),
        # Custom non-standard ports
        ("ws://127.0.0.1:9090", "http://127.0.0.1:9090"),
        ("wss://webrtc.corp.internal:8443", "https://webrtc.corp.internal:8443"),
        ("ws://localhost:1883", "http://localhost:1883"),
        ("wss://edge-cluster.cloud:443", "https://edge-cluster.cloud:443"),
        # Subpaths and URL query parameters
        ("ws://livekit:7880/rtc", "http://livekit:7880/rtc"),
        ("wss://livekit.videolog.app/v1/rpc?param=value&foo=bar", "https://livekit.videolog.app/v1/rpc?param=value&foo=bar"),
        ("ws://localhost:7880/deep/nested/path/", "http://localhost:7880/deep/nested/path/"),
        # IPv6 addresses with custom ports
        ("ws://[::1]:7880", "http://[::1]:7880"),
        ("wss://[2001:db8::1]:8443", "https://[2001:db8::1]:8443"),
        # Scheme without host (edge cases)
        ("ws://", "http://"),
        ("wss://", "https://"),
        # Arbitrary/custom schemes or no scheme
        ("grpc://livekit:7880", "grpc://livekit:7880"),
        ("localhost:7880", "localhost:7880"),
        ("", ""),
    ],
)
def test_livekit_scheme_conversion_matrix(input_url, expected_url):
    """Stress test get_http_livekit_url across standard, custom port, IPv6, and path variants."""
    assert agent.get_http_livekit_url(input_url) == expected_url


# ==============================================================================
# SECTION 2: REDIS PAYLOAD VALIDATION & DISCONNECT RESILIENCE
# ==============================================================================

@pytest.mark.asyncio
async def test_listen_room_events_ignores_non_message_types():
    """Verify pubsub listen loop ignores subscribe/pmessage/unsubscribe meta-messages."""
    events = [
        {"type": "subscribe", "pattern": None, "channel": "room:events", "data": 1},
        {"type": "pmessage", "pattern": "room:*", "channel": "room:events", "data": "dummy"},
        {"type": "unsubscribe", "pattern": None, "channel": "room:events", "data": 0},
    ]

    async def mock_listen():
        for ev in events:
            yield ev
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

        assert mock_start.call_count == 0
        assert mock_stop.await_count == 0


@pytest.mark.asyncio
async def test_listen_room_events_handles_malformed_and_empty_payloads():
    """Verify listen_room_events handles empty, non-json, or invalid payloads."""
    events = [
        {"type": "message", "data": None},
        {"type": "message", "data": ""},
        {"type": "message", "data": "{not a valid json:"},
        {"type": "message", "data": "truncated { \"event\": "},
        # Valid message
        {"type": "message", "data": json.dumps({"event": "room_started", "room_name": "resilient-room"})},
    ]
    events_iter = iter(events)

    async def mock_listen():
        for ev in events_iter:
            yield ev
        raise asyncio.CancelledError()

    mock_pubsub = MagicMock()
    mock_pubsub.subscribe = AsyncMock()
    mock_pubsub.unsubscribe = AsyncMock()
    mock_pubsub.close = AsyncMock()
    mock_pubsub.listen = mock_listen

    mock_redis = MagicMock()
    mock_redis.pubsub.return_value = mock_pubsub
    agent.redis_client = mock_redis

    with patch("agent.start_watching_room") as mock_start:
        with pytest.raises(asyncio.CancelledError):
            await agent.listen_room_events()

        # The valid message was processed despite preceding malformed inputs
        mock_start.assert_called_once_with("resilient-room")


@pytest.mark.asyncio
async def test_listen_room_events_missing_or_blank_room_name():
    """Verify listen_room_events skips payloads where room_name is missing, None, or blank."""
    events = [
        {"type": "message", "data": json.dumps({"event": "room_started"})},  # No room_name
        {"type": "message", "data": json.dumps({"event": "room_started", "room_name": None})},
        {"type": "message", "data": json.dumps({"event": "room_started", "room_name": ""})},
        {"type": "message", "data": json.dumps({"event": "room_finished"})},
        {"type": "message", "data": json.dumps({"event": "room_finished", "room_name": ""})},
        # Valid message
        {"type": "message", "data": json.dumps({"event": "room_started", "room_name": "valid-room"})},
    ]

    async def mock_listen():
        for ev in events:
            yield ev
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

        assert mock_start.call_count == 1
        mock_start.assert_called_once_with("valid-room")
        assert mock_stop.await_count == 0


@pytest.mark.asyncio
async def test_listen_room_events_redis_disconnect_and_reconnect():
    """Empirically test that an unexpected Redis disconnection triggers retry and reconnects."""
    call_count = 0

    class DisconnectingPubSub:
        async def subscribe(self, channel):
            pass

        async def unsubscribe(self, channel):
            pass

        async def close(self):
            pass

        async def listen(self):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # First run: yield one event, then raise Redis connection error
                yield {"type": "message", "data": json.dumps({"event": "room_started", "room_name": "pre-crash-room"})}
                raise ConnectionResetError("Redis connection lost unexpectedly")
            elif call_count == 2:
                # Reconnected run: yield second event, then cleanly cancel
                yield {"type": "message", "data": json.dumps({"event": "room_started", "room_name": "post-reconnect-room"})}
                raise asyncio.CancelledError()

    mock_redis = MagicMock()
    mock_redis.pubsub.side_effect = lambda: DisconnectingPubSub()
    agent.redis_client = mock_redis

    with patch("agent.start_watching_room") as mock_start, \
         patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        with pytest.raises(asyncio.CancelledError):
            await agent.listen_room_events()

        assert call_count == 2
        mock_sleep.assert_awaited_with(3)  # Verified 3s backoff before reconnect
        assert mock_start.call_count == 2
        mock_start.assert_any_call("pre-crash-room")
        mock_start.assert_any_call("post-reconnect-room")


# ==============================================================================
# SECTION 3: L1 / L2 CACHE ISOLATION, EXPIRATION, AND CROSS-ATHLETE BLEED
# ==============================================================================

@pytest.mark.asyncio
async def test_cache_cross_athlete_isolation_no_bleed():
    """Stress test that concurrent/interleaved queries for multiple distinct athletes
    never bleed or cross-pollute cached thresholds."""
    athletes_db = {
        "athlete_alpha": {
            "_id": "athlete_alpha",
            "athlete_profile": {"hr_max": 202, "hr_rest": 42},
            "health_consent": {"granted": True},
        },
        "athlete_beta": {
            "_id": "athlete_beta",
            "athlete_profile": {"hr_max": 168, "hr_rest": 58},
            "health_consent": {"granted": True},
        },
        "athlete_no_consent": {
            "_id": "athlete_no_consent",
            "athlete_profile": {"hr_max": 199, "hr_rest": 40},
            "health_consent": {"granted": False},
        },
        "athlete_partial": {
            "_id": "athlete_partial",
            "athlete_profile": {"hr_max": 177, "hr_rest": None},
            "health_consent": {"granted": True},
        },
        "athlete_missing": None,
    }

    mock_col = AsyncMock()
    async def mock_find_one(query, projection=None):
        uid = query.get("_id")
        return athletes_db.get(uid)

    mock_col.find_one = mock_find_one
    agent.users_col = mock_col
    agent.redis_client = None  # Test L1 memory isolation

    # Interleaved queries across athletes
    expected_results = {
        "athlete_alpha": (202, 42),
        "athlete_beta": (168, 58),
        "athlete_no_consent": (185, 50),  # Fallback
        "athlete_partial": (177, 50),     # Custom max, fallback rest
        "athlete_missing": (185, 50),     # Fallback
    }

    # Query in repeated shuffled sequence 50 times
    for _ in range(50):
        for uid, expected in expected_results.items():
            result = await agent.get_athlete_thresholds(uid)
            assert result == expected, f"Cross-athlete bleed detected for {uid}: got {result}, expected {expected}"


@pytest.mark.asyncio
async def test_cache_l1_expiration_and_invalidation():
    """Verify that after L1 CACHE_TTL_SECONDS expires, updated MongoDB profile is reflected."""
    athlete_id = "athlete_updating"
    doc = {
        "_id": athlete_id,
        "athlete_profile": {"hr_max": 180, "hr_rest": 55},
        "health_consent": {"granted": True},
    }

    mock_col = AsyncMock()
    mock_col.find_one.return_value = doc
    agent.users_col = mock_col
    agent.redis_client = None

    current_time = 1000.0
    with patch("time.monotonic", side_effect=lambda: current_time):
        # 1. First fetch: loads into L1 cache
        t1 = await agent.get_athlete_thresholds(athlete_id)
        assert t1 == (180, 55)
        assert mock_col.find_one.await_count == 1

        # 2. Athlete updates their profile in database
        doc["athlete_profile"] = {"hr_max": 195, "hr_rest": 48}

        # Query before TTL expiration (e.g. at +100s)
        current_time = 1100.0
        t2 = await agent.get_athlete_thresholds(athlete_id)
        assert t2 == (180, 55)  # Still cached in L1
        assert mock_col.find_one.await_count == 1  # No DB call

        # 3. Advance time past TTL (now > 1000 + 300)
        current_time = 1301.0
        t3 = await agent.get_athlete_thresholds(athlete_id)
        assert t3 == (195, 48)  # L1 expired, fetched fresh from DB
        assert mock_col.find_one.await_count == 2


@pytest.mark.asyncio
async def test_consent_revocation_invalidates_to_fallback():
    """Verify that when athlete revokes consent (granted: False) after TTL, thresholds revert to fallback."""
    athlete_id = "athlete_revoking"
    doc = {
        "_id": athlete_id,
        "athlete_profile": {"hr_max": 198, "hr_rest": 44},
        "health_consent": {"granted": True},
    }

    mock_col = AsyncMock()
    mock_col.find_one.return_value = doc
    agent.users_col = mock_col
    agent.redis_client = None

    current_time = 5000.0
    with patch("time.monotonic", side_effect=lambda: current_time):
        t1 = await agent.get_athlete_thresholds(athlete_id)
        assert t1 == (198, 44)

        # Consent revoked by athlete
        doc["health_consent"] = {"granted": False}

        # Advance past TTL
        current_time = 5305.0
        t2 = await agent.get_athlete_thresholds(athlete_id)
        assert t2 == (185, 50)  # Must strictly revert to global defaults (ALERT_HR_MAX, ALERT_HR_REST)


@pytest.mark.asyncio
async def test_concurrent_threshold_queries_race_condition():
    """Stress test 100 concurrent asynchronous threshold requests across 10 athletes."""
    athletes = [f"athlete_{i}" for i in range(10)]
    mock_col = AsyncMock()

    async def mock_find_one(query, projection=None):
        uid = query.get("_id")
        idx = int(uid.split("_")[1])
        return {
            "_id": uid,
            "athlete_profile": {"hr_max": 170 + idx, "hr_rest": 45 + idx},
            "health_consent": {"granted": True},
        }

    mock_col.find_one = mock_find_one
    agent.users_col = mock_col
    agent.redis_client = None

    tasks = [
        agent.get_athlete_thresholds(athletes[i % 10])
        for i in range(100)
    ]
    results = await asyncio.gather(*tasks)

    for i, res in enumerate(results):
        idx = i % 10
        assert res == (170 + idx, 45 + idx)
