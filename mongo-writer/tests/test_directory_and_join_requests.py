"""Tests de bout en bout pour l'annuaire mobile Clubs/Coachs et les demandes d'adhésion."""
from datetime import date, timedelta

import pytest

import db as db_mod


def _switch_user(mock_claims: dict, uid: str, email: str, *, verified: bool = True):
    mock_claims["uid"] = uid
    mock_claims["email"] = email
    mock_claims["email_verified"] = verified
    mock_claims["platform_admin"] = False


async def _setup_club_and_solo_coach(client, mock_claims):
    # 1. Créer un Club (owner-club) avec cyclisme + groupe home_trainer + coach profile
    _switch_user(mock_claims, "owner-club", "owner@vclyon.fr")
    res = await client.post(
        "/v1/me/onboarding",
        json={
            "account_type": "club",
            "organization_name": "VC Lyon Endurance",
            "sports": ["cyclisme"],
            "city": "Lyon",
            "first_name": "Marc",
            "last_name": "Martin",
            "age_confirmed": True,
        },
    )
    assert res.status_code == 201
    club_id = res.json()["organization"]["id"]

    # Enrichir la description du club
    res = await client.patch(
        f"/v1/orgs/{club_id}",
        json={
            "description": "Club route & home-trainer connecté à Lyon.",
            "is_public": True,
            "accepting_join_requests": True,
        },
    )
    assert res.status_code == 200
    assert res.json()["organization"]["description"] == "Club route & home-trainer connecté à Lyon."

    # Enrichir le profil coach du propriétaire (home_trainer élite + cyclisme nationale)
    res = await client.put(
        "/v1/me/coach-profile",
        json={
            "sports": [
                {"sport": "home_trainer", "level": "élite", "years_experience": 9},
                {"sport": "cyclisme", "level": "nationale", "years_experience": 12},
            ],
            "certifications": "DEJEPS Cyclisme",
            "bio": "Préparation puissance et contre-la-montre.",
        },
    )
    assert res.status_code == 200

    # Créer un groupe indoor home_trainer
    res = await client.post(
        f"/v1/orgs/{club_id}/groups",
        json={
            "name": "Groupe Home-Trainer Hiver",
            "sport": "home_trainer",
            "coach_uids": ["owner-club"],
        },
    )
    assert res.status_code == 201
    group_ht_id = res.json()["group"]["id"]

    # 2. Créer un Coach privé (solo-coach-1) en aviron_indoor & cross_training
    _switch_user(mock_claims, "solo-coach-1", "claire@coach-ergo.fr")
    res = await client.post(
        "/v1/me/onboarding",
        json={
            "account_type": "solo_coach",
            "organization_name": "Claire Ergo Coaching",
            "sports": ["aviron_indoor", "cross_training"],
            "city": "Bordeaux",
            "first_name": "Claire",
            "last_name": "Dupuis",
            "age_confirmed": True,
        },
    )
    assert res.status_code == 201
    solo_id = res.json()["organization"]["id"]

    res = await client.put(
        "/v1/me/coach-profile",
        json={
            "sports": [
                {"sport": "aviron_indoor", "level": "élite", "years_experience": 7},
                {"sport": "cross_training", "level": "compétition régionale", "years_experience": 4},
            ],
            "certifications": "BPJEPS + Concept2 Certified",
            "bio": "Spécialiste rameur 2000m et HIIT.",
        },
    )
    assert res.status_code == 200

    # 3. Créer un Club privé masqué (is_public=False)
    _switch_user(mock_claims, "owner-private", "secret@club.fr")
    res = await client.post(
        "/v1/me/onboarding",
        json={
            "account_type": "club",
            "organization_name": "Club Privé Secret",
            "sports": ["triathlon"],
            "city": "Paris",
            "first_name": "Jean",
            "last_name": "Secret",
            "age_confirmed": True,
        },
    )
    assert res.status_code == 201
    private_id = res.json()["organization"]["id"]
    await client.patch(f"/v1/orgs/{private_id}", json={"is_public": False})

    return {
        "club_id": club_id,
        "group_ht_id": group_ht_id,
        "solo_id": solo_id,
        "private_id": private_id,
    }


@pytest.mark.asyncio
async def test_sports_catalog_and_directory_filters(client, mock_claims):
    # 1. Catalogue des sports complet + filtres
    r_all = await client.get("/v1/sports")
    assert r_all.status_code == 200
    all_sports = r_all.json()["sports"]
    assert len(all_sports) == 17
    codes = {s["code"] for s in all_sports}
    assert {"cyclisme", "home_trainer", "aviron_indoor", "skierg", "cross_training"}.issubset(codes)

    r_indoor = await client.get("/v1/sports?is_indoor=true")
    assert r_indoor.status_code == 200
    assert all(s["is_indoor"] is True for s in r_indoor.json()["sports"])

    r_ergo = await client.get("/v1/sports?category=ergo")
    assert r_ergo.status_code == 200
    assert {s["code"] for s in r_ergo.json()["sports"]} == {
        "home_trainer",
        "tapis_course",
        "aviron_indoor",
        "skierg",
    }

    ids = await _setup_club_and_solo_coach(client, mock_claims)

    # 2. Un athlète mobile nouvellement inscrit (sans org) explore l'annuaire
    _switch_user(mock_claims, "athlete-mob-1", "alice@mobile.fr")
    r_dir = await client.get("/v1/directory/organizations")
    assert r_dir.status_code == 200
    body = r_dir.json()
    assert body["total"] == 2
    org_ids = {o["id"] for o in body["organizations"]}
    assert org_ids == {ids["club_id"], ids["solo_id"]}
    assert ids["private_id"] not in org_ids  # Club masqué exclu

    # Vérifier qu'aucune PII (email) n'est exposée sur les coachs
    club_item = next(o for o in body["organizations"] if o["id"] == ids["club_id"])
    assert club_item["is_member"] is False
    assert club_item["pending_request_id"] is None
    assert "home_trainer" in club_item["sports"]  # agrégé via groupe + coach_profile
    assert "cyclisme" in club_item["sports"]
    ht_detail = next(s for s in club_item["sports_details"] if s["code"] == "home_trainer")
    assert ht_detail["is_indoor"] is True
    assert ht_detail["category"] == "ergo"
    assert ht_detail["supports_power"] is True
    assert ht_detail["supports_gps"] is False
    assert ht_detail["levels"] == ["élite"]
    assert ht_detail["max_years_experience"] == 9
    assert len(ht_detail["groups"]) == 1
    assert ht_detail["groups"][0]["name"] == "Groupe Home-Trainer Hiver"
    assert len(ht_detail["coaches"]) == 1
    assert ht_detail["coaches"][0]["name"] == "Marc Martin"
    for c in club_item["coaches"]:
        assert "email" not in c
        assert "phone" not in c

    # Filtre par type=solo_coach
    r_solo = await client.get("/v1/directory/organizations?type=solo_coach")
    assert r_solo.status_code == 200
    assert r_solo.json()["total"] == 1
    solo_item = r_solo.json()["organizations"][0]
    assert solo_item["id"] == ids["solo_id"]
    # Fallback de description sur la bio du coach privé
    assert "rameur 2000m" in (solo_item["description"] or "")

    # Filtre par sport=home_trainer & indoor_only=true & city=lyon & q=DEJEPS
    r_ht = await client.get("/v1/directory/organizations?sport=home_trainer&indoor_only=true")
    assert r_ht.status_code == 200
    assert [o["id"] for o in r_ht.json()["organizations"]] == [ids["club_id"]]

    r_q = await client.get("/v1/directory/organizations?q=Concept2")
    assert r_q.status_code == 200
    assert [o["id"] for o in r_q.json()["organizations"]] == [ids["solo_id"]]

    r_city = await client.get("/v1/directory/organizations?city=lyon")
    assert r_city.status_code == 200
    assert [o["id"] for o in r_city.json()["organizations"]] == [ids["club_id"]]

    # Fiche détaillée publique (et 404 sur club privé)
    r_detail = await client.get(f"/v1/directory/organizations/{ids['club_id']}")
    assert r_detail.status_code == 200
    assert r_detail.json()["organization"]["id"] == ids["club_id"]
    assert r_detail.json()["my_join_request"] is None

    r_priv = await client.get(f"/v1/directory/organizations/{ids['private_id']}")
    assert r_priv.status_code == 404


@pytest.mark.asyncio
async def test_join_request_full_approval_workflow_and_validations(client, mock_claims):
    ids = await _setup_club_and_solo_coach(client, mock_claims)

    # Athlète mobile soumet une demande vers le club (groupe Home-Trainer)
    _switch_user(mock_claims, "athlete-mob-2", "bob@mobile.fr")
    r_req = await client.post(
        f"/v1/orgs/{ids['club_id']}/join-requests",
        json={
            "requested_sports": ["home_trainer", "cyclisme"],
            "requested_group_id": ids["group_ht_id"],
            "target_coach_uid": "owner-club",
            "message": "Bonjour, je cherche un suivi structuré sur home-trainer.",
            "first_name": "Bob",
            "last_name": "Rider",
            "age_confirmed": True,
        },
    )
    assert r_req.status_code == 201
    req_id = r_req.json()["join_request"]["id"]
    assert r_req.json()["join_request"]["status"] == "pending"
    assert r_req.json()["join_request"]["requested_sports"] == ["home_trainer", "cyclisme"]

    # Doublon bloqué (409)
    r_dup = await client.post(
        f"/v1/orgs/{ids['club_id']}/join-requests",
        json={"requested_sports": ["home_trainer"], "age_confirmed": True},
    )
    assert r_dup.status_code == 409

    # Vérifier pending_request_id visible dans l'annuaire et /v1/me/join-requests
    r_detail = await client.get(f"/v1/directory/organizations/{ids['club_id']}")
    assert r_detail.json()["organization"]["pending_request_id"] == req_id
    assert r_detail.json()["my_join_request"]["status"] == "pending"

    r_my_reqs = await client.get("/v1/me/join-requests")
    assert r_my_reqs.status_code == 200
    assert r_my_reqs.json()["count"] == 1
    assert r_my_reqs.json()["join_requests"][0]["org_name"] == "VC Lyon Endurance"
    assert r_my_reqs.json()["join_requests"][0]["requested_group_name"] == "Groupe Home-Trainer Hiver"

    # Vérifier que le propriétaire du club a reçu une notification in-app
    _switch_user(mock_claims, "owner-club", "owner@vclyon.fr")
    r_notifs = await client.get("/v1/me/notifications")
    assert r_notifs.status_code == 200
    assert any(n["type"] == "join_request_created" for n in r_notifs.json()["items"])

    # Le propriétaire consulte les demandes reçues
    r_org_reqs = await client.get(f"/v1/orgs/{ids['club_id']}/join-requests")
    assert r_org_reqs.status_code == 200
    assert r_org_reqs.json()["pending_count"] == 1
    item = r_org_reqs.json()["join_requests"][0]
    assert item["applicant"]["first_name"] == "Bob"
    assert item["applicant"]["email"] == "bob@mobile.fr"
    assert item["message"] == "Bonjour, je cherche un suivi structuré sur home-trainer."

    # Le propriétaire approuve la demande
    r_app = await client.post(
        f"/v1/orgs/{ids['club_id']}/join-requests/{req_id}/approve",
        json={
            "role": "athlete",
            "group_ids": [ids["group_ht_id"]],
            "decision_note": "Bienvenue au VC Lyon !",
        },
    )
    assert r_app.status_code == 200
    assert r_app.json()["status"] == "approved"
    assert r_app.json()["group_ids"] == [ids["group_ht_id"]]

    # L'athlète est désormais membre actif dans /v1/me et a reçu une notification
    _switch_user(mock_claims, "athlete-mob-2", "bob@mobile.fr")
    r_me = await client.get("/v1/me")
    assert r_me.status_code == 200
    assert r_me.json()["needs_onboarding"] is False
    assert len(r_me.json()["memberships"]) == 1
    assert r_me.json()["memberships"][0]["org_id"] == ids["club_id"]
    assert r_me.json()["memberships"][0]["group_ids"] == [ids["group_ht_id"]]

    r_ath_notifs = await client.get("/v1/me/notifications")
    assert any(n["type"] == "join_request_approved" for n in r_ath_notifs.json()["items"])

    # Re-demander alors que déjà membre -> 409
    r_already = await client.post(
        f"/v1/orgs/{ids['club_id']}/join-requests",
        json={"requested_sports": ["cyclisme"], "age_confirmed": True},
    )
    assert r_already.status_code == 409


@pytest.mark.asyncio
async def test_join_request_reject_cancel_closed_and_underage(client, mock_claims):
    ids = await _setup_club_and_solo_coach(client, mock_claims)

    # 1. Refus d'une demande + nouvelle demande possible ensuite
    _switch_user(mock_claims, "athlete-mob-3", "carla@mobile.fr")
    r1 = await client.post(
        f"/v1/orgs/{ids['solo_id']}/join-requests",
        json={
            "requested_sports": ["aviron_indoor"],
            "message": "Je vise 6m50 au 2000m.",
            "first_name": "Carla",
            "last_name": "Row",
        },
    )
    assert r1.status_code == 201
    req1_id = r1.json()["join_request"]["id"]

    _switch_user(mock_claims, "solo-coach-1", "claire@coach-ergo.fr")
    r_rej = await client.post(
        f"/v1/orgs/{ids['solo_id']}/join-requests/{req1_id}/reject",
        json={"decision_note": "Planning complet ce trimestre."},
    )
    assert r_rej.status_code == 200
    assert r_rej.json()["status"] == "rejected"

    # L'athlète voit le refus + peut annuler une nouvelle demande
    _switch_user(mock_claims, "athlete-mob-3", "carla@mobile.fr")
    r_notifs = await client.get("/v1/me/notifications")
    assert any(n["type"] == "join_request_rejected" for n in r_notifs.json()["items"])

    r2 = await client.post(
        f"/v1/orgs/{ids['solo_id']}/join-requests",
        json={"requested_sports": ["cross_training"]},
    )
    assert r2.status_code == 201
    req2_id = r2.json()["join_request"]["id"]

    r_cancel = await client.delete(f"/v1/me/join-requests/{req2_id}")
    assert r_cancel.status_code == 200
    assert r_cancel.json()["status"] == "cancelled"

    # 2. Fermeture des demandes d'adhésion (accepting_join_requests=False -> 403)
    _switch_user(mock_claims, "solo-coach-1", "claire@coach-ergo.fr")
    await client.patch(
        f"/v1/orgs/{ids['solo_id']}", json={"accepting_join_requests": False}
    )
    _switch_user(mock_claims, "athlete-mob-3", "carla@mobile.fr")
    r_closed = await client.post(
        f"/v1/orgs/{ids['solo_id']}/join-requests",
        json={"requested_sports": ["aviron_indoor"]},
    )
    assert r_closed.status_code == 403

    # 3. Interdiction < 15 ans (age_confirmed=False -> 422 et birth_date < 15 ans -> 422)
    r_no_age = await client.post(
        f"/v1/orgs/{ids['club_id']}/join-requests",
        json={"requested_sports": ["cyclisme"], "age_confirmed": False},
    )
    assert r_no_age.status_code == 422

    young_bd = (date.today() - timedelta(days=12 * 365)).isoformat()
    await db_mod.users_col.update_one(
        {"_id": "athlete-young"},
        {
            "$set": {
                "email": "young@mobile.fr",
                "athlete_profile": {"sport": "cyclisme", "level": "loisir", "birth_date": young_bd},
            }
        },
        upsert=True,
    )
    _switch_user(mock_claims, "athlete-young", "young@mobile.fr")
    r_young = await client.post(
        f"/v1/orgs/{ids['club_id']}/join-requests",
        json={"requested_sports": ["cyclisme"], "age_confirmed": True},
    )
    assert r_young.status_code == 422
