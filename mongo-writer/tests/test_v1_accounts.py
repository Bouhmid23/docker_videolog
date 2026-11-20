"""Tests pour /v1/me, onboarding, profil sportif, interdiction < 15 ans et consentement."""
from datetime import date, timedelta

import pytest


@pytest.mark.asyncio
async def test_me_and_onboarding_club(client, mock_claims):
    r = await client.get("/v1/me")
    assert r.status_code == 200
    data = r.json()
    assert data["needs_onboarding"] is True
    assert data["uid"] == "user-1"

    # E-mail non vérifié -> 403
    mock_claims["email_verified"] = False
    r = await client.post(
        "/v1/me/onboarding",
        json={
            "account_type": "club",
            "organization_name": "Club Cycliste Lyon",
            "sports": ["cyclisme"],
            "city": "Lyon",
            "first_name": "Jean",
            "last_name": "Dupont",
        },
    )
    assert r.status_code == 403

    mock_claims["email_verified"] = True
    r = await client.post(
        "/v1/me/onboarding",
        json={
            "account_type": "club",
            "organization_name": "Club Cycliste Lyon",
            "sports": ["cyclisme"],
            "city": "Lyon",
            "first_name": "Jean",
            "last_name": "Dupont",
        },
    )
    assert r.status_code == 201
    created = r.json()
    assert created["organization"]["type"] == "club"
    assert created["membership"]["role"] == "owner"

    # Second onboarding -> 409
    r2 = await client.post(
        "/v1/me/onboarding",
        json={
            "account_type": "solo_coach",
            "organization_name": "Autre",
            "sports": [],
            "first_name": "Jean",
            "last_name": "Dupont",
        },
    )
    assert r2.status_code == 409

    me = await client.get("/v1/me")
    assert me.json()["needs_onboarding"] is False
    assert len(me.json()["memberships"]) == 1


@pytest.mark.asyncio
async def test_athlete_profile_age_and_health_consent(client):
    under_15 = (date.today() - timedelta(days=14 * 365)).isoformat()
    r = await client.put(
        "/v1/me/athlete-profile",
        json={
            "sport": "cyclisme",
            "level": "loisir",
            "birth_date": under_15,
            "height_cm": 165,
            "health_consent": False,
        },
    )
    assert r.status_code == 422

    adult_birth = (date.today() - timedelta(days=22 * 365)).isoformat()
    # Champs santé sans consentement -> 422
    r = await client.put(
        "/v1/me/athlete-profile",
        json={
            "sport": "cyclisme",
            "level": "nationale",
            "birth_date": adult_birth,
            "height_cm": 178,
            "weight_kg": 70,
            "hr_max": 192,
            "hr_rest": 48,
            "health_consent": False,
        },
    )
    assert r.status_code == 422

    # Avec consentement -> 200
    r = await client.put(
        "/v1/me/athlete-profile",
        json={
            "sport": "cyclisme",
            "level": "nationale",
            "birth_date": adult_birth,
            "height_cm": 178,
            "weight_kg": 70,
            "hr_max": 192,
            "hr_rest": 48,
            "health_consent": True,
        },
    )
    assert r.status_code == 200
    user = r.json()["user"]
    assert user["athlete_profile"]["hr_max"] == 192
    assert user["health_consent"]["granted"] is True

    # Retrait du consentement -> efface les champs santé
    r_del = await client.delete("/v1/me/health-consent")
    assert r_del.status_code == 200
    user2 = r_del.json()["user"]
    assert user2["health_consent"] is None
    assert user2["athlete_profile"]["hr_max"] is None
    assert user2["athlete_profile"]["weight_kg"] is None
    assert user2["athlete_profile"]["height_cm"] == 178
