"""Empirical Challenger stress tests for telemetry-agent and threshold logic.

Covers:
1. Dynamic Room Task Lifecycle (rapid concurrent start/stop, race conditions, cleanup integrity)
2. Athlete Threshold Fallback Logic (edge-case documents, corrupted profiles, negative thresholds, unicode IDs, Mongo errors)
3. Strict Alert Emissions (personalized boundaries vs fallback boundaries, dual alerts)
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

AGENT_DIR = Path(__file__).resolve().parent.parent
WORKSPACE_ROOT = AGENT_DIR.parent

if str(AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(AGENT_DIR))
if str(WORKSPACE_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_ROOT))

import agent


@pytest.fixture(autouse=True)
def reset_agent():
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
# SECTION 1: Room Task Lifecycle Stress Tests
# ==============================================================================

@pytest.mark.asyncio
async def test_lifecycle_rapid_concurrent_multi_room_churn():
    """Stress test starting, stopping, and restarting 30 rooms concurrently."""
    async def dummy_run_room(name: str):
        try:
            while True:
                await asyncio.sleep(0.01)
        except asyncio.CancelledError:
            await asyncio.sleep(0.005)
            raise

    with patch("agent.run_room", side_effect=dummy_run_room):
        room_names = [f"stress_room_{i}" for i in range(30)]

        # 1. Concurrent startup
        tasks = [agent.start_watching_room(name) for name in room_names]
        assert len(agent.active_rooms) == 30
        assert all(t is not None and not t.done() for t in tasks)

        # 2. Concurrently stop half of them
        half_to_stop = room_names[:15]
        await asyncio.gather(*(agent.stop_watching_room(name) for name in half_to_stop))
        assert len(agent.active_rooms) == 15
        for name in half_to_stop:
            assert name not in agent.active_rooms

        # 3. Concurrently restart them
        new_tasks = [agent.start_watching_room(name) for name in half_to_stop]
        assert len(agent.active_rooms) == 30

        # 4. Concurrently stop all 30 rooms
        await asyncio.gather(*(agent.stop_watching_room(name) for name in room_names))
        await asyncio.sleep(0.02)
        assert len(agent.active_rooms) == 0


@pytest.mark.asyncio
async def test_lifecycle_start_stop_idempotency_and_empty():
    """Ensure start/stop handles duplicate calls, whitespace, and empty strings safely."""
    async def dummy_run_room(name: str):
        try:
            while True:
                await asyncio.sleep(0.01)
        except asyncio.CancelledError:
            pass

    with patch("agent.run_room", side_effect=dummy_run_room):
        # Empty/whitespace inputs
        assert agent.start_watching_room("") is None
        assert agent.start_watching_room("   ") is None
        await agent.stop_watching_room("")
        await agent.stop_watching_room("   ")

        # Start and double stop
        task = agent.start_watching_room("idempotent_room")
        assert task is agent.start_watching_room("idempotent_room")
        assert "idempotent_room" in agent.active_rooms

        await agent.stop_watching_room("idempotent_room")
        assert "idempotent_room" not in agent.active_rooms
        await agent.stop_watching_room("idempotent_room")


@pytest.mark.asyncio
async def test_lifecycle_restarting_room_race_condition():
    """Verify that restarting a room while previous task is cancelling does not orphan the new task."""
    async def run_room_with_cancellation_delay(name: str):
        try:
            while True:
                await asyncio.sleep(0.05)
        except asyncio.CancelledError:
            await asyncio.sleep(0.02)
            raise

    with patch("agent.run_room", side_effect=run_room_with_cancellation_delay):
        t1 = agent.start_watching_room("race_room")
        assert "race_room" in agent.active_rooms

        # Initiate stop in background
        stop_future = asyncio.create_task(agent.stop_watching_room("race_room"))
        await asyncio.sleep(0.001)

        # Immediately start room again while t1 is cancelling
        t2 = agent.start_watching_room("race_room")

        await stop_future
        # Allow done callbacks to fire
        await asyncio.sleep(0.05)

        # The new task t2 should still be tracked in active_rooms
        assert not t2.done(), "New task should still be running"
        assert "race_room" in agent.active_rooms, "Room should still be in active_rooms"
        assert agent.active_rooms.get("race_room") is t2, "active_rooms should track the new task t2"


# ==============================================================================
# SECTION 2: Athlete Fallback Threshold Logic Edge Cases
# ==============================================================================

@pytest.mark.asyncio
async def test_fallback_empty_document():
    """Empty athlete document in MongoDB must strictly fall back to defaults."""
    mock_col = AsyncMock()
    mock_col.find_one.return_value = {}
    agent.users_col = mock_col

    hr_max, hr_rest = await agent.get_athlete_thresholds("ath_empty_doc")
    assert (hr_max, hr_rest) == (185, 50)


@pytest.mark.asyncio
async def test_fallback_null_hr_max_in_profile():
    """Athlete with granted consent but null hr_max should fall back to default hr_max."""
    mock_col = AsyncMock()
    mock_col.find_one.return_value = {
        "_id": "ath_null_max",
        "athlete_profile": {"hr_max": None, "hr_rest": 48},
        "health_consent": {"granted": True},
    }
    agent.users_col = mock_col

    hr_max, hr_rest = await agent.get_athlete_thresholds("ath_null_max")
    assert hr_max == 185
    assert hr_rest == 48


@pytest.mark.asyncio
async def test_fallback_malformed_consent_types():
    """Verify non-boolean or malformed health_consent representations fall back to defaults."""
    malformed_consents = [
        "granted",
        1,
        0,
        {"granted": "true"},  # string instead of bool
        {"granted": "yes"},
        {"granted": 1},
        {"granted": None},
        {"granted": []},
        [],
        True,  # not a dict
    ]

    for idx, bad_consent in enumerate(malformed_consents):
        agent._athlete_thresholds_cache.clear()
        mock_col = AsyncMock()
        mock_col.find_one.return_value = {
            "_id": f"ath_bad_consent_{idx}",
            "athlete_profile": {"hr_max": 195, "hr_rest": 42},
            "health_consent": bad_consent,
        }
        agent.users_col = mock_col

        hr_max, hr_rest = await agent.get_athlete_thresholds(f"ath_bad_consent_{idx}")
        assert (hr_max, hr_rest) == (185, 50), f"Failed on bad_consent: {bad_consent!r}"


@pytest.mark.asyncio
async def test_fallback_malformed_athlete_profiles():
    """Verify corrupted athlete_profile types (strings, lists, primitives) fall back cleanly."""
    corrupted_profiles = [
        "not a profile",
        12345,
        ["hr_max", 190],
        None,
    ]

    for idx, bad_profile in enumerate(corrupted_profiles):
        agent._athlete_thresholds_cache.clear()
        mock_col = AsyncMock()
        mock_col.find_one.return_value = {
            "_id": f"ath_bad_prof_{idx}",
            "athlete_profile": bad_profile,
            "health_consent": {"granted": True},
        }
        agent.users_col = mock_col

        hr_max, hr_rest = await agent.get_athlete_thresholds(f"ath_bad_prof_{idx}")
        assert (hr_max, hr_rest) == (185, 50), f"Failed on bad_profile: {bad_profile!r}"


@pytest.mark.asyncio
async def test_fallback_negative_hr_rest_falls_back_to_default():
    """Negative hr_rest is physiologically invalid and must fall back to ALERT_HR_REST (50)."""
    mock_col = AsyncMock()
    mock_col.find_one.return_value = {
        "_id": "ath_negative_hr_rest",
        "athlete_profile": {"hr_max": 190, "hr_rest": -10},
        "health_consent": {"granted": True},
    }
    agent.users_col = mock_col

    hr_max, hr_rest = await agent.get_athlete_thresholds("ath_negative_hr_rest")
    assert hr_max == 190
    assert hr_rest == 50, f"Expected fallback 50 for negative hr_rest, got {hr_rest}"


@pytest.mark.asyncio
async def test_fallback_corrupted_numeric_strings_in_profile():
    """Non-numeric values in hr_max/hr_rest must fall back to defaults rather than throwing."""
    mock_col = AsyncMock()
    mock_col.find_one.return_value = {
        "_id": "ath_non_numeric",
        "athlete_profile": {"hr_max": "unlimited", "hr_rest": "forty-two"},
        "health_consent": {"granted": True},
    }
    agent.users_col = mock_col

    hr_max, hr_rest = await agent.get_athlete_thresholds("ath_non_numeric")
    assert (hr_max, hr_rest) == (185, 50)


@pytest.mark.asyncio
async def test_unicode_and_special_identities():
    """Identities containing unicode, accented characters, spaces, and symbols must work without error."""
    special_identities = [
        "athlète_français_001",
        "لاعب_رياضي_002",
        "运动员_003",
        "athlete:with:colons",
        "user-uuid-1234-5678",
    ]

    for ident in special_identities:
        agent._athlete_thresholds_cache.clear()
        mock_col = AsyncMock()
        mock_col.find_one.return_value = {
            "_id": ident,
            "athlete_profile": {"hr_max": 191, "hr_rest": 47},
            "health_consent": {"granted": True},
        }
        agent.users_col = mock_col

        hr_max, hr_rest = await agent.get_athlete_thresholds(ident)
        assert (hr_max, hr_rest) == (191, 47)


@pytest.mark.asyncio
async def test_mongo_timeout_and_network_outage():
    """Mongo timeout / asyncio.TimeoutError must be safely caught and return fallback defaults."""
    mock_col = AsyncMock()
    mock_col.find_one.side_effect = asyncio.TimeoutError("MongoDB request timed out after 2000ms")
    agent.users_col = mock_col

    hr_max, hr_rest = await agent.get_athlete_thresholds("ath_timeout")
    assert (hr_max, hr_rest) == (185, 50)


# ==============================================================================
# SECTION 3: Alert Emissions Strict Boundary Verification
# ==============================================================================

@pytest.mark.asyncio
async def test_alerts_personalized_strict_boundary():
    """Personalized athlete (hr_max=172, hr_rest=46): verify exact boundary conditions."""
    with patch("agent.get_athlete_thresholds", new_callable=AsyncMock) as mock_thresh, \
         patch("agent.emit_alert", new_callable=AsyncMock) as mock_emit:
        mock_thresh.return_value = (172, 46)

        # 1. Exactly on hr_max -> NO alert
        await agent.maybe_emit_alerts("r1", "ath", {"heart_rate": "172", "speed": "10.0"}, 100.0, 1.0)
        assert mock_emit.await_count == 0

        # 2. 1 bpm above hr_max -> HR MAX ALERT
        await agent.maybe_emit_alerts("r1", "ath", {"heart_rate": "173", "speed": "10.0"}, 200.0, 2.0)
        assert mock_emit.await_count == 1
        args, kwargs = mock_emit.await_args
        assert args[4] == "hr_max"
        assert args[5] == 173
        assert kwargs.get("threshold") == 172
        mock_emit.reset_mock()

        # 3. Exactly on hr_rest -> NO alert
        await agent.maybe_emit_alerts("r1", "ath", {"heart_rate": "46", "speed": "10.0"}, 300.0, 3.0)
        assert mock_emit.await_count == 0

        # 4. 1 bpm below hr_rest -> HR LOW ALERT
        await agent.maybe_emit_alerts("r1", "ath", {"heart_rate": "45", "speed": "10.0"}, 400.0, 4.0)
        assert mock_emit.await_count == 1
        args, kwargs = mock_emit.await_args
        assert args[4] == "hr_low"
        assert args[5] == 45
        assert kwargs.get("threshold") == 46
        mock_emit.reset_mock()

        # 5. Heart rate == 0 (sensor disconnected) -> NO low alert
        await agent.maybe_emit_alerts("r1", "ath", {"heart_rate": "0", "speed": "10.0"}, 500.0, 5.0)
        assert mock_emit.await_count == 0


@pytest.mark.asyncio
async def test_alerts_default_fallback_strict_boundary():
    """Default fallback (185, 50): verify exact boundary conditions."""
    with patch("agent.get_athlete_thresholds", new_callable=AsyncMock) as mock_thresh, \
         patch("agent.emit_alert", new_callable=AsyncMock) as mock_emit:
        mock_thresh.return_value = (185, 50)

        # At default max 185 -> NO alert
        await agent.maybe_emit_alerts("r1", "ath", {"heart_rate": "185", "speed": "15.0"}, 100.0, 1.0)
        assert mock_emit.await_count == 0

        # 1 bpm above default max -> ALERT with threshold 185
        await agent.maybe_emit_alerts("r1", "ath", {"heart_rate": "186", "speed": "15.0"}, 200.0, 2.0)
        assert mock_emit.await_count == 1
        args, kwargs = mock_emit.await_args
        assert args[4] == "hr_max"
        assert args[5] == 186
        assert kwargs.get("threshold") == 185
        mock_emit.reset_mock()

        # At default rest 50 -> NO alert
        await agent.maybe_emit_alerts("r1", "ath", {"heart_rate": "50", "speed": "15.0"}, 300.0, 3.0)
        assert mock_emit.await_count == 0

        # 1 bpm below default rest -> ALERT with threshold 50
        await agent.maybe_emit_alerts("r1", "ath", {"heart_rate": "49", "speed": "15.0"}, 400.0, 4.0)
        assert mock_emit.await_count == 1
        args, kwargs = mock_emit.await_args
        assert args[4] == "hr_low"
        assert args[5] == 49
        assert kwargs.get("threshold") == 50


@pytest.mark.asyncio
async def test_alerts_dual_hr_and_speed_emissions():
    """Telemetry packet exceeding both HR max and speed max must trigger both alerts independently."""
    with patch("agent.get_athlete_thresholds", new_callable=AsyncMock) as mock_thresh, \
         patch("agent.emit_alert", new_callable=AsyncMock) as mock_emit:
        mock_thresh.return_value = (180, 50)

        event = {"heart_rate": "195", "speed": "48.2"}
        await agent.maybe_emit_alerts("r1", "ath", event, 1000.0, 10.0)

        assert mock_emit.await_count == 2
        emitted_types = [call[0][4] for call in mock_emit.await_args_list]
        assert "hr_max" in emitted_types
        assert "speed_max" in emitted_types


@pytest.mark.asyncio
async def test_alerts_malformed_telemetry_strings_graceful():
    """Corrupted telemetry values ('abc', 'N/A') should not crash maybe_emit_alerts."""
    with patch("agent.get_athlete_thresholds", new_callable=AsyncMock) as mock_thresh, \
         patch("agent.emit_alert", new_callable=AsyncMock) as mock_emit:
        mock_thresh.return_value = (185, 50)

        # Should not raise ValueError
        await agent.maybe_emit_alerts("r1", "ath", {"heart_rate": "invalid_hr", "speed": "bad_speed"}, 1000.0, 10.0)
        assert mock_emit.await_count == 0

