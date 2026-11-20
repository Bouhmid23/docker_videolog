"""Routes de demandes d'adhésion (mobile -> club/coach) et traitement back-office."""
from __future__ import annotations

from datetime import UTC, date, datetime
import os
from typing import Any, Literal
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from auth_firebase import (
    Account,
    OrgContext,
    current_account,
    require_org_role,
    require_verified_email,
)
import db as db_mod
from mailer import send_join_request_decision_email, send_join_request_received_email
from models import (
    JoinRequestApproveIn,
    JoinRequestCreateIn,
    JoinRequestRejectIn,
    MIN_AGE_YEARS,
    compute_age,
)
from services.sports_catalog import build_org_sports_details, get_sport_meta

router = APIRouter(tags=["join-requests"])

APP_PUBLIC_URL = os.environ.get("APP_PUBLIC_URL", "https://app.videolog.app").rstrip("/")
MAX_JOIN_REQUESTS_PER_HOUR = 10
MAX_PENDING_REQUESTS_PER_USER = 10


def _serialize_dt(val: Any) -> str | None:
    if isinstance(val, datetime):
        return val.isoformat()
    return val if isinstance(val, str) else None


async def _enforce_join_rate_limit(request: Request, uid: str) -> None:
    redis_client = getattr(request.app.state, "redis", None)
    if not redis_client:
        return
    key = f"rl:join_req:{uid}"
    try:
        count = await redis_client.incr(key)
        if count == 1:
            await redis_client.expire(key, 3600)
        if count > MAX_JOIN_REQUESTS_PER_HOUR:
            raise HTTPException(
                status_code=429,
                detail="Limite horaire de demandes d'adhésion atteinte. Réessayez plus tard.",
            )
    except HTTPException:
        raise
    except Exception:
        return


def _check_user_birth_date_age(user_doc: dict[str, Any] | None) -> None:
    ap = (user_doc or {}).get("athlete_profile")
    if not isinstance(ap, dict):
        return
    bd_raw = ap.get("birth_date")
    if not bd_raw:
        return
    try:
        bd = date.fromisoformat(str(bd_raw)[:10])
    except ValueError:
        return
    if compute_age(bd) < MIN_AGE_YEARS:
        raise HTTPException(
            status_code=422,
            detail="L'accès à la plateforme est interdit aux personnes de moins de 15 ans.",
        )


def _coach_can_handle_request(ctx: OrgContext, req_doc: dict[str, Any]) -> bool:
    if ctx.role in {"owner", "admin"}:
        return True
    if ctx.role != "coach":
        return False
    if req_doc.get("target_coach_uid") == ctx.account.uid:
        return True
    req_group = req_doc.get("requested_group_id")
    coach_groups = set(ctx.membership.group_ids if ctx.membership else [])
    if req_group and req_group in coach_groups:
        return True
    return False


def _serialize_join_request_base(doc: dict[str, Any]) -> dict[str, Any]:
    sports = list(doc.get("requested_sports") or [])
    return {
        "id": doc["_id"],
        "org_id": doc["org_id"],
        "uid": doc["uid"],
        "requested_sports": sports,
        "requested_sports_details": [get_sport_meta(s) for s in sports],
        "requested_group_id": doc.get("requested_group_id"),
        "target_coach_uid": doc.get("target_coach_uid"),
        "message": doc.get("message"),
        "status": doc["status"],
        "decision_note": doc.get("decision_note"),
        "decided_by": doc.get("decided_by"),
        "decided_at": _serialize_dt(doc.get("decided_at")),
        "assigned_group_ids": list(doc.get("assigned_group_ids") or []),
        "assigned_coach_uids": list(doc.get("assigned_coach_uids") or []),
        "created_at": _serialize_dt(doc.get("created_at")),
        "updated_at": _serialize_dt(doc.get("updated_at")),
    }


@router.post("/v1/orgs/{org_id}/join-requests", status_code=201)
async def create_join_request(
    org_id: str,
    body: JoinRequestCreateIn,
    request: Request,
    account: Account = Depends(require_verified_email),
):
    """Permet à un utilisateur mobile de demander l'adhésion à un club ou à un coach."""
    org = await db_mod.orgs_col.find_one({"_id": org_id, "status": "active"})
    if not org or org.get("is_public", True) is False:
        raise HTTPException(status_code=404, detail="Club ou coach introuvable.")
    if org.get("accepting_join_requests", True) is False:
        raise HTTPException(
            status_code=403,
            detail="Ce club ou ce coach n'accepte pas de demandes d'adhésion actuellement.",
        )

    _check_user_birth_date_age(account.user_doc)

    existing_membership = await db_mod.memberships_col.find_one(
        {"org_id": org_id, "uid": account.uid, "status": "active"}
    )
    if existing_membership:
        raise HTTPException(
            status_code=409,
            detail="Vous êtes déjà membre de cette organisation.",
        )

    existing_pending = await db_mod.join_requests_col.find_one(
        {"org_id": org_id, "uid": account.uid, "status": "pending"}
    )
    if existing_pending:
        raise HTTPException(
            status_code=409,
            detail="Vous avez déjà une demande d'adhésion en attente pour cette organisation.",
        )

    pending_user_total = await db_mod.join_requests_col.count_documents(
        {"uid": account.uid, "status": "pending"}
    )
    if pending_user_total >= MAX_PENDING_REQUESTS_PER_USER:
        raise HTTPException(
            status_code=409,
            detail="Vous avez atteint le nombre maximum de demandes d'adhésion simultanées en attente.",
        )

    group_doc = None
    if body.requested_group_id:
        group_doc = await db_mod.groups_col.find_one(
            {"_id": body.requested_group_id, "org_id": org_id}
        )
        if not group_doc:
            raise HTTPException(
                status_code=422,
                detail="Le groupe demandé n'existe pas dans cette organisation.",
            )

    if body.target_coach_uid:
        coach_mem = await db_mod.memberships_col.find_one(
            {
                "org_id": org_id,
                "uid": body.target_coach_uid,
                "status": "active",
                "role": {"$in": ["owner", "admin", "coach"]},
            }
        )
        if not coach_mem:
            raise HTTPException(
                status_code=422,
                detail="Le coach sélectionné n'appartient pas à cette organisation.",
            )

    await _enforce_join_rate_limit(request, account.uid)

    now = datetime.now(UTC)

    # S'assurer qu'un document utilisateur existe dans MongoDB pour que le club voie l'identité
    user_updates: dict[str, Any] = {
        "email": account.email,
        "email_verified": account.email_verified,
        "updated_at": now,
        "last_login_at": now,
    }
    if body.first_name:
        user_updates["first_name"] = body.first_name
    if body.last_name:
        user_updates["last_name"] = body.last_name

    await db_mod.users_col.update_one(
        {"_id": account.uid},
        {
            "$set": user_updates,
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

    requested_sports = list(body.requested_sports)
    if not requested_sports and group_doc and group_doc.get("sport"):
        requested_sports = [group_doc["sport"]]
    elif not requested_sports and account.user_doc:
        ap_sport = (account.user_doc.get("athlete_profile") or {}).get("sport")
        if ap_sport:
            requested_sports = [ap_sport]

    req_doc = {
        "_id": str(uuid.uuid4()),
        "org_id": org_id,
        "uid": account.uid,
        "requested_sports": requested_sports,
        "requested_group_id": body.requested_group_id,
        "target_coach_uid": body.target_coach_uid
        or (org.get("owner_uid") if org.get("type") == "solo_coach" else None),
        "message": body.message,
        "status": "pending",
        "decision_note": None,
        "decided_by": None,
        "decided_at": None,
        "assigned_group_ids": [],
        "assigned_coach_uids": [],
        "created_at": now,
        "updated_at": now,
    }
    await db_mod.join_requests_col.insert_one(req_doc)

    # Notifier owner/admins + éventuel coach ciblé + coachs du groupe demandé
    recipient_uids: list[str] = []
    staff_docs = await db_mod.memberships_col.find(
        {
            "org_id": org_id,
            "status": "active",
            "role": {"$in": ["owner", "admin"]},
        },
        {"uid": 1},
    ).to_list(length=50)
    recipient_uids.extend(s["uid"] for s in staff_docs if s.get("uid"))
    if req_doc["target_coach_uid"]:
        recipient_uids.append(req_doc["target_coach_uid"])
    if group_doc:
        recipient_uids.extend(group_doc.get("coach_uids") or [])
    unique_recipients = [u for u in dict.fromkeys(recipient_uids) if u and u != account.uid]

    applicant_name = (
        f"{body.first_name or (account.user_doc or {}).get('first_name') or ''} "
        f"{body.last_name or (account.user_doc or {}).get('last_name') or ''}"
    ).strip() or account.display_name
    sports_labels = [get_sport_meta(s)["label"] for s in requested_sports]

    if unique_recipients:
        notifs = [
            {
                "_id": uuid.uuid4().hex,
                "recipient_uid": r_uid,
                "org_id": org_id,
                "type": "join_request_created",
                "title": f"Demande d'adhésion : {applicant_name}",
                "body": (
                    f"{applicant_name} souhaite rejoindre {org.get('name', 'votre organisation')}"
                    + (f" ({', '.join(sports_labels[:3])})" if sports_labels else "")
                    + "."
                ),
                "link": "/app/invitations?tab=requests",
                "read": False,
                "created_at": now,
            }
            for r_uid in unique_recipients
        ]
        await db_mod.notifications_col.insert_many(notifs)

        review_url = f"{APP_PUBLIC_URL}/app/invitations?tab=requests"
        async for u in db_mod.users_col.find(
            {"_id": {"$in": unique_recipients}}, {"email": 1}
        ):
            if u.get("email"):
                await send_join_request_received_email(
                    to_email=u["email"],
                    org_name=org.get("name", "Videolog"),
                    applicant_name=applicant_name,
                    sports_labels=sports_labels,
                    message=body.message,
                    review_url=review_url,
                )

    return {
        "join_request": {
            **_serialize_join_request_base(req_doc),
            "org_name": org.get("name"),
            "org_type": org.get("type"),
        }
    }


@router.get("/v1/me/join-requests")
async def list_my_join_requests(
    status: Literal["pending", "approved", "rejected", "cancelled"] | None = Query(default=None),
    account: Account = Depends(current_account),
):
    """Liste les demandes d'adhésion envoyées par l'utilisateur connecté (app mobile)."""
    query: dict[str, Any] = {"uid": account.uid}
    if status:
        query["status"] = status

    docs = (
        await db_mod.join_requests_col.find(query)
        .sort("created_at", -1)
        .to_list(length=100)
    )
    if not docs:
        return {"join_requests": [], "count": 0}

    org_ids = list({d["org_id"] for d in docs})
    group_ids = list({d["requested_group_id"] for d in docs if d.get("requested_group_id")})
    coach_uids = list({d["target_coach_uid"] for d in docs if d.get("target_coach_uid")})

    orgs_map: dict[str, dict[str, Any]] = {}
    async for o in db_mod.orgs_col.find({"_id": {"$in": org_ids}}):
        orgs_map[o["_id"]] = o

    groups_map: dict[str, dict[str, Any]] = {}
    if group_ids:
        async for g in db_mod.groups_col.find({"_id": {"$in": group_ids}}):
            groups_map[g["_id"]] = g

    coaches_map: dict[str, str] = {}
    if coach_uids:
        async for u in db_mod.users_col.find(
            {"_id": {"$in": coach_uids}}, {"first_name": 1, "last_name": 1}
        ):
            name = f"{u.get('first_name') or ''} {u.get('last_name') or ''}".strip()
            coaches_map[u["_id"]] = name or "Coach"

    items = []
    for d in docs:
        org = orgs_map.get(d["org_id"], {})
        _, org_sports_details = build_org_sports_details(org.get("sports") or [])
        g = groups_map.get(d.get("requested_group_id") or "")
        items.append(
            {
                **_serialize_join_request_base(d),
                "org_name": org.get("name", "Organisation"),
                "org_type": org.get("type", "club"),
                "org_city": org.get("city"),
                "org_sports_details": org_sports_details,
                "requested_group_name": g.get("name") if g else None,
                "target_coach_name": coaches_map.get(d.get("target_coach_uid") or ""),
            }
        )
    return {"join_requests": items, "count": len(items)}


@router.delete("/v1/me/join-requests/{request_id}")
async def cancel_my_join_request(
    request_id: str,
    account: Account = Depends(current_account),
):
    """Permet à l'utilisateur mobile d'annuler sa demande tant qu'elle est en attente."""
    req_doc = await db_mod.join_requests_col.find_one(
        {"_id": request_id, "uid": account.uid}
    )
    if not req_doc:
        raise HTTPException(status_code=404, detail="Demande d'adhésion introuvable.")
    if req_doc["status"] != "pending":
        raise HTTPException(
            status_code=409,
            detail="Seule une demande en attente peut être annulée.",
        )

    now = datetime.now(UTC)
    await db_mod.join_requests_col.update_one(
        {"_id": request_id},
        {"$set": {"status": "cancelled", "updated_at": now}},
    )
    return {"status": "cancelled", "id": request_id}


@router.get("/v1/orgs/{org_id}/join-requests")
async def list_org_join_requests(
    org_id: str,
    status: Literal["pending", "approved", "rejected", "cancelled"] | None = Query(default=None),
    ctx: OrgContext = Depends(require_org_role("owner", "admin", "coach")),
):
    """Liste les demandes d'adhésion reçues par un club ou un coach."""
    query: dict[str, Any] = {"org_id": org_id}
    if status:
        query["status"] = status

    if ctx.role == "coach":
        coach_groups = list(ctx.membership.group_ids if ctx.membership else [])
        coach_or: list[dict[str, Any]] = [{"target_coach_uid": ctx.account.uid}]
        if coach_groups:
            coach_or.append({"requested_group_id": {"$in": coach_groups}})
        query["$or"] = coach_or

    docs = (
        await db_mod.join_requests_col.find(query)
        .sort("created_at", -1)
        .to_list(length=200)
    )
    # Placer les demandes "pending" en tête
    docs.sort(key=lambda d: (0 if d.get("status") == "pending" else 1))

    applicant_uids = list({d["uid"] for d in docs})
    coach_uids = list({d["target_coach_uid"] for d in docs if d.get("target_coach_uid")})
    group_ids = list({d["requested_group_id"] for d in docs if d.get("requested_group_id")})

    users_map: dict[str, dict[str, Any]] = {}
    all_uids = list(set(applicant_uids) | set(coach_uids))
    if all_uids:
        async for u in db_mod.users_col.find({"_id": {"$in": all_uids}}):
            users_map[u["_id"]] = u

    groups_map: dict[str, dict[str, Any]] = {}
    if group_ids:
        async for g in db_mod.groups_col.find({"_id": {"$in": group_ids}}):
            groups_map[g["_id"]] = g

    items = []
    pending_count = 0
    for d in docs:
        if d.get("status") == "pending":
            pending_count += 1
        u = users_map.get(d["uid"], {})
        ap = u.get("athlete_profile")
        safe_ap = None
        if isinstance(ap, dict):
            safe_ap = {
                "sport": ap.get("sport"),
                "level": ap.get("level"),
                "birth_date": ap.get("birth_date"),
                "height_cm": ap.get("height_cm"),
            }
        tc = users_map.get(d.get("target_coach_uid") or "", {})
        tc_name = (
            f"{tc.get('first_name') or ''} {tc.get('last_name') or ''}".strip()
            or tc.get("email")
            if tc
            else None
        )
        g = groups_map.get(d.get("requested_group_id") or "")
        items.append(
            {
                **_serialize_join_request_base(d),
                "applicant": {
                    "uid": d["uid"],
                    "email": u.get("email"),
                    "first_name": u.get("first_name"),
                    "last_name": u.get("last_name"),
                    "photo_url": u.get("photo_url"),
                    "athlete_profile": safe_ap,
                },
                "requested_group_name": g.get("name") if g else None,
                "target_coach_name": tc_name,
            }
        )

    return {
        "join_requests": items,
        "count": len(items),
        "pending_count": pending_count,
    }


@router.post("/v1/orgs/{org_id}/join-requests/{request_id}/approve")
async def approve_join_request(
    org_id: str,
    request_id: str,
    body: JoinRequestApproveIn | None = None,
    ctx: OrgContext = Depends(require_org_role("owner", "admin", "coach")),
):
    """Accepte une demande d'adhésion et crée/réactive automatiquement le membership."""
    payload = body or JoinRequestApproveIn()
    org = await db_mod.orgs_col.find_one({"_id": org_id})
    if not org:
        raise HTTPException(status_code=404, detail="Organisation introuvable.")

    req_doc = await db_mod.join_requests_col.find_one(
        {"_id": request_id, "org_id": org_id}
    )
    if not req_doc:
        raise HTTPException(status_code=404, detail="Demande d'adhésion introuvable.")
    if not _coach_can_handle_request(ctx, req_doc):
        raise HTTPException(status_code=403, detail="Accès refusé à cette demande.")
    if req_doc["status"] != "pending":
        raise HTTPException(
            status_code=409, detail="Cette demande d'adhésion a déjà été traitée."
        )

    role = payload.role
    if ctx.role == "coach" and role != "athlete":
        raise HTTPException(
            status_code=403, detail="Un coach ne peut accepter qu'en rôle athlète."
        )
    if role == "coach" and org.get("type") == "solo_coach":
        raise HTTPException(
            status_code=400,
            detail="Un coach privé ne peut pas attribuer un rôle de coach.",
        )

    # Résolution des groupes assignés
    if payload.group_ids is not None:
        assigned_groups = list(dict.fromkeys(payload.group_ids))
    elif req_doc.get("requested_group_id"):
        assigned_groups = [req_doc["requested_group_id"]]
    else:
        assigned_groups = []

    if assigned_groups:
        valid_count = await db_mod.groups_col.count_documents(
            {"org_id": org_id, "_id": {"$in": assigned_groups}}
        )
        if valid_count != len(assigned_groups):
            raise HTTPException(
                status_code=422, detail="Un ou plusieurs groupes sont invalides."
            )
        if ctx.role == "coach":
            coach_groups = set(ctx.membership.group_ids if ctx.membership else [])
            if not set(assigned_groups).issubset(coach_groups):
                raise HTTPException(
                    status_code=403,
                    detail="Un coach ne peut assigner que ses propres groupes.",
                )

    # Résolution des coachs référents
    if payload.coach_uids is not None:
        assigned_coaches = list(dict.fromkeys(payload.coach_uids))
    else:
        default_coaches: list[str] = []
        if org.get("type") == "solo_coach" and org.get("owner_uid"):
            default_coaches.append(org["owner_uid"])
        if req_doc.get("target_coach_uid"):
            default_coaches.append(req_doc["target_coach_uid"])
        if ctx.role == "coach":
            default_coaches.append(ctx.account.uid)
        assigned_coaches = list(dict.fromkeys(default_coaches))

    now = datetime.now(UTC)
    applicant_uid = req_doc["uid"]

    membership_update: dict[str, Any] = {
        "$set": {
            "role": role,
            "group_ids": assigned_groups,
            "status": "active",
            "invited_by": ctx.account.uid,
            "joined_via": "join_request",
            "updated_at": now,
        },
        "$setOnInsert": {
            "_id": str(uuid.uuid4()),
            "created_at": now,
        },
    }
    if assigned_coaches:
        membership_update["$addToSet"] = {"coach_uids": {"$each": assigned_coaches}}

    await db_mod.memberships_col.update_one(
        {"org_id": org_id, "uid": applicant_uid},
        membership_update,
        upsert=True,
    )

    # Révoquer d'éventuelles invitations e-mail redondantes
    applicant_user = await db_mod.users_col.find_one({"_id": applicant_uid})
    if applicant_user and applicant_user.get("email"):
        await db_mod.invitations_col.update_many(
            {
                "org_id": org_id,
                "email": applicant_user["email"].lower(),
                "status": "pending",
            },
            {"$set": {"status": "revoked"}},
        )

    await db_mod.join_requests_col.update_one(
        {"_id": request_id},
        {
            "$set": {
                "status": "approved",
                "decision_note": payload.decision_note,
                "decided_by": ctx.account.uid,
                "decided_at": now,
                "assigned_group_ids": assigned_groups,
                "assigned_coach_uids": assigned_coaches,
                "updated_at": now,
            }
        },
    )

    # Notification in-app + e-mail à l'athlète
    await db_mod.notifications_col.insert_one(
        {
            "_id": uuid.uuid4().hex,
            "recipient_uid": applicant_uid,
            "org_id": org_id,
            "type": "join_request_approved",
            "title": f"Adhésion acceptée : {org.get('name', 'Organisation')}",
            "body": payload.decision_note
            or f"Votre demande pour rejoindre {org.get('name', 'cette organisation')} a été acceptée.",
            "link": "/app",
            "read": False,
            "created_at": now,
        }
    )

    if applicant_user and applicant_user.get("email"):
        await send_join_request_decision_email(
            to_email=applicant_user["email"],
            org_name=org.get("name", "Videolog"),
            approved=True,
            decision_note=payload.decision_note,
            app_url=f"{APP_PUBLIC_URL}/app",
        )

    return {
        "status": "approved",
        "id": request_id,
        "org_id": org_id,
        "uid": applicant_uid,
        "role": role,
        "group_ids": assigned_groups,
        "coach_uids": assigned_coaches,
    }


@router.post("/v1/orgs/{org_id}/join-requests/{request_id}/reject")
async def reject_join_request(
    org_id: str,
    request_id: str,
    body: JoinRequestRejectIn | None = None,
    ctx: OrgContext = Depends(require_org_role("owner", "admin", "coach")),
):
    """Refuse une demande d'adhésion avec un motif optionnel."""
    payload = body or JoinRequestRejectIn()
    org = await db_mod.orgs_col.find_one({"_id": org_id})
    if not org:
        raise HTTPException(status_code=404, detail="Organisation introuvable.")

    req_doc = await db_mod.join_requests_col.find_one(
        {"_id": request_id, "org_id": org_id}
    )
    if not req_doc:
        raise HTTPException(status_code=404, detail="Demande d'adhésion introuvable.")
    if not _coach_can_handle_request(ctx, req_doc):
        raise HTTPException(status_code=403, detail="Accès refusé à cette demande.")
    if req_doc["status"] != "pending":
        raise HTTPException(
            status_code=409, detail="Cette demande d'adhésion a déjà été traitée."
        )

    now = datetime.now(UTC)
    await db_mod.join_requests_col.update_one(
        {"_id": request_id},
        {
            "$set": {
                "status": "rejected",
                "decision_note": payload.decision_note,
                "decided_by": ctx.account.uid,
                "decided_at": now,
                "updated_at": now,
            }
        },
    )

    applicant_uid = req_doc["uid"]
    await db_mod.notifications_col.insert_one(
        {
            "_id": uuid.uuid4().hex,
            "recipient_uid": applicant_uid,
            "org_id": org_id,
            "type": "join_request_rejected",
            "title": f"Demande refusée : {org.get('name', 'Organisation')}",
            "body": payload.decision_note
            or f"Votre demande pour rejoindre {org.get('name', 'cette organisation')} n'a pas été retenue.",
            "link": None,
            "read": False,
            "created_at": now,
        }
    )

    applicant_user = await db_mod.users_col.find_one({"_id": applicant_uid})
    if applicant_user and applicant_user.get("email"):
        await send_join_request_decision_email(
            to_email=applicant_user["email"],
            org_name=org.get("name", "Videolog"),
            approved=False,
            decision_note=payload.decision_note,
            app_url=f"{APP_PUBLIC_URL}/app",
        )

    return {
        "status": "rejected",
        "id": request_id,
        "org_id": org_id,
        "uid": applicant_uid,
    }
