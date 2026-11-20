"""Tests pour organisations, groupes, invitations et isolation multi-tenant."""
import pytest


async def _create_org(client, mock_claims, uid="owner-1", email="owner@club.fr", acc_type="club"):
    mock_claims.update({"uid": uid, "email": email, "email_verified": True})
    r = await client.post(
        "/v1/me/onboarding",
        json={
            "account_type": acc_type,
            "organization_name": f"Org {uid}",
            "sports": ["cyclisme"],
            "first_name": "Alice",
            "last_name": "Martin",
        },
    )
    assert r.status_code == 201
    return r.json()["organization"]["id"]


@pytest.mark.asyncio
async def test_invitation_lifecycle_and_solo_coach_restriction(client, mock_claims):
    solo_org = await _create_org(client, mock_claims, uid="solo-1", email="solo@coach.fr", acc_type="solo_coach")
    # Un solo_coach ne peut pas inviter un autre coach
    r_coach = await client.post(
        f"/v1/orgs/{solo_org}/invitations",
        json={"email": "autre@coach.fr", "role": "coach", "group_ids": []},
    )
    assert r_coach.status_code == 400

    # Mais il peut inviter un athlète
    r_inv = await client.post(
        f"/v1/orgs/{solo_org}/invitations",
        json={"email": "athlete1@test.fr", "role": "athlete", "group_ids": []},
    )
    assert r_inv.status_code == 201
    invite_url = r_inv.json()["invite_url"]
    assert invite_url and "/invite/" in invite_url
    token = invite_url.split("/invite/")[-1]

    # Aperçu public (e-mail masqué)
    prev = await client.get(f"/v1/invitations/{token}")
    assert prev.status_code == 200
    assert prev.json()["masked_email"] == "a***@test.fr"
    assert prev.json()["role"] == "athlete"

    # Mauvais e-mail -> 403
    mock_claims.update({"uid": "ath-1", "email": "intrus@test.fr", "email_verified": False})
    bad = await client.post(f"/v1/invitations/{token}/accept", json={"age_confirmed": True})
    assert bad.status_code == 403

    # Bon e-mail -> 200 (même si email_verified=False au départ, le lien valide l'adresse)
    mock_claims.update({"uid": "ath-1", "email": "athlete1@test.fr", "email_verified": False})
    ok = await client.post(
        f"/v1/invitations/{token}/accept",
        json={"first_name": "Bob", "last_name": "Durand", "age_confirmed": True},
    )
    assert ok.status_code == 200
    assert ok.json()["role"] == "athlete"

    # Réutilisation du même token -> 409
    again = await client.post(f"/v1/invitations/{token}/accept", json={"age_confirmed": True})
    assert again.status_code == 409


@pytest.mark.asyncio
async def test_multi_tenant_isolation(client, mock_claims):
    org_a = await _create_org(client, mock_claims, uid="owner-a", email="a@club.fr")
    org_b = await _create_org(client, mock_claims, uid="owner-b", email="b@club.fr")

    # owner-b tente de lire les membres / groupes / invitations de org_a
    r1 = await client.get(f"/v1/orgs/{org_a}/members")
    r2 = await client.get(f"/v1/orgs/{org_a}/groups")
    r3 = await client.get(f"/v1/orgs/{org_a}/invitations")
    assert r1.status_code == 403
    assert r2.status_code == 403
    assert r3.status_code == 403

    # owner-a crée un groupe dans org_a
    mock_claims.update({"uid": "owner-a", "email": "a@club.fr"})
    grp = await client.post(
        f"/v1/orgs/{org_a}/groups",
        json={"name": "U19 Route", "sport": "cyclisme", "coach_uids": []},
    )
    assert grp.status_code == 201
    assert grp.json()["group"]["org_id"] == org_a
    assert org_b != org_a


@pytest.mark.asyncio
async def test_coach_invite_without_group_and_multi_org_athlete_in_app(client, mock_claims):
    """Un coach sans groupe peut inviter un athlète, et un athlète peut rejoindre plusieurs clubs/coachs."""
    import db as db_mod

    # 1. Club A avec un coach sans groupe + Solo Coach B
    org_a = await _create_org(client, mock_claims, uid="owner-club-a", email="owner@cluba.fr", acc_type="club")
    await db_mod.users_col.insert_one(
        {"_id": "coach-club-a", "email": "coach@cluba.fr", "first_name": "Coach", "last_name": "A"}
    )
    await db_mod.memberships_col.insert_one(
        {"org_id": org_a, "uid": "coach-club-a", "role": "coach", "status": "active", "group_ids": []}
    )

    org_b = await _create_org(client, mock_claims, uid="solo-b", email="solo@b.fr", acc_type="solo_coach")

    # 2. Coach A invite l'athlète sans groupe
    mock_claims.update({"uid": "coach-club-a", "email": "coach@cluba.fr"})
    inv_a = await client.post(
        f"/v1/orgs/{org_a}/invitations",
        json={"email": "multi@athlete.fr", "role": "athlete", "group_ids": []},
    )
    assert inv_a.status_code == 201
    inv_a_id = inv_a.json()["invitation"]["id"]

    # 3. Solo Coach B invite le même athlète
    mock_claims.update({"uid": "solo-b", "email": "solo@b.fr"})
    inv_b = await client.post(
        f"/v1/orgs/{org_b}/invitations",
        json={"email": "multi@athlete.fr", "role": "athlete", "group_ids": []},
    )
    assert inv_b.status_code == 201
    inv_b_id = inv_b.json()["invitation"]["id"]

    # 4. L'athlète connecté voit ses 2 invitations in-app et accepte les 2
    mock_claims.update({"uid": "ath-multi", "email": "multi@athlete.fr"})
    pending = await client.get("/v1/me/invitations")
    assert pending.status_code == 200
    assert len(pending.json()["invitations"]) == 2

    acc_a = await client.post(f"/v1/me/invitations/{inv_a_id}/accept")
    assert acc_a.status_code == 200
    assert acc_a.json()["org_id"] == org_a

    acc_b = await client.post(f"/v1/me/invitations/{inv_b_id}/accept")
    assert acc_b.status_code == 200
    assert acc_b.json()["org_id"] == org_b

    me = await client.get("/v1/me")
    assert len(me.json()["memberships"]) == 2

    # 5. Coach A voit bien l'athlète qu'il a invité même sans groupe
    mock_claims.update({"uid": "coach-club-a", "email": "coach@cluba.fr"})
    members_a = await client.get(f"/v1/orgs/{org_a}/members?role=athlete")
    assert members_a.status_code == 200
    assert [m["uid"] for m in members_a.json()["members"]] == ["ath-multi"]

    # 6. L'athlète quitte Club A mais reste rattaché à Solo Coach B
    mock_claims.update({"uid": "ath-multi", "email": "multi@athlete.fr"})
    leave = await client.delete(f"/v1/orgs/{org_a}/members/me")
    assert leave.status_code == 200
    me_after = await client.get("/v1/me")
    assert [m["org_id"] for m in me_after.json()["memberships"]] == [org_b]
