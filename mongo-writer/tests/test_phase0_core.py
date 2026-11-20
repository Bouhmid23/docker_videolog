"""Tests Phase 0 : abstraction S3, paramètres d'enregistrement par org, worker Arq et routers extraits."""
import pytest

import core_config
import db as db_mod
from storage import ObjectStorage, StorageConfig
from worker.main import finalize_live_session


def test_storage_config_and_presign(monkeypatch):
    monkeypatch.setenv("S3_ENDPOINT", "http://s3.internal:9000")
    monkeypatch.setenv("S3_PUBLIC_ENDPOINT", "https://cdn.videolog.test")
    monkeypatch.setenv("S3_ACCESS_KEY", "ak-test")
    monkeypatch.setenv("S3_SECRET_KEY", "sk-test")
    monkeypatch.setenv("S3_BUCKET", "videos-test")

    cfg = StorageConfig.from_env()
    assert cfg.endpoint_url == "http://s3.internal:9000"
    assert cfg.public_endpoint_url == "https://cdn.videolog.test"
    assert cfg.bucket == "videos-test"

    store = ObjectStorage(cfg)
    url = store.presign_get("/room-1/rec.mp4", expires_in=600)
    assert url.startswith("https://cdn.videolog.test/videos-test/room-1/rec.mp4")
    assert "X-Amz-Signature=" in url
    assert store.presign_get("") == ""


@pytest.mark.asyncio
async def test_org_max_recording_duration_setting(client, mock_claims):
    await db_mod.users_col.insert_one(
        {
            "_id": mock_claims["uid"],
            "email": mock_claims["email"],
            "first_name": "Coach",
            "last_name": "Pro",
        }
    )
    await db_mod.orgs_col.insert_one(
        {
            "_id": "org-dur",
            "type": "club",
            "name": "Club Endurance",
            "owner_uid": mock_claims["uid"],
            "status": "active",
        }
    )
    await db_mod.memberships_col.insert_one(
        {
            "org_id": "org-dur",
            "uid": mock_claims["uid"],
            "role": "owner",
            "status": "active",
            "group_ids": [],
        }
    )

    # Défaut avant paramétrage
    default_dur = await core_config.get_max_recording_seconds({"org_id": "org-dur"})
    assert default_dur == core_config.RECORDING_MAX_DURATION_SECONDS

    # Mise à jour à 7200 s (2h)
    resp = await client.patch(
        "/v1/orgs/org-dur",
        json={"settings": {"max_recording_s": 7200}},
    )
    assert resp.status_code == 200
    assert resp.json()["organization"]["settings"]["max_recording_s"] == 7200

    custom_dur = await core_config.get_max_recording_seconds({"org_id": "org-dur"})
    assert custom_dur == 7200


@pytest.mark.asyncio
async def test_worker_finalize_live_session(client):
    await db_mod.rooms.insert_one({"room_name": "room-w1", "org_id": "org-1"})
    await db_mod.col.insert_many(
        [
            {"room_name": "room-w1", "pts_ms": 1000},
            {"room_name": "room-w1", "pts_ms": 2000},
        ]
    )
    await db_mod.recs.insert_one({"egress_id": "eg-1", "room_name": "room-w1"})

    res = await finalize_live_session({}, "room-w1")
    assert res["status"] == "finalized"
    assert res["telemetry_sample_count"] == 2
    assert res["recordings_count"] == 1

    updated = await db_mod.rooms.find_one({"room_name": "room-w1"})
    assert updated["telemetry_sample_count"] == 2
    assert updated["recordings_count"] == 1
    assert updated.get("finalized_at") is not None

    missing = await finalize_live_session({}, "unknown-room")
    assert missing["status"] == "not_found"


@pytest.mark.asyncio
async def test_extracted_routers_health_and_token_validation(client):
    health = await client.get("/health")
    assert health.status_code == 200
    assert health.json()["status"] == "ok"

    bad_token = await client.post("/token", json={"room": ""})
    assert bad_token.status_code == 422
