"""Routes d'invitations par e-mail (création, liste, renvoi, révocation, aperçu public et acceptation)."""
from datetime import UTC, datetime, timedelta
import hashlib
import os
import re
import secrets
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request

from auth_firebase import (
    Account,
    OrgContext,
    current_account,
    mark_firebase_email_verified,
    require_org_role,
)
from db import groups_col, invitations_col, memberships_col, orgs_col, users_col
from mailer import send_invitation_email, smtp_configured
from models import AcceptInvitationIn, InvitationIn

router = APIRouter(tags=["invitations"])

APP_PUBLIC_URL = os.environ.get("APP_PUBLIC_URL", "https://app.videolog.app").rstrip("/")
MAX_PENDING_PER_ORG = 50
MAX_INVITES_PER_HOUR = 20


def _hash_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def _mask_email(email: str) -> str:
    if "@" not in email:
        return "***"
    local, domain = email.split("@", 1)
    prefix = local[:1] if local else "*"
    return f"{prefix}***@{domain}"


def _serialize_invitation(doc: dict) -> dict:
    return {
        "id": doc["_id"],
        "org_id": doc["org_id"],
        "email": doc["email"],
        "role": doc["role"],
        "group_ids": doc.get("group_ids", []),
        "status": doc["status"],
        "expires_at": doc["expires_at"].isoformat()
        if isinstance(doc.get("expires_at"), datetime)
        else doc.get("expires_at"),
        "created_at": doc["created_at"].isoformat()
        if isinstance(doc.get("created_at"), datetime)
        else doc.get("created_at"),
    }


async def _enforce_rate_limit(request: Request, uid: str) -> None:
    redis_client = getattr(request.app.state, "redis", None)
    if not redis_client:
        return
    key = f"rl:invite:{uid}"
    try:
        count = await redis_client.incr(key)
        if count == 1:
            await redis_client.expire(key, 3600)
        if count > MAX_INVITES_PER_HOUR:
            raise HTTPException(
                status_code=429,
                detail="Limite horaire d'invitations atteinte. Réessayez plus tard.",
            )
    except HTTPException:
        raise
    except Exception:
        # Ne bloque pas l'envoi si Redis est momentanément indisponible.
        return


@router.post("/v1/orgs/{org_id}/invitations", status_code=201)
async def create_invitation(
    org_id: str,
    body: InvitationIn,
    request: Request,
    ctx: OrgContext = Depends(require_org_role("owner", "admin", "coach")),
):
    org = await orgs_col.find_one({"_id": org_id})
    if not org:
        raise HTTPException(status_code=404, detail="Organisation introuvable.")

    if ctx.role == "coach":
        if body.role != "athlete":
            raise HTTPException(
                status_code=403, detail="Un coach ne peut inviter que des athlètes."
            )
        if body.group_ids:
            coach_groups = set(ctx.membership.group_ids if ctx.membership else [])
            if not set(body.group_ids).issubset(coach_groups):
                raise HTTPException(
                    status_code=403,
                    detail="Un coach ne peut inviter un athlète que dans ses propres groupes.",
                )

    if body.role == "coach" and org.get("type") == "solo_coach":
        raise HTTPException(
            status_code=400,
            detail="Un coach privé ne peut pas inviter d'autres coachs.",
        )

    if body.group_ids:
        valid_count = await groups_col.count_documents(
            {"org_id": org_id, "_id": {"$in": body.group_ids}}
        )
        if valid_count != len(set(body.group_ids)):
            raise HTTPException(
                status_code=422, detail="Un ou plusieurs groupes sont invalides."
            )

    existing_user = await users_col.find_one({"email": body.email})
    if existing_user:
        existing_membership = await memberships_col.find_one(
            {"org_id": org_id, "uid": existing_user["_id"], "status": "active"}
        )
        if existing_membership:
            raise HTTPException(
                status_code=409,
                detail="Cet utilisateur est déjà membre de l'organisation.",
            )

    pending_count = await invitations_col.count_documents(
        {"org_id": org_id, "status": "pending"}
    )
    if pending_count >= MAX_PENDING_PER_ORG:
        raise HTTPException(
            status_code=409,
            detail="Nombre maximum d'invitations en attente atteint pour cette organisation.",
        )

    await _enforce_rate_limit(request, ctx.account.uid)

    # Révoquer une éventuelle invitation en attente pour le même e-mail.
    await invitations_col.update_many(
        {"org_id": org_id, "email": body.email, "status": "pending"},
        {"$set": {"status": "revoked"}},
    )

    raw_token = secrets.token_urlsafe(32)
    now = datetime.now(UTC)
    coach_uids = (
        [ctx.account.uid]
        if (ctx.role == "coach" or org.get("type") == "solo_coach") and body.role == "athlete"
        else []
    )
    inv_doc = {
        "_id": str(uuid.uuid4()),
        "org_id": org_id,
        "email": body.email,
        "role": body.role,
        "group_ids": list(dict.fromkeys(body.group_ids)),
        "coach_uids": coach_uids,
        "token_hash": _hash_token(raw_token),
        "invited_by": ctx.account.uid,
        "status": "pending",
        "expires_at": now + timedelta(days=7),
        "accepted_by": None,
        "accepted_at": None,
        "created_at": now,
    }
    await invitations_col.insert_one(inv_doc)

    invite_url = f"{APP_PUBLIC_URL}/invite/{raw_token}"
    email_sent = await send_invitation_email(
        to_email=body.email,
        org_name=org.get("name", "Videolog"),
        role=body.role,
        invite_url=invite_url,
        inviter_name=ctx.account.display_name,
    )

    return {
        "invitation": _serialize_invitation(inv_doc),
        "email_sent": email_sent,
        "invite_url": None if (email_sent and smtp_configured()) else invite_url,
    }


@router.get("/v1/orgs/{org_id}/invitations")
async def list_invitations(
    org_id: str,
    ctx: OrgContext = Depends(require_org_role("owner", "admin", "coach")),
):
    query: dict = {"org_id": org_id}
    if ctx.role == "coach":
        query["invited_by"] = ctx.account.uid

    docs = await invitations_col.find(query).sort("created_at", -1).to_list(length=200)
    now = datetime.now(UTC)
    result = []
    for d in docs:
        exp = d.get("expires_at")
        if d["status"] == "pending" and isinstance(exp, datetime):
            exp_utc = exp if exp.tzinfo else exp.replace(tzinfo=UTC)
            if exp_utc < now:
                d["status"] = "expired"
        result.append(_serialize_invitation(d))
    return {"invitations": result}


@router.post("/v1/orgs/{org_id}/invitations/{inv_id}/resend")
async def resend_invitation(
    org_id: str,
    inv_id: str,
    request: Request,
    ctx: OrgContext = Depends(require_org_role("owner", "admin", "coach")),
):
    inv = await invitations_col.find_one({"_id": inv_id, "org_id": org_id})
    if not inv:
        raise HTTPException(status_code=404, detail="Invitation introuvable.")
    if ctx.role == "coach" and inv.get("invited_by") != ctx.account.uid:
        raise HTTPException(status_code=403, detail="Accès refusé.")
    if inv["status"] not in {"pending", "expired"}:
        raise HTTPException(
            status_code=409,
            detail="Cette invitation ne peut plus être renvoyée.",
        )

    await _enforce_rate_limit(request, ctx.account.uid)

    org = await orgs_col.find_one({"_id": org_id})
    raw_token = secrets.token_urlsafe(32)
    now = datetime.now(UTC)
    new_exp = now + timedelta(days=7)
    await invitations_col.update_one(
        {"_id": inv_id},
        {
            "$set": {
                "token_hash": _hash_token(raw_token),
                "status": "pending",
                "expires_at": new_exp,
            }
        },
    )
    inv["status"] = "pending"
    inv["expires_at"] = new_exp

    invite_url = f"{APP_PUBLIC_URL}/invite/{raw_token}"
    email_sent = await send_invitation_email(
        to_email=inv["email"],
        org_name=(org or {}).get("name", "Videolog"),
        role=inv["role"],
        invite_url=invite_url,
        inviter_name=ctx.account.display_name,
    )
    return {
        "invitation": _serialize_invitation(inv),
        "email_sent": email_sent,
        "invite_url": None if (email_sent and smtp_configured()) else invite_url,
    }


@router.delete("/v1/orgs/{org_id}/invitations/{inv_id}")
async def revoke_invitation(
    org_id: str,
    inv_id: str,
    ctx: OrgContext = Depends(require_org_role("owner", "admin", "coach")),
):
    inv = await invitations_col.find_one({"_id": inv_id, "org_id": org_id})
    if not inv:
        raise HTTPException(status_code=404, detail="Invitation introuvable.")
    if ctx.role == "coach" and inv.get("invited_by") != ctx.account.uid:
        raise HTTPException(status_code=403, detail="Accès refusé.")
    if inv["status"] != "pending":
        raise HTTPException(status_code=409, detail="Invitation déjà clôturée.")

    await invitations_col.update_one(
        {"_id": inv_id},
        {"$set": {"status": "revoked"}},
    )
    return {"status": "revoked", "id": inv_id}


def _ensure_invitation_usable(inv: dict | None) -> dict:
    if not inv:
        raise HTTPException(status_code=404, detail="Invitation introuvable.")
    if inv["status"] == "accepted":
        raise HTTPException(status_code=409, detail="Cette invitation a déjà été acceptée.")
    if inv["status"] == "revoked":
        raise HTTPException(status_code=410, detail="Cette invitation a été révoquée.")
    exp = inv.get("expires_at")
    if isinstance(exp, datetime):
        exp_utc = exp if exp.tzinfo else exp.replace(tzinfo=UTC)
        if exp_utc < datetime.now(UTC):
            raise HTTPException(status_code=410, detail="Cette invitation a expiré.")
    if inv["status"] != "pending":
        raise HTTPException(status_code=410, detail="Invitation invalide.")
    return inv


@router.get("/v1/invitations/{token}")
async def preview_invitation(token: str):
    inv = await invitations_col.find_one({"token_hash": _hash_token(token)})
    inv = _ensure_invitation_usable(inv)
    org = await orgs_col.find_one({"_id": inv["org_id"]})
    return {
        "org_name": (org or {}).get("name", "Organisation"),
        "org_type": (org or {}).get("type", "club"),
        "role": inv["role"],
        "masked_email": _mask_email(inv["email"]),
        "expires_at": inv["expires_at"].isoformat()
        if isinstance(inv.get("expires_at"), datetime)
        else inv.get("expires_at"),
    }


async def _accept_invitation_doc(
    inv: dict,
    account: Account,
    *,
    first_name: str | None = None,
    last_name: str | None = None,
) -> dict:
    """Valide l'e-mail et rattache l'utilisateur à l'organisation de l'invitation."""
    if not account.email or account.email.lower() != inv["email"].lower():
        raise HTTPException(
            status_code=403,
            detail="Cette invitation est destinée à une autre adresse e-mail.",
        )

    now = datetime.now(UTC)
    if not account.email_verified:
        try:
            await mark_firebase_email_verified(account.uid)
        except Exception:
            # En environnement sans Firebase Admin configuré (tests), on continue.
            pass

    user_updates: dict = {
        "email": account.email,
        "email_verified": True,
        "updated_at": now,
        "last_login_at": now,
    }
    if first_name:
        user_updates["first_name"] = first_name.strip()
    if last_name:
        user_updates["last_name"] = last_name.strip()

    await users_col.update_one(
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

    membership_update: dict = {
        "$set": {
            "role": inv["role"],
            "group_ids": list(inv.get("group_ids") or []),
            "status": "active",
            "invited_by": inv.get("invited_by"),
        },
        "$setOnInsert": {
            "_id": str(uuid.uuid4()),
            "created_at": now,
        },
    }
    coach_uids = list(inv.get("coach_uids") or [])
    if coach_uids:
        membership_update["$addToSet"] = {"coach_uids": {"$each": coach_uids}}

    await memberships_col.update_one(
        {"org_id": inv["org_id"], "uid": account.uid},
        membership_update,
        upsert=True,
    )

    await invitations_col.update_one(
        {"_id": inv["_id"]},
        {
            "$set": {
                "status": "accepted",
                "accepted_by": account.uid,
                "accepted_at": now,
            }
        },
    )

    return {
        "status": "accepted",
        "org_id": inv["org_id"],
        "role": inv["role"],
    }


@router.post("/v1/invitations/{token}/accept")
async def accept_invitation(
    token: str,
    body: AcceptInvitationIn,
    account: Account = Depends(current_account),
):
    inv = await invitations_col.find_one({"token_hash": _hash_token(token)})
    inv = _ensure_invitation_usable(inv)
    return await _accept_invitation_doc(
        inv,
        account,
        first_name=body.first_name,
        last_name=body.last_name,
    )


@router.get("/v1/me/invitations")
async def list_my_pending_invitations(
    account: Account = Depends(current_account),
):
    """Liste les invitations en attente destinées à l'utilisateur connecté."""
    if not account.email:
        return {"invitations": []}
    now = datetime.now(UTC)
    cursor = invitations_col.find(
        {
            "email": {"$regex": f"^{re.escape(account.email)}$", "$options": "i"},
            "status": "pending",
        }
    ).sort("created_at", -1)
    pending = await cursor.to_list(length=50)

    valid_invs: list[dict] = []
    org_ids: set[str] = set()
    inviter_uids: set[str] = set()
    for inv in pending:
        exp = inv.get("expires_at")
        if isinstance(exp, datetime):
            exp_utc = exp if exp.tzinfo else exp.replace(tzinfo=UTC)
            if exp_utc < now:
                continue
        valid_invs.append(inv)
        org_ids.add(inv["org_id"])
        if inv.get("invited_by"):
            inviter_uids.add(inv["invited_by"])

    orgs_map: dict[str, dict] = {}
    if org_ids:
        async for o in orgs_col.find({"_id": {"$in": list(org_ids)}}):
            orgs_map[o["_id"]] = o

    inviters_map: dict[str, str] = {}
    if inviter_uids:
        async for u in users_col.find({"_id": {"$in": list(inviter_uids)}}):
            name = f"{u.get('first_name') or ''} {u.get('last_name') or ''}".strip()
            inviters_map[u["_id"]] = name or u.get("email") or u["_id"]

    items = []
    for inv in valid_invs:
        org = orgs_map.get(inv["org_id"], {})
        items.append(
            {
                **_serialize_invitation(inv),
                "org_name": org.get("name", "Organisation"),
                "org_type": org.get("type", "club"),
                "inviter_name": inviters_map.get(inv.get("invited_by", ""), None),
            }
        )
    return {"invitations": items}


@router.post("/v1/me/invitations/{inv_id}/accept")
async def accept_my_invitation(
    inv_id: str,
    account: Account = Depends(current_account),
):
    inv = await invitations_col.find_one({"_id": inv_id})
    inv = _ensure_invitation_usable(inv)
    return await _accept_invitation_doc(inv, account)


@router.post("/v1/me/invitations/{inv_id}/decline")
async def decline_my_invitation(
    inv_id: str,
    account: Account = Depends(current_account),
):
    inv = await invitations_col.find_one({"_id": inv_id})
    inv = _ensure_invitation_usable(inv)
    if not account.email or account.email.lower() != inv["email"].lower():
        raise HTTPException(
            status_code=403,
            detail="Cette invitation est destinée à une autre adresse e-mail.",
        )
    await invitations_col.update_one(
        {"_id": inv_id},
        {
            "$set": {
                "status": "declined",
                "declined_by": account.uid,
                "declined_at": datetime.now(UTC),
            }
        },
    )
    return {"status": "declined", "id": inv_id}
