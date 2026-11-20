"""Tests pour /v1/orgs/{id}/sessions et sécurisation de /token, /mobile/rooms et /health."""
import pytest

import auth_firebase
import db as db_mod


async def _setup_org_and_athlete(client, mock_claims):
    mock_claims.update({"uid": "coach-1", "email": "coach@club.fr", "email_verified": True})
    r = await client.post(
        "/v1/me/onboarding",
        json={
            "account_type": "solo_coach",
            "organization_name": "Coach Pro",
            "sports": ["cyclisme"],
            "first_name": "Marc",
            "last_name": "Lenoir",
        },
    )
    org_id = r.json()["organization"]["id"]

    inv = await client.post(
        f"/v1/orgs/{org_id}/invitations",
        json={"email": "ath@club.fr", "role": "athlete", "group_ids": []},
    )
    token = inv.json()["invite_url"].split("/invite/")[-1]
    mock_claims.update({"uid": "ath-1", "email": "ath@club.fr", "email_verified": True})
    await client.post(f"/v1/invitations/{token}/accept", json={"age_confirmed": True})

    # Un autre utilisateur hors organisation
    mock_claims.update({"uid": "outsider", "email": "out@other.fr", "email_verified": True})
    await client.post(
        "/v1/me/onboarding",
        json={
            "account_type": "solo_coach",
            "organization_name": "Autre Club",
            "sports": ["triathlon"],
            "first_name": "Paul",
            "last_name": "Autre",
        },
    )
    return org_id


@pytest.mark.asyncio
async def test_session_creation_and_mobile_isolation(client, mock_claims):
    org_id = await _setup_org_and_athlete(client, mock_claims)

    mock_claims.update({"uid": "coach-1", "email": "coach@club.fr"})
    s = await client.post(
        f"/v1/orgs/{org_id}/sessions",
        json={"title": "Sortie seuil", "participant_uids": ["ath-1"]},
    )
    assert s.status_code == 201
    room_name = s.json()["session"]["room_name"]

    # L'athlète participant voit la room avec can_control_recording=False et obtient un token
    mock_claims.update({"uid": "ath-1", "email": "ath@club.fr"})
    mob = await client.get("/mobile/rooms")
    assert mob.status_code == 200
    assert mob.json()["count"] == 1
    assert mob.json()["rooms"][0]["room_name"] == room_name
    assert mob.json()["rooms"][0]["can_control_recording"] is False

    tok = await client.post("/token", json={"room": room_name})
    assert tok.status_code == 200
    assert tok.json()["room"] == room_name

    # L'athlète ne peut pas démarrer l'enregistrement
    rec_start = await client.post(f"/mobile/rooms/{room_name}/recording/start")
    assert rec_start.status_code == 403

    # L'outsider ne voit rien et reçoit 403 sur /token
    mock_claims.update({"uid": "outsider", "email": "out@other.fr"})
    mob_out = await client.get("/mobile/rooms")
    assert mob_out.status_code == 200
    assert mob_out.json()["count"] == 0

    tok_out = await client.post("/token", json={"room": room_name})
    assert tok_out.status_code == 403


@pytest.mark.asyncio
async def test_legacy_room_flag_and_health_smoke(client, mock_claims, monkeypatch):
    await db_mod.rooms.insert_one({"room_name": "legacy-room", "finished": False, "count": 0})
    mock_claims.update({"uid": "outsider", "email": "out@other.fr"})

    monkeypatch.setattr(auth_firebase, "LEGACY_OPEN_ROOMS", False)
    r_denied = await client.post("/token", json={"room": "legacy-room"})
    assert r_denied.status_code == 403

    monkeypatch.setattr(auth_firebase, "LEGACY_OPEN_ROOMS", True)
    r_ok = await client.post("/token", json={"room": "legacy-room"})
    assert r_ok.status_code == 200

    health = await client.get("/health")
    assert health.status_code == 200
    assert health.json()["status"] == "ok"


@pytest.mark.asyncio
async def test_legacy_unauthenticated_poc_routes_removed(client):
    for method, path in [
        ("GET", "/rooms"),
        ("POST", "/rooms"),
        ("GET", "/rooms/poc-room"),
        ("POST", "/rooms/poc-room/remove-participant"),
        ("GET", "/recordings"),
        ("GET", "/recordings/files"),
        ("GET", "/recordings/eg-1"),
        ("POST", "/recordings/start"),
        ("POST", "/recordings/stop"),
        ("GET", "/recordings/eg-1/telemetry"),
        ("GET", "/telemetry/latest"),
        ("GET", "/telemetry/history"),
        ("GET", "/telemetry/series"),
        ("GET", "/telemetry/export"),
    ]:
        resp = await client.request(method, path)
        assert resp.status_code == 404, f"{method} {path} should return 404, got {resp.status_code}"


@pytest.mark.asyncio
async def test_room_lobby_online_presence_with_string_and_dict_participants(
    client, mock_claims
):
    org_id = await _setup_org_and_athlete(client, mock_claims)
    mock_claims.update({"uid": "coach-1", "email": "coach@club.fr"})
    s = await client.post(
        f"/v1/orgs/{org_id}/sessions",
        json={"title": "Séance Musculation Live", "participant_uids": ["ath-1"]},
    )
    assert s.status_code == 201
    room_name = s.json()["session"]["room_name"]

    # Cas 1 : egress-webhook enregistre participants sous forme de liste de chaînes ["coach-1:web", "ath-1"]
    await db_mod.rooms.update_one(
        {"room_name": room_name},
        {"$set": {"participants": ["coach-1:web", "ath-1"], "count": 2}},
    )
    lobby_res = await client.get(f"/rooms/{room_name}/lobby")
    assert lobby_res.status_code == 200
    invited = {
        p["uid"]: p for p in lobby_res.json()["invited_participants"]
    }
    assert invited["coach-1"]["connected"] is True
    assert invited["ath-1"]["connected"] is True

    # Cas 2 : format dictionnaire [{"identity": "coach-1:web"}]
    await db_mod.rooms.update_one(
        {"room_name": room_name},
        {"$set": {"participants": [{"identity": "coach-1:web"}], "count": 1}},
    )
    lobby_res2 = await client.get(f"/rooms/{room_name}/lobby")
    assert lobby_res2.status_code == 200
    invited2 = {
        p["uid"]: p for p in lobby_res2.json()["invited_participants"]
    }
    assert invited2["coach-1"]["connected"] is True
    assert invited2["ath-1"]["connected"] is False


@pytest.mark.asyncio
async def test_livekit_webhook_egress_ended_parses_protobuf_string_size(
    client, monkeypatch
):
    from livekit import api as lkapi

    monkeypatch.setattr(
        lkapi.WebhookReceiver,
        "receive",
        lambda self, body, auth: None,
    )

    await db_mod.rooms.insert_one(
        {
            "room_name": "room-egress-str",
            "org_id": "org-1",
            "mobile_recording": {"egress_id": "EG_STR_1", "status": "recording"},
        }
    )

    payload = {
        "event": "egress_ended",
        "egressInfo": {
            "egressId": "EG_STR_1",
            "roomName": "room-egress-str",
            "endedAt": "1795096474801000000",
            "fileResults": [
                {
                    "filename": "room-egress-str/20261119_135333/recording.mp4",
                    "size": "5242880",
                    "duration": "45000000000",
                    "startedAt": "1795096420000000000",
                    "endedAt": "1795096465000000000",
                }
            ],
        },
    }
    res = await client.post(
        "/webhook/livekit",
        json=payload,
        headers={"Authorization": "Bearer dummy-signature"},
    )
    assert res.status_code == 200

    rec = await db_mod.recs.find_one({"egress_id": "EG_STR_1"})
    assert rec is not None
    assert rec["status"] == "completed"
    assert rec["duration_s"] == 45.0
    assert rec["files"][0]["size_bytes"] == 5242880
    assert rec["files"][0]["size_mb"] == 5.0

    room = await db_mod.rooms.find_one({"room_name": "room-egress-str"})
    assert room["mobile_recording"]["status"] == "completed"



