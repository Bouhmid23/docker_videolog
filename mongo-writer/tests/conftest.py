"""Fixtures partagées pour les tests de l'API Videolog."""
import os
import sys
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from mongomock_motor import AsyncMongoMockClient

# Variables requises avant import de api.py / db.py
os.environ.setdefault("LIVEKIT_API_KEY", "test-key")
os.environ.setdefault("LIVEKIT_API_SECRET", "test-secret-012345678901234567890123")
os.environ.setdefault("MINIO_ACCESS_KEY", "minioadmin")
os.environ.setdefault("MINIO_SECRET_KEY", "minioadmin")
os.environ.setdefault("MINIO_PUBLIC_URL", "https://minio.videolog.test")
os.environ.setdefault("LIVEKIT_PUBLIC_URL", "wss://livekit.videolog.test")

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import db as db_mod  # noqa: E402
import auth_firebase  # noqa: E402
import api as api_mod  # noqa: E402
from routers import accounts as accounts_router  # noqa: E402
from routers import invitations as invitations_router  # noqa: E402
from routers import organizations as orgs_router  # noqa: E402
from routers import sessions as sessions_router  # noqa: E402


@pytest.fixture
def mock_claims():
    state = {
        "uid": "user-1",
        "email": "coach@club.fr",
        "email_verified": True,
        "platform_admin": False,
    }
    return state


@pytest_asyncio.fixture
async def client(mock_claims):
    mock_client = AsyncMongoMockClient()
    mock_db = mock_client["videolog_test"]

    # Patcher les références de collections dans db et les modules déjà importés
    mapping = {
        "col": mock_db.telemetry,
        "recs": mock_db.recordings,
        "sess": mock_db.sessions,
        "alerts": mock_db.alerts,
        "rooms": mock_db.room_state,
        "users_col": mock_db.users,
        "orgs_col": mock_db.organizations,
        "memberships_col": mock_db.memberships,
        "groups_col": mock_db.groups,
        "invitations_col": mock_db.invitations,
        "markers_col": mock_db.markers,
        "threshold_history_col": mock_db.threshold_history,
        "activities_col": mock_db.activities,
        "activity_streams_col": mock_db.activity_streams,
        "activity_comments_col": mock_db.activity_comments,
        "video_assets_col": mock_db.video_assets,
        "notifications_col": mock_db.notifications,
        "join_requests_col": mock_db.join_requests,
        "workout_comments_col": mock_db.workout_comments,
        "workout_templates_col": mock_db.workout_templates,
        "planned_workouts_col": mock_db.planned_workouts,
        "training_plans_col": mock_db.training_plans,
        "goals_col": mock_db.goals,
        "daily_metrics_col": mock_db.daily_metrics,
        "wellness_entries_col": mock_db.wellness_entries,
    }
    for attr, coll in mapping.items():
        setattr(db_mod, attr, coll)
        for mod in (
            auth_firebase,
            api_mod,
            accounts_router,
            orgs_router,
            invitations_router,
            sessions_router,
        ):
            if hasattr(mod, attr):
                setattr(mod, attr, coll)

    async def override_firebase_user():
        return dict(mock_claims)

    api_mod.app.dependency_overrides[auth_firebase.firebase_user] = override_firebase_user
    api_mod.app.state.redis = None

    transport = ASGITransport(app=api_mod.app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac

    api_mod.app.dependency_overrides.clear()
