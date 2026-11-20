"""Routes /v1/orgs/{org_id} : organisation, membres, groupes et profils athlètes."""
from datetime import UTC, datetime
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query

from auth_firebase import OrgContext, require_org_role
from db import groups_col, memberships_col, orgs_col, users_col
from models import GroupIn, MemberUpdateIn, OrgUpdateIn
from routers.accounts import _serialize_user

router = APIRouter(prefix="/v1/orgs/{org_id}", tags=["organizations"])


def _serialize_org(doc: dict) -> dict:
    return {
        "id": doc["_id"],
        "type": doc.get("type"),
        "name": doc.get("name"),
        "sports": doc.get("sports", []),
        "city": doc.get("city"),
        "country": doc.get("country", "FR"),
        "description": doc.get("description"),
        "is_public": doc.get("is_public", True) is not False,
        "accepting_join_requests": doc.get("accepting_join_requests", True) is not False,
        "logo_url": doc.get("logo_url"),
        "owner_uid": doc.get("owner_uid"),
        "status": doc.get("status", "active"),
        "settings": doc.get("settings") or {},
        "created_at": doc["created_at"].isoformat()
        if isinstance(doc.get("created_at"), datetime)
        else doc.get("created_at"),
    }


def _serialize_group(doc: dict) -> dict:
    return {
        "id": doc["_id"],
        "org_id": doc["org_id"],
        "name": doc["name"],
        "sport": doc.get("sport", "autre"),
        "coach_uids": doc.get("coach_uids", []),
        "created_at": doc["created_at"].isoformat()
        if isinstance(doc.get("created_at"), datetime)
        else doc.get("created_at"),
    }


async def _get_org_or_404(org_id: str) -> dict:
    org = await orgs_col.find_one({"_id": org_id})
    if not org:
        raise HTTPException(status_code=404, detail="Organisation introuvable.")
    return org


@router.get("")
async def get_organization(
    org_id: str,
    ctx: OrgContext = Depends(require_org_role("owner", "admin", "coach", "athlete")),
):
    org = await _get_org_or_404(org_id)
    return {"organization": _serialize_org(org), "my_role": ctx.role}


@router.patch("")
async def update_organization(
    org_id: str,
    body: OrgUpdateIn,
    ctx: OrgContext = Depends(require_org_role("owner", "admin")),
):
    await _get_org_or_404(org_id)
    updates: dict = {"updated_at": datetime.now(UTC)}
    if body.name is not None:
        cleaned = body.name.strip()
        if not cleaned:
            raise HTTPException(status_code=422, detail="Le nom ne peut pas être vide.")
        updates["name"] = cleaned
    if body.sports is not None:
        updates["sports"] = body.sports
    if body.city is not None:
        updates["city"] = body.city.strip() or None
    if "description" in body.model_fields_set:
        updates["description"] = body.description
    if body.is_public is not None:
        updates["is_public"] = body.is_public
    if body.accepting_join_requests is not None:
        updates["accepting_join_requests"] = body.accepting_join_requests
    if body.settings is not None and body.settings.max_recording_s is not None:
        updates["settings.max_recording_s"] = body.settings.max_recording_s

    await orgs_col.update_one({"_id": org_id}, {"$set": updates})
    org = await _get_org_or_404(org_id)
    return {"organization": _serialize_org(org)}


@router.get("/members")
async def list_members(
    org_id: str,
    role: str | None = Query(default=None),
    group_id: str | None = Query(default=None),
    ctx: OrgContext = Depends(require_org_role("owner", "admin", "coach")),
):
    await _get_org_or_404(org_id)
    query: dict = {"org_id": org_id, "status": "active"}
    if role:
        query["role"] = role
    if group_id:
        query["group_ids"] = group_id

    if ctx.role == "coach" and ctx.membership:
        coach_groups = set(ctx.membership.group_ids)
        if group_id and group_id not in coach_groups:
            raise HTTPException(status_code=403, detail="Accès refusé à ce groupe.")
        if not group_id:
            coach_scope: list[dict] = [
                {"invited_by": ctx.account.uid},
                {"coach_uids": ctx.account.uid},
            ]
            if coach_groups:
                coach_scope.append({"group_ids": {"$in": list(coach_groups)}})
            query["$or"] = coach_scope

    memberships = await memberships_col.find(query).to_list(length=500)
    uids = [m["uid"] for m in memberships]
    users_by_uid: dict[str, dict] = {}
    if uids:
        async for u in users_col.find({"_id": {"$in": uids}}):
            users_by_uid[u["_id"]] = u

    items = []
    for m in memberships:
        u = users_by_uid.get(m["uid"], {})
        cp = u.get("coach_profile")
        items.append(
            {
                "uid": m["uid"],
                "role": m["role"],
                "group_ids": m.get("group_ids", []),
                "coach_uids": m.get("coach_uids", []),
                "invited_by": m.get("invited_by"),
                "email": u.get("email"),
                "first_name": u.get("first_name"),
                "last_name": u.get("last_name"),
                "has_athlete_profile": bool(u.get("athlete_profile")),
                "sport": (u.get("athlete_profile") or {}).get("sport"),
                "level": (u.get("athlete_profile") or {}).get("level"),
                "has_coach_profile": bool(
                    cp and (cp.get("sports") or cp.get("certifications") or cp.get("bio"))
                ),
                "coach_profile": cp,
                "created_at": m["created_at"].isoformat()
                if isinstance(m.get("created_at"), datetime)
                else m.get("created_at"),
            }
        )
    return {"members": items, "count": len(items)}


@router.patch("/members/{uid}")
async def update_member(
    org_id: str,
    uid: str,
    body: MemberUpdateIn,
    ctx: OrgContext = Depends(require_org_role("owner", "admin")),
):
    org = await _get_org_or_404(org_id)
    target = await memberships_col.find_one(
        {"org_id": org_id, "uid": uid, "status": "active"}
    )
    if not target:
        raise HTTPException(status_code=404, detail="Membre introuvable.")

    if target["role"] == "owner" and body.role is not None:
        raise HTTPException(
            status_code=400, detail="Le rôle du propriétaire ne peut pas être modifié."
        )
    if body.role in {"admin", "coach"} and org.get("type") == "solo_coach":
        raise HTTPException(
            status_code=400,
            detail="Un coach privé ne peut pas attribuer un rôle de coach ou d'administrateur.",
        )

    updates: dict = {}
    if body.role is not None:
        updates["role"] = body.role
    if body.group_ids is not None:
        if body.group_ids:
            valid_count = await groups_col.count_documents(
                {"org_id": org_id, "_id": {"$in": body.group_ids}}
            )
            if valid_count != len(set(body.group_ids)):
                raise HTTPException(
                    status_code=422, detail="Un ou plusieurs groupes sont invalides."
                )
        updates["group_ids"] = list(dict.fromkeys(body.group_ids))

    if updates:
        await memberships_col.update_one({"_id": target["_id"]}, {"$set": updates})
    updated = await memberships_col.find_one({"_id": target["_id"]})
    return {
        "member": {
            "uid": updated["uid"],
            "role": updated["role"],
            "group_ids": updated.get("group_ids", []),
        }
    }


@router.delete("/members/me")
async def leave_organization(
    org_id: str,
    ctx: OrgContext = Depends(require_org_role("owner", "admin", "coach", "athlete")),
):
    """Permet à un membre (notamment un athlète) de quitter un club ou un coach."""
    await _get_org_or_404(org_id)
    if ctx.role == "owner":
        raise HTTPException(
            status_code=400,
            detail="Le propriétaire de l'organisation ne peut pas se retirer lui-même.",
        )
    await memberships_col.update_one(
        {"org_id": org_id, "uid": ctx.account.uid, "status": "active"},
        {"$set": {"status": "removed", "removed_at": datetime.now(UTC)}},
    )
    return {"status": "removed", "org_id": org_id, "uid": ctx.account.uid}


@router.delete("/members/{uid}")
async def remove_member(
    org_id: str,
    uid: str,
    ctx: OrgContext = Depends(require_org_role("owner", "admin")),
):
    await _get_org_or_404(org_id)
    target = await memberships_col.find_one(
        {"org_id": org_id, "uid": uid, "status": "active"}
    )
    if not target:
        raise HTTPException(status_code=404, detail="Membre introuvable.")
    if target["role"] == "owner":
        raise HTTPException(
            status_code=400,
            detail="Le propriétaire de l'organisation ne peut pas être retiré.",
        )

    await memberships_col.update_one(
        {"_id": target["_id"]},
        {"$set": {"status": "removed", "removed_at": datetime.now(UTC)}},
    )
    return {"status": "removed", "uid": uid}


@router.get("/athletes/{uid}")
async def get_athlete_detail(
    org_id: str,
    uid: str,
    ctx: OrgContext = Depends(require_org_role("owner", "admin", "coach", "athlete")),
):
    await _get_org_or_404(org_id)
    if ctx.role == "athlete" and uid != ctx.account.uid:
        raise HTTPException(status_code=403, detail="Accès refusé.")

    membership = await memberships_col.find_one(
        {"org_id": org_id, "uid": uid, "status": "active"}
    )
    if not membership:
        raise HTTPException(status_code=404, detail="Athlète introuvable dans cette organisation.")

    if ctx.role == "coach" and ctx.membership:
        shared = set(ctx.membership.group_ids) & set(membership.get("group_ids") or [])
        if not shared and uid != ctx.account.uid:
            raise HTTPException(
                status_code=403,
                detail="Cet athlète ne fait partie d'aucun de vos groupes.",
            )

    user_doc = await users_col.find_one({"_id": uid})
    return {
        "uid": uid,
        "role": membership["role"],
        "group_ids": membership.get("group_ids", []),
        "user": _serialize_user(user_doc),
    }


@router.get("/groups")
async def list_groups(
    org_id: str,
    ctx: OrgContext = Depends(require_org_role("owner", "admin", "coach", "athlete")),
):
    await _get_org_or_404(org_id)
    query: dict = {"org_id": org_id}
    if ctx.role in {"coach", "athlete"} and ctx.membership:
        query["_id"] = {"$in": ctx.membership.group_ids or ["__none__"]}

    docs = await groups_col.find(query).sort("name", 1).to_list(length=200)
    return {"groups": [_serialize_group(g) for g in docs]}


@router.post("/groups", status_code=201)
async def create_group(
    org_id: str,
    body: GroupIn,
    ctx: OrgContext = Depends(require_org_role("owner", "admin")),
):
    await _get_org_or_404(org_id)
    group_id = str(uuid.uuid4())
    now = datetime.now(UTC)
    doc = {
        "_id": group_id,
        "org_id": org_id,
        "name": body.name,
        "sport": body.sport,
        "coach_uids": list(dict.fromkeys(body.coach_uids)),
        "created_at": now,
    }
    await groups_col.insert_one(doc)
    if doc["coach_uids"]:
        await memberships_col.update_many(
            {"org_id": org_id, "uid": {"$in": doc["coach_uids"]}, "status": "active"},
            {"$addToSet": {"group_ids": group_id}},
        )
    return {"group": _serialize_group(doc)}


@router.patch("/groups/{group_id}")
async def update_group(
    org_id: str,
    group_id: str,
    body: GroupIn,
    ctx: OrgContext = Depends(require_org_role("owner", "admin")),
):
    existing = await groups_col.find_one({"_id": group_id, "org_id": org_id})
    if not existing:
        raise HTTPException(status_code=404, detail="Groupe introuvable.")
    coach_uids = list(dict.fromkeys(body.coach_uids))
    await groups_col.update_one(
        {"_id": group_id},
        {"$set": {"name": body.name, "sport": body.sport, "coach_uids": coach_uids}},
    )
    if coach_uids:
        await memberships_col.update_many(
            {"org_id": org_id, "uid": {"$in": coach_uids}, "status": "active"},
            {"$addToSet": {"group_ids": group_id}},
        )
    updated = await groups_col.find_one({"_id": group_id})
    return {"group": _serialize_group(updated)}


@router.delete("/groups/{group_id}")
async def delete_group(
    org_id: str,
    group_id: str,
    ctx: OrgContext = Depends(require_org_role("owner", "admin")),
):
    res = await groups_col.delete_one({"_id": group_id, "org_id": org_id})
    if res.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Groupe introuvable.")
    await memberships_col.update_many(
        {"org_id": org_id},
        {"$pull": {"group_ids": group_id}},
    )
    return {"status": "deleted", "id": group_id}
