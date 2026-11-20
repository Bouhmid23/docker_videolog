"""Routes /v1/orgs/{org_id}/plans et /goals : plans multi-semaines et objectifs A/B/C."""
from datetime import UTC, datetime, timedelta
from typing import Any
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query

from auth_firebase import OrgContext, require_org_role
import db as db_mod
from models import ApplyPlanIn, GoalIn, TrainingPlanIn
from services.pmc import recompute_pmc
from services.workout_estimator import estimate_workout

router = APIRouter(prefix="/v1/orgs/{org_id}", tags=["training-plans"])


def _serialize_plan(doc: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(doc["_id"]),
        "org_id": doc.get("org_id"),
        "owner_uid": doc.get("owner_uid"),
        "name": doc.get("name"),
        "sport": doc.get("sport"),
        "weeks": doc.get("weeks", 1),
        "description": doc.get("description"),
        "items": doc.get("items") or [],
        "created_at": doc["created_at"].isoformat()
        if isinstance(doc.get("created_at"), datetime)
        else doc.get("created_at"),
    }


@router.get("/plans")
async def list_plans(
    org_id: str,
    ctx: OrgContext = Depends(require_org_role("owner", "admin", "coach", "athlete")),
):
    docs = (
        await db_mod.training_plans_col.find({"org_id": org_id})
        .sort("created_at", -1)
        .to_list(100)
    )
    return {"items": [_serialize_plan(d) for d in docs]}


@router.post("/plans", status_code=201)
async def create_plan(
    org_id: str,
    body: TrainingPlanIn,
    ctx: OrgContext = Depends(require_org_role("owner", "admin", "coach")),
):
    now = datetime.now(UTC)
    items_raw = []
    for item in body.items:
        steps_raw = [s.model_dump() for s in item.steps]
        est = estimate_workout(steps_raw)
        items_raw.append(
            {
                "day_offset": item.day_offset,
                "template_id": item.template_id,
                "title": item.title,
                "sport": item.sport,
                "description": item.description,
                "steps": steps_raw,
                **est,
            }
        )

    doc = {
        "_id": uuid.uuid4().hex,
        "org_id": org_id,
        "owner_uid": ctx.account.uid,
        "name": body.name,
        "sport": body.sport,
        "weeks": body.weeks,
        "description": body.description,
        "items": items_raw,
        "created_at": now,
        "updated_at": now,
    }
    await db_mod.training_plans_col.insert_one(doc)
    return {"plan": _serialize_plan(doc)}


@router.patch("/plans/{plan_id}")
async def update_plan(
    org_id: str,
    plan_id: str,
    body: TrainingPlanIn,
    ctx: OrgContext = Depends(require_org_role("owner", "admin", "coach")),
):
    doc = await db_mod.training_plans_col.find_one({"_id": plan_id})
    if not doc or doc.get("org_id") != org_id:
        raise HTTPException(status_code=404, detail="Plan introuvable.")

    items_raw = []
    for item in body.items:
        steps_raw = [s.model_dump() for s in item.steps]
        est = estimate_workout(steps_raw)
        items_raw.append(
            {
                "day_offset": item.day_offset,
                "template_id": item.template_id,
                "title": item.title,
                "sport": item.sport,
                "description": item.description,
                "steps": steps_raw,
                **est,
            }
        )

    updates = {
        "name": body.name,
        "sport": body.sport,
        "weeks": body.weeks,
        "description": body.description,
        "items": items_raw,
        "updated_at": datetime.now(UTC),
    }
    await db_mod.training_plans_col.update_one({"_id": plan_id}, {"$set": updates})
    updated = await db_mod.training_plans_col.find_one({"_id": plan_id})
    return {"plan": _serialize_plan(updated)}


@router.delete("/plans/{plan_id}")
async def delete_plan(
    org_id: str,
    plan_id: str,
    ctx: OrgContext = Depends(require_org_role("owner", "admin", "coach")),
):
    doc = await db_mod.training_plans_col.find_one({"_id": plan_id})
    if not doc or doc.get("org_id") != org_id:
        raise HTTPException(status_code=404, detail="Plan introuvable.")
    await db_mod.training_plans_col.delete_one({"_id": plan_id})
    return {"deleted": True, "id": plan_id}


@router.post("/plans/{plan_id}/apply", status_code=201)
async def apply_plan(
    org_id: str,
    plan_id: str,
    body: ApplyPlanIn,
    ctx: OrgContext = Depends(require_org_role("owner", "admin", "coach")),
):
    plan = await db_mod.training_plans_col.find_one({"_id": plan_id})
    if not plan or plan.get("org_id") != org_id:
        raise HTTPException(status_code=404, detail="Plan introuvable.")

    target_uids: list[str] = []
    if body.uid:
        member = await db_mod.memberships_col.find_one(
            {"org_id": org_id, "uid": body.uid, "status": "active"}
        )
        if not member:
            raise HTTPException(status_code=404, detail="Athlète introuvable.")
        target_uids.append(body.uid)
    elif body.group_id:
        group = await db_mod.groups_col.find_one({"_id": body.group_id, "org_id": org_id})
        if not group:
            raise HTTPException(status_code=404, detail="Groupe introuvable.")
        async for m in db_mod.memberships_col.find(
            {"org_id": org_id, "group_ids": body.group_id, "status": "active", "role": "athlete"},
            {"uid": 1},
        ):
            target_uids.append(m["uid"])

    if not target_uids:
        return {"created_count": 0, "target_uids": []}

    if body.start_date:
        anchor_start = body.start_date
    else:
        total_days = int(plan.get("weeks", 1)) * 7 - 1
        anchor_start = body.target_date - timedelta(days=total_days)

    now = datetime.now(UTC)
    docs_to_insert = []
    for target_uid in target_uids:
        for item in plan.get("items") or []:
            w_date = (anchor_start + timedelta(days=int(item["day_offset"]))).isoformat()
            docs_to_insert.append(
                {
                    "_id": uuid.uuid4().hex,
                    "org_id": org_id,
                    "uid": target_uid,
                    "date": w_date,
                    "template_id": item.get("template_id"),
                    "plan_id": plan_id,
                    "title": item["title"],
                    "sport": item["sport"],
                    "description": item.get("description"),
                    "steps": item.get("steps") or [],
                    "coach_note": None,
                    "status": "planned",
                    "activity_id": None,
                    "compliance": None,
                    "estimated_duration_s": item.get("estimated_duration_s", 0),
                    "estimated_distance_m": item.get("estimated_distance_m", 0),
                    "estimated_tss": item.get("estimated_tss", 0),
                    "blocks": item.get("blocks") or [],
                    "created_by": ctx.account.uid,
                    "created_at": now,
                    "updated_at": now,
                }
            )

    if docs_to_insert:
        await db_mod.planned_workouts_col.insert_many(docs_to_insert)
        for target_uid in target_uids:
            await recompute_pmc(org_id, target_uid)

    return {"created_count": len(docs_to_insert), "target_uids": target_uids}


# ── Objectifs / Compétitions A/B/C ─────────────────────────────────────────────

@router.get("/goals")
async def list_goals(
    org_id: str,
    uid: str | None = Query(default=None),
    ctx: OrgContext = Depends(require_org_role("owner", "admin", "coach", "athlete")),
):
    query: dict[str, Any] = {"org_id": org_id}
    if ctx.role == "athlete":
        query["uid"] = ctx.account.uid
    elif uid:
        query["uid"] = uid

    docs = await db_mod.goals_col.find(query).sort("date", 1).to_list(100)
    return {
        "items": [
            {
                "id": str(g["_id"]),
                "org_id": g["org_id"],
                "uid": g["uid"],
                "title": g["title"],
                "date": g["date"],
                "sport": g["sport"],
                "priority": g.get("priority", "A"),
                "distance_m": g.get("distance_m"),
                "notes": g.get("notes"),
            }
            for g in docs
        ]
    }


@router.post("/goals", status_code=201)
async def create_goal(
    org_id: str,
    body: GoalIn,
    ctx: OrgContext = Depends(require_org_role("owner", "admin", "coach", "athlete")),
):
    if ctx.role == "athlete" and body.uid != ctx.account.uid:
        raise HTTPException(status_code=403, detail="Accès refusé.")
    now = datetime.now(UTC)
    doc = {
        "_id": uuid.uuid4().hex,
        "org_id": org_id,
        "uid": body.uid,
        "title": body.title,
        "date": body.goal_date.isoformat(),
        "sport": body.sport,
        "priority": body.priority,
        "distance_m": body.distance_m,
        "notes": body.notes,
        "created_at": now,
    }
    await db_mod.goals_col.insert_one(doc)
    return {
        "goal": {
            "id": doc["_id"],
            "org_id": doc["org_id"],
            "uid": doc["uid"],
            "title": doc["title"],
            "date": doc["date"],
            "sport": doc["sport"],
            "priority": doc["priority"],
            "distance_m": doc["distance_m"],
            "notes": doc["notes"],
        }
    }


@router.delete("/goals/{goal_id}")
async def delete_goal(
    org_id: str,
    goal_id: str,
    ctx: OrgContext = Depends(require_org_role("owner", "admin", "coach", "athlete")),
):
    doc = await db_mod.goals_col.find_one({"_id": goal_id})
    if not doc or doc.get("org_id") != org_id:
        raise HTTPException(status_code=404, detail="Objectif introuvable.")
    if ctx.role == "athlete" and doc.get("uid") != ctx.account.uid:
        raise HTTPException(status_code=403, detail="Accès refusé.")
    await db_mod.goals_col.delete_one({"_id": goal_id})
    return {"deleted": True, "id": goal_id}
