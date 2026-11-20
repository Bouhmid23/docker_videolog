"""Routes /v1/me : profil utilisateur, onboarding club/coach privé et profil sportif."""
from datetime import UTC, datetime
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request

from auth_firebase import Account, current_account, require_verified_email
import db as db_mod
from db import memberships_col, orgs_col, users_col
from models import (
    AthleteProfileIn,
    CoachProfileIn,
    HEALTH_CONSENT_VERSION,
    MeUpdateIn,
    OnboardingIn,
)

router = APIRouter(prefix="/v1/me", tags=["accounts"])


def _serialize_dt(value):
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def _serialize_user(doc: dict | None) -> dict | None:
    if not doc:
        return None
    out = dict(doc)
    out["uid"] = out.pop("_id", out.get("uid"))
    for k in ("created_at", "updated_at", "last_login_at"):
        if k in out:
            out[k] = _serialize_dt(out[k])
    hc = out.get("health_consent")
    if isinstance(hc, dict) and "granted_at" in hc:
        out["health_consent"] = {**hc, "granted_at": _serialize_dt(hc["granted_at"])}
    return out


@router.get("")
async def get_me(account: Account = Depends(current_account)):
    org_ids = [m.org_id for m in account.memberships]
    orgs_by_id: dict[str, dict] = {}
    coaches_by_org: dict[str, list[dict]] = {}
    if org_ids:
        cursor = orgs_col.find({"_id": {"$in": org_ids}})
        for o in await cursor.to_list(length=100):
            orgs_by_id[o["_id"]] = {
                "id": o["_id"],
                "type": o.get("type"),
                "name": o.get("name"),
                "sports": o.get("sports", []),
                "city": o.get("city"),
                "country": o.get("country", "FR"),
                "description": o.get("description"),
                "is_public": o.get("is_public", True) is not False,
                "accepting_join_requests": o.get("accepting_join_requests", True) is not False,
                "owner_uid": o.get("owner_uid"),
                "status": o.get("status", "active"),
                "settings": o.get("settings") or {},
            }

        staff_mems = await memberships_col.find(
            {
                "org_id": {"$in": org_ids},
                "role": {"$in": ["owner", "coach"]},
                "status": "active",
            }
        ).to_list(length=300)
        staff_uids = list({sm["uid"] for sm in staff_mems if sm.get("uid")})
        staff_users_by_id: dict[str, dict] = {}
        if staff_uids:
            for u in await users_col.find({"_id": {"$in": staff_uids}}).to_list(
                length=len(staff_uids) + 10
            ):
                staff_users_by_id[u["_id"]] = u

        for sm in staff_mems:
            oid = sm.get("org_id")
            uid = sm.get("uid")
            if not oid or not uid:
                continue
            u = staff_users_by_id.get(uid, {})
            org_type = (orgs_by_id.get(oid) or {}).get("type")
            has_cp = bool(u.get("coach_profile"))
            if sm.get("role") == "coach" or org_type == "solo_coach" or has_cp:
                fn = u.get("first_name") or ""
                ln = u.get("last_name") or ""
                name = f"{fn} {ln}".strip() or u.get("email") or uid
                coaches_by_org.setdefault(oid, []).append(
                    {
                        "uid": uid,
                        "name": name,
                        "role": sm.get("role"),
                        "coach_profile": u.get("coach_profile"),
                    }
                )

    memberships_out = []
    for m in account.memberships:
        org_info = orgs_by_id.get(m.org_id)
        memberships_out.append(
            {
                "org_id": m.org_id,
                "role": m.role,
                "group_ids": m.group_ids,
                "organization": org_info,
                "coaches": coaches_by_org.get(m.org_id, []),
            }
        )

    return {
        "uid": account.uid,
        "email": account.email,
        "email_verified": account.email_verified,
        "platform_admin": account.platform_admin,
        "needs_onboarding": not account.memberships and not account.platform_admin,
        "user": _serialize_user(account.user_doc),
        "memberships": memberships_out,
    }


@router.post("/onboarding", status_code=201)
async def onboard(
    body: OnboardingIn,
    account: Account = Depends(require_verified_email),
):
    if account.memberships:
        raise HTTPException(
            status_code=409,
            detail="Votre compte est déjà rattaché à une organisation.",
        )

    now = datetime.now(UTC)
    org_id = str(uuid.uuid4())

    user_doc = {
        "_id": account.uid,
        "email": account.email,
        "email_verified": True,
        "first_name": body.first_name,
        "last_name": body.last_name,
        "phone": (body.phone or "").strip() or None,
        "locale": "fr",
        "photo_url": None,
        "created_at": now,
        "updated_at": now,
        "last_login_at": now,
        "athlete_profile": None,
        "health_consent": None,
    }
    await users_col.update_one(
        {"_id": account.uid},
        {
            "$set": {
                "email": account.email,
                "email_verified": True,
                "first_name": body.first_name,
                "last_name": body.last_name,
                "phone": user_doc["phone"],
                "updated_at": now,
                "last_login_at": now,
            },
            "$setOnInsert": {
                "locale": "fr",
                "photo_url": None,
                "created_at": now,
                "athlete_profile": None,
                "health_consent": None,
            },
        },
        upsert=True,
    )

    org_doc = {
        "_id": org_id,
        "type": body.account_type,
        "name": body.organization_name,
        "sports": body.sports,
        "city": (body.city or "").strip() or None,
        "country": "FR",
        "description": None,
        "is_public": True,
        "accepting_join_requests": True,
        "logo_url": None,
        "owner_uid": account.uid,
        "created_at": now,
        "updated_at": now,
        "status": "active",
    }
    await orgs_col.insert_one(org_doc)

    membership_doc = {
        "_id": str(uuid.uuid4()),
        "org_id": org_id,
        "uid": account.uid,
        "role": "owner",
        "group_ids": [],
        "status": "active",
        "created_at": now,
        "invited_by": account.uid,
    }
    await memberships_col.insert_one(membership_doc)

    saved_user = await users_col.find_one({"_id": account.uid})
    return {
        "user": _serialize_user(saved_user),
        "organization": {
            "id": org_id,
            "type": org_doc["type"],
            "name": org_doc["name"],
            "sports": org_doc["sports"],
            "city": org_doc["city"],
            "country": org_doc["country"],
            "description": org_doc["description"],
            "is_public": org_doc["is_public"],
            "accepting_join_requests": org_doc["accepting_join_requests"],
            "owner_uid": org_doc["owner_uid"],
            "status": org_doc["status"],
        },
        "membership": {
            "org_id": org_id,
            "role": "owner",
            "group_ids": [],
        },
    }


@router.patch("")
async def update_me(
    body: MeUpdateIn,
    account: Account = Depends(require_verified_email),
):
    updates: dict = {"updated_at": datetime.now(UTC)}
    if body.first_name is not None:
        updates["first_name"] = body.first_name
    if body.last_name is not None:
        updates["last_name"] = body.last_name
    if body.phone is not None:
        updates["phone"] = body.phone.strip() or None
    if body.locale is not None:
        updates["locale"] = body.locale

    await users_col.update_one(
        {"_id": account.uid},
        {
            "$set": updates,
            "$setOnInsert": {
                "email": account.email,
                "email_verified": account.email_verified,
                "created_at": datetime.now(UTC),
                "athlete_profile": None,
                "health_consent": None,
            },
        },
        upsert=True,
    )
    saved = await users_col.find_one({"_id": account.uid})
    return {"user": _serialize_user(saved)}


@router.put("/athlete-profile")
async def update_athlete_profile(
    body: AthleteProfileIn,
    request: Request,
    account: Account = Depends(require_verified_email),
):
    now = datetime.now(UTC)
    profile = {
        "sport": body.sport,
        "level": body.level,
        "birth_date": body.birth_date.isoformat(),
        "height_cm": body.height_cm,
        "weight_kg": body.weight_kg if body.health_consent else None,
        "hr_max": body.hr_max if body.health_consent else None,
        "hr_rest": body.hr_rest if body.health_consent else None,
    }
    health_consent = (
        {
            "granted": True,
            "version": HEALTH_CONSENT_VERSION,
            "granted_at": now,
        }
        if body.health_consent
        else None
    )

    await users_col.update_one(
        {"_id": account.uid},
        {
            "$set": {
                "athlete_profile": profile,
                "health_consent": health_consent,
                "updated_at": now,
            },
            "$setOnInsert": {
                "email": account.email,
                "email_verified": account.email_verified,
                "created_at": now,
            },
        },
        upsert=True,
    )
    if not body.health_consent:
        await db_mod.wellness_entries_col.update_many(
            {"uid": account.uid},
            {"$set": {"hrv_ms": None, "resting_hr": None, "weight_kg": None}},
        )
    r = getattr(request.app.state, "redis", None)
    if r is not None:
        try:
            await r.delete(f"athlete:thresholds:{account.uid}")
        except Exception:
            pass
    saved = await users_col.find_one({"_id": account.uid})
    return {"user": _serialize_user(saved)}


@router.delete("/health-consent")
async def revoke_health_consent(
    request: Request,
    account: Account = Depends(require_verified_email),
):
    now = datetime.now(UTC)
    existing = await users_col.find_one({"_id": account.uid})
    update_fields: dict = {"health_consent": None, "updated_at": now}
    if existing and isinstance(existing.get("athlete_profile"), dict):
        update_fields["athlete_profile.weight_kg"] = None
        update_fields["athlete_profile.hr_max"] = None
        update_fields["athlete_profile.hr_rest"] = None
    await users_col.update_one({"_id": account.uid}, {"$set": update_fields})
    await db_mod.wellness_entries_col.update_many(
        {"uid": account.uid},
        {"$set": {"hrv_ms": None, "resting_hr": None, "weight_kg": None}},
    )
    r = getattr(request.app.state, "redis", None)
    if r is not None:
        try:
            await r.delete(f"athlete:thresholds:{account.uid}")
        except Exception:
            pass
    saved = await users_col.find_one({"_id": account.uid})
    return {"user": _serialize_user(saved)}


@router.get("/export")
async def export_my_personal_data(
    account: Account = Depends(current_account),
):
    """Export RGPD (Art. 20) des données personnelles et sportives de l'utilisateur."""
    thresholds = (
        await db_mod.threshold_history_col.find({"uid": account.uid}, {"_id": 0})
        .sort("effective_from", -1)
        .to_list(200)
    )
    wellness = (
        await db_mod.wellness_entries_col.find({"uid": account.uid}, {"_id": 0})
        .sort("date", -1)
        .to_list(365)
    )
    activities_raw = (
        await db_mod.activities_col.find(
            {"uid": account.uid},
            {
                "_id": 1,
                "org_id": 1,
                "sport": 1,
                "title": 1,
                "start_time": 1,
                "duration_s": 1,
                "distance_m": 1,
                "metrics.tss": 1,
                "rpe": 1,
            },
        )
        .sort("start_time", -1)
        .to_list(500)
    )
    activities = [
        {
            "id": str(a.get("_id")),
            "org_id": a.get("org_id"),
            "sport": a.get("sport"),
            "title": a.get("title"),
            "start_time": _serialize_dt(a.get("start_time")),
            "duration_s": a.get("duration_s"),
            "distance_m": a.get("distance_m"),
            "tss": (a.get("metrics") or {}).get("tss"),
            "rpe": a.get("rpe"),
        }
        for a in activities_raw
    ]
    return {
        "exported_at": datetime.now(UTC).isoformat(),
        "user": _serialize_user(account.user_doc),
        "memberships": [
            {"org_id": m.org_id, "role": m.role, "group_ids": m.group_ids}
            for m in account.memberships
        ],
        "thresholds": [
            {**t, "created_at": _serialize_dt(t.get("created_at")), "updated_at": _serialize_dt(t.get("updated_at"))}
            for t in thresholds
        ],
        "wellness_entries": [
            {**w, "created_at": _serialize_dt(w.get("created_at")), "updated_at": _serialize_dt(w.get("updated_at"))}
            for w in wellness
        ],
        "activities": activities,
    }


@router.put("/coach-profile")
async def update_coach_profile(
    body: CoachProfileIn,
    account: Account = Depends(require_verified_email),
):
    now = datetime.now(UTC)
    profile = {
        "sports": [s.model_dump() for s in body.sports],
        "certifications": body.certifications,
        "bio": body.bio,
    }
    await users_col.update_one(
        {"_id": account.uid},
        {
            "$set": {
                "coach_profile": profile,
                "updated_at": now,
            },
            "$setOnInsert": {
                "email": account.email,
                "email_verified": account.email_verified,
                "created_at": now,
            },
        },
        upsert=True,
    )
    saved = await users_col.find_one({"_id": account.uid})
    return {"user": _serialize_user(saved)}
