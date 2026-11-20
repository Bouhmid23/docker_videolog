"""Routes /v1/orgs/{org_id}/workouts et /calendar : bibliothèque, séances planifiées, export & calendrier."""
from datetime import UTC, date, datetime, timedelta
import os
from typing import Any
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response

from auth_firebase import OrgContext, require_org_role
import db as db_mod
from mailer import send_comment_notification_email
from models import (
    ActivityCommentIn,
    PlannedWorkoutIn,
    PlannedWorkoutPatchIn,
    WorkoutTemplateIn,
)
from services.workout_estimator import estimate_workout
from services.workout_export import (
    export_workout_erg,
    export_workout_fit,
    export_workout_zwo,
)
from services.workout_matching import compute_compliance
from services.pmc import recompute_pmc
from services.zones import resolve_thresholds_for_date

router = APIRouter(prefix="/v1/orgs/{org_id}", tags=["workouts"])


def _serialize_template(doc: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(doc["_id"]),
        "org_id": doc.get("org_id"),
        "owner_uid": doc.get("owner_uid"),
        "name": doc.get("name"),
        "sport": doc.get("sport"),
        "description": doc.get("description"),
        "tags": doc.get("tags") or [],
        "steps": doc.get("steps") or [],
        "estimated_duration_s": doc.get("estimated_duration_s", 0),
        "estimated_distance_m": doc.get("estimated_distance_m", 0),
        "estimated_tss": doc.get("estimated_tss", 0),
        "blocks": doc.get("blocks") or [],
        "created_at": doc["created_at"].isoformat()
        if isinstance(doc.get("created_at"), datetime)
        else doc.get("created_at"),
    }


def _serialize_planned(doc: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(doc["_id"]),
        "org_id": doc.get("org_id"),
        "uid": doc.get("uid"),
        "date": doc.get("date"),
        "start_time": doc.get("start_time"),
        "template_id": doc.get("template_id"),
        "title": doc.get("title"),
        "sport": doc.get("sport"),
        "description": doc.get("description"),
        "steps": doc.get("steps") or [],
        "coach_note": doc.get("coach_note"),
        "status": doc.get("status", "planned"),
        "activity_id": doc.get("activity_id"),
        "compliance": doc.get("compliance"),
        "estimated_duration_s": doc.get("estimated_duration_s", 0),
        "estimated_distance_m": doc.get("estimated_distance_m", 0),
        "estimated_tss": doc.get("estimated_tss", 0),
        "blocks": doc.get("blocks") or [],
    }


# ── Bibliothèque de séances ────────────────────────────────────────────────────

@router.get("/workouts/templates")
async def list_workout_templates(
    org_id: str,
    sport: str | None = Query(default=None),
    tag: str | None = Query(default=None),
    ctx: OrgContext = Depends(require_org_role("owner", "admin", "coach", "athlete")),
):
    query: dict[str, Any] = {"org_id": org_id}
    if sport:
        query["sport"] = sport
    if tag:
        query["tags"] = tag
    docs = (
        await db_mod.workout_templates_col.find(query)
        .sort("created_at", -1)
        .to_list(200)
    )
    return {"items": [_serialize_template(d) for d in docs]}


@router.post("/workouts/templates", status_code=201)
async def create_workout_template(
    org_id: str,
    body: WorkoutTemplateIn,
    ctx: OrgContext = Depends(require_org_role("owner", "admin", "coach")),
):
    steps_raw = [s.model_dump() for s in body.steps]
    est = estimate_workout(steps_raw)
    now = datetime.now(UTC)
    doc = {
        "_id": uuid.uuid4().hex,
        "org_id": org_id,
        "owner_uid": ctx.account.uid,
        "name": body.name,
        "sport": body.sport,
        "description": body.description,
        "tags": [t.strip().lower() for t in body.tags if t.strip()],
        "steps": steps_raw,
        **est,
        "created_at": now,
        "updated_at": now,
    }
    await db_mod.workout_templates_col.insert_one(doc)
    return {"template": _serialize_template(doc)}


@router.patch("/workouts/templates/{template_id}")
async def update_workout_template(
    org_id: str,
    template_id: str,
    body: WorkoutTemplateIn,
    ctx: OrgContext = Depends(require_org_role("owner", "admin", "coach")),
):
    doc = await db_mod.workout_templates_col.find_one({"_id": template_id})
    if not doc or doc.get("org_id") != org_id:
        raise HTTPException(status_code=404, detail="Modèle introuvable.")
    steps_raw = [s.model_dump() for s in body.steps]
    est = estimate_workout(steps_raw)
    updates = {
        "name": body.name,
        "sport": body.sport,
        "description": body.description,
        "tags": [t.strip().lower() for t in body.tags if t.strip()],
        "steps": steps_raw,
        **est,
        "updated_at": datetime.now(UTC),
    }
    await db_mod.workout_templates_col.update_one({"_id": template_id}, {"$set": updates})
    updated = await db_mod.workout_templates_col.find_one({"_id": template_id})
    return {"template": _serialize_template(updated)}


@router.post("/workouts/templates/{template_id}/duplicate", status_code=201)
async def duplicate_workout_template(
    org_id: str,
    template_id: str,
    ctx: OrgContext = Depends(require_org_role("owner", "admin", "coach")),
):
    doc = await db_mod.workout_templates_col.find_one({"_id": template_id})
    if not doc or doc.get("org_id") != org_id:
        raise HTTPException(status_code=404, detail="Modèle introuvable.")
    now = datetime.now(UTC)
    copy_doc = {
        **doc,
        "_id": uuid.uuid4().hex,
        "owner_uid": ctx.account.uid,
        "name": f"{doc['name']} (copie)",
        "created_at": now,
        "updated_at": now,
    }
    await db_mod.workout_templates_col.insert_one(copy_doc)
    return {"template": _serialize_template(copy_doc)}


@router.delete("/workouts/templates/{template_id}")
async def delete_workout_template(
    org_id: str,
    template_id: str,
    ctx: OrgContext = Depends(require_org_role("owner", "admin", "coach")),
):
    doc = await db_mod.workout_templates_col.find_one({"_id": template_id})
    if not doc or doc.get("org_id") != org_id:
        raise HTTPException(status_code=404, detail="Modèle introuvable.")
    await db_mod.workout_templates_col.delete_one({"_id": template_id})
    return {"deleted": True, "id": template_id}


# ── Séances planifiées ─────────────────────────────────────────────────────────

@router.post("/workouts/planned", status_code=201)
async def create_planned_workout(
    org_id: str,
    body: PlannedWorkoutIn,
    ctx: OrgContext = Depends(require_org_role("owner", "admin", "coach", "athlete")),
):
    if ctx.role == "athlete" and body.uid != ctx.account.uid:
        raise HTTPException(
            status_code=403,
            detail="Un athlète ne peut planifier que pour lui-même.",
        )
    member = await db_mod.memberships_col.find_one(
        {"org_id": org_id, "uid": body.uid, "status": "active"}
    )
    if not member:
        raise HTTPException(status_code=404, detail="Athlète introuvable.")

    title = body.title
    sport = body.sport
    description = body.description
    steps_raw = [s.model_dump() for s in body.steps] if body.steps is not None else None

    if body.template_id:
        tpl = await db_mod.workout_templates_col.find_one({"_id": body.template_id})
        if not tpl or tpl.get("org_id") != org_id:
            raise HTTPException(status_code=404, detail="Modèle introuvable.")
        title = title or tpl["name"]
        sport = sport or tpl["sport"]
        description = description if description is not None else tpl.get("description")
        if steps_raw is None:
            steps_raw = tpl.get("steps") or []

    if not title:
        raise HTTPException(status_code=422, detail="Le titre de la séance est requis.")
    sport = sport or "cyclisme"
    steps_raw = steps_raw or []
    est = estimate_workout(steps_raw)
    now = datetime.now(UTC)
    doc = {
        "_id": uuid.uuid4().hex,
        "org_id": org_id,
        "uid": body.uid,
        "date": body.workout_date.isoformat(),
        "start_time": body.start_time,
        "template_id": body.template_id,
        "title": title,
        "sport": sport,
        "description": description,
        "steps": steps_raw,
        "coach_note": body.coach_note,
        "status": "planned",
        "activity_id": None,
        "compliance": None,
        **est,
        "created_by": ctx.account.uid,
        "created_at": now,
        "updated_at": now,
    }
    await db_mod.planned_workouts_col.insert_one(doc)
    await recompute_pmc(org_id, body.uid)
    return {"planned_workout": _serialize_planned(doc)}


@router.patch("/workouts/planned/{planned_id}")
async def update_planned_workout(
    org_id: str,
    planned_id: str,
    body: PlannedWorkoutPatchIn,
    ctx: OrgContext = Depends(require_org_role("owner", "admin", "coach", "athlete")),
):
    doc = await db_mod.planned_workouts_col.find_one({"_id": planned_id})
    if not doc or doc.get("org_id") != org_id:
        raise HTTPException(status_code=404, detail="Séance planifiée introuvable.")
    if ctx.role == "athlete" and doc.get("uid") != ctx.account.uid:
        raise HTTPException(status_code=403, detail="Accès refusé.")

    updates: dict[str, Any] = {"updated_at": datetime.now(UTC)}
    if body.workout_date is not None:
        updates["date"] = body.workout_date.isoformat()
    if body.start_time is not None:
        updates["start_time"] = body.start_time
    if body.title is not None:
        updates["title"] = body.title
    if body.sport is not None:
        updates["sport"] = body.sport
    if body.description is not None:
        updates["description"] = body.description
    if body.coach_note is not None:
        updates["coach_note"] = body.coach_note
    if body.status is not None:
        updates["status"] = body.status
    if body.steps is not None:
        steps_raw = [s.model_dump() for s in body.steps]
        updates["steps"] = steps_raw
        updates.update(estimate_workout(steps_raw))

    await db_mod.planned_workouts_col.update_one({"_id": planned_id}, {"$set": updates})
    updated = await db_mod.planned_workouts_col.find_one({"_id": planned_id})
    return {"planned_workout": _serialize_planned(updated)}


@router.delete("/workouts/planned/{planned_id}")
async def delete_planned_workout(
    org_id: str,
    planned_id: str,
    ctx: OrgContext = Depends(require_org_role("owner", "admin", "coach", "athlete")),
):
    doc = await db_mod.planned_workouts_col.find_one({"_id": planned_id})
    if not doc or doc.get("org_id") != org_id:
        raise HTTPException(status_code=404, detail="Séance planifiée introuvable.")
    if ctx.role == "athlete" and doc.get("uid") != ctx.account.uid:
        raise HTTPException(status_code=403, detail="Accès refusé.")
    await db_mod.planned_workouts_col.delete_one({"_id": planned_id})
    await recompute_pmc(org_id, doc["uid"])
    return {"deleted": True, "id": planned_id}


@router.get("/workouts/planned/{planned_id}/comments")
async def list_planned_workout_comments(
    org_id: str,
    planned_id: str,
    ctx: OrgContext = Depends(require_org_role("owner", "admin", "coach", "athlete")),
):
    doc = await db_mod.planned_workouts_col.find_one({"_id": planned_id})
    if not doc or doc.get("org_id") != org_id:
        raise HTTPException(status_code=404, detail="Séance planifiée introuvable.")
    if ctx.role == "athlete" and doc.get("uid") != ctx.account.uid:
        raise HTTPException(status_code=403, detail="Accès refusé.")
    comments = (
        await db_mod.workout_comments_col.find(
            {"org_id": org_id, "planned_workout_id": planned_id}
        )
        .sort("created_at", 1)
        .to_list(200)
    )
    return {
        "items": [
            {
                "id": str(c["_id"]),
                "planned_workout_id": c["planned_workout_id"],
                "author_uid": c["author_uid"],
                "author_name": c.get("author_name") or c["author_uid"],
                "author_role": c.get("author_role") or "athlete",
                "body": c["body"],
                "created_at": c["created_at"].isoformat()
                if isinstance(c.get("created_at"), datetime)
                else c.get("created_at"),
            }
            for c in comments
        ]
    }


@router.post("/workouts/planned/{planned_id}/comments", status_code=201)
async def create_planned_workout_comment(
    org_id: str,
    planned_id: str,
    body: ActivityCommentIn,
    ctx: OrgContext = Depends(require_org_role("owner", "admin", "coach", "athlete")),
):
    planned = await db_mod.planned_workouts_col.find_one({"_id": planned_id})
    if not planned or planned.get("org_id") != org_id:
        raise HTTPException(status_code=404, detail="Séance planifiée introuvable.")
    if ctx.role == "athlete" and planned.get("uid") != ctx.account.uid:
        raise HTTPException(status_code=403, detail="Accès refusé.")

    now = datetime.now(UTC)
    planned_date_str = planned.get("date")
    if planned_date_str:
        try:
            planned_d = date.fromisoformat(str(planned_date_str)[:10])
            today_d = now.date()
            if planned_d < today_d:
                raise HTTPException(
                    status_code=400,
                    detail="Impossible d'envoyer un message sur une séance déjà passée.",
                )
            if planned_d == today_d and planned.get("start_time"):
                hh, mm = str(planned["start_time"]).split(":")
                end_dt = datetime(
                    planned_d.year,
                    planned_d.month,
                    planned_d.day,
                    int(hh),
                    int(mm),
                    tzinfo=UTC,
                ) + timedelta(seconds=int(planned.get("estimated_duration_s") or 3600))
                if end_dt < now:
                    raise HTTPException(
                        status_code=400,
                        detail="Impossible d'envoyer un message sur une séance déjà passée.",
                    )
        except ValueError:
            pass
    doc = {
        "_id": uuid.uuid4().hex,
        "org_id": org_id,
        "planned_workout_id": planned_id,
        "author_uid": ctx.account.uid,
        "author_name": ctx.account.display_name,
        "author_role": ctx.role,
        "body": body.body,
        "created_at": now,
    }
    await db_mod.workout_comments_col.insert_one(doc)

    recipient_uids: list[str] = []
    if ctx.account.uid != planned.get("uid") and planned.get("uid"):
        recipient_uids.append(planned["uid"])
    else:
        staff = await db_mod.memberships_col.find(
            {
                "org_id": org_id,
                "status": "active",
                "role": {"$in": ["owner", "admin", "coach"]},
                "uid": {"$ne": ctx.account.uid},
            },
            {"uid": 1},
        ).to_list(20)
        recipient_uids.extend(s["uid"] for s in staff)

    if recipient_uids:
        unique_recipients = list(dict.fromkeys(recipient_uids))
        title_str = planned.get("title") or "Séance planifiée"
        author_label = ctx.account.display_name or "Membre"
        notifs = [
            {
                "_id": uuid.uuid4().hex,
                "recipient_uid": r_uid,
                "org_id": org_id,
                "type": "workout_comment",
                "title": f"Message de {author_label}",
                "body": f"{title_str} : {body.body[:120]}",
                "link": "/app/calendar",
                "read": False,
                "created_at": now,
            }
            for r_uid in unique_recipients
        ]
        await db_mod.notifications_col.insert_many(notifs)

        app_url = os.environ.get("APP_PUBLIC_URL", "https://app.videolog.app").rstrip("/")
        async for u in db_mod.users_col.find(
            {"_id": {"$in": unique_recipients}}, {"email": 1}
        ):
            if u.get("email"):
                await send_comment_notification_email(
                    to_email=u["email"],
                    author_name=author_label,
                    item_title=title_str,
                    comment_body=body.body,
                    item_url=f"{app_url}/app/calendar",
                )

    return {
        "comment": {
            "id": doc["_id"],
            "planned_workout_id": doc["planned_workout_id"],
            "author_uid": doc["author_uid"],
            "author_name": doc["author_name"],
            "author_role": doc["author_role"],
            "body": doc["body"],
            "created_at": now.isoformat(),
        }
    }


@router.post("/workouts/planned/{planned_id}/link-activity")
async def link_activity_to_planned(
    org_id: str,
    planned_id: str,
    activity_id: str = Query(...),
    ctx: OrgContext = Depends(require_org_role("owner", "admin", "coach", "athlete")),
):
    doc = await db_mod.planned_workouts_col.find_one({"_id": planned_id})
    if not doc or doc.get("org_id") != org_id:
        raise HTTPException(status_code=404, detail="Séance planifiée introuvable.")
    act = await db_mod.activities_col.find_one({"_id": activity_id})
    if not act or act.get("org_id") != org_id:
        raise HTTPException(status_code=404, detail="Activité introuvable.")
    if ctx.role == "athlete" and doc.get("uid") != ctx.account.uid:
        raise HTTPException(status_code=403, detail="Accès refusé.")

    compliance = compute_compliance(doc, act)
    status = "done" if compliance["color"] != "red" else "partial"
    now = datetime.now(UTC)
    await db_mod.planned_workouts_col.update_one(
        {"_id": planned_id},
        {"$set": {"activity_id": activity_id, "compliance": compliance, "status": status, "updated_at": now}},
    )
    await db_mod.activities_col.update_one(
        {"_id": activity_id},
        {"$set": {"planned_workout_id": planned_id, "updated_at": now}},
    )
    updated = await db_mod.planned_workouts_col.find_one({"_id": planned_id})
    return {"planned_workout": _serialize_planned(updated)}


@router.get("/workouts/planned/{planned_id}/export")
async def export_planned_workout(
    org_id: str,
    planned_id: str,
    format: str = Query(default="fit", pattern="^(fit|zwo|erg)$"),
    ctx: OrgContext = Depends(require_org_role("owner", "admin", "coach", "athlete")),
):
    doc = await db_mod.planned_workouts_col.find_one({"_id": planned_id})
    if not doc or doc.get("org_id") != org_id:
        raise HTTPException(status_code=404, detail="Séance planifiée introuvable.")
    if ctx.role == "athlete" and doc.get("uid") != ctx.account.uid:
        raise HTTPException(status_code=403, detail="Accès refusé.")

    thresholds = await resolve_thresholds_for_date(doc["uid"], doc["sport"])
    slug = "".join(c if c.isalnum() else "_" for c in (doc.get("title") or "workout")).strip("_") or "workout"

    if format == "fit":
        payload = export_workout_fit(
            doc["title"],
            doc["sport"],
            doc.get("steps") or [],
            ftp_w=thresholds.get("ftp_w"),
            lthr_bpm=thresholds.get("lthr_bpm"),
        )
        return Response(
            content=payload,
            media_type="application/octet-stream",
            headers={"Content-Disposition": f'attachment; filename="{slug}.fit"'},
        )
    if format == "zwo":
        xml = export_workout_zwo(
            doc["title"],
            doc.get("description"),
            doc.get("steps") or [],
            sport=doc.get("sport") or "cyclisme",
        )
        return Response(
            content=xml.encode("utf-8"),
            media_type="application/xml",
            headers={"Content-Disposition": f'attachment; filename="{slug}.zwo"'},
        )

    erg = export_workout_erg(
        doc["title"], doc.get("steps") or [], ftp_w=thresholds.get("ftp_w")
    )
    return Response(
        content=erg.encode("utf-8"),
        media_type="text/plain",
        headers={"Content-Disposition": f'attachment; filename="{slug}.erg"'},
    )


# ── Calendrier ─────────────────────────────────────────────────────────────────

@router.get("/calendar")
async def get_calendar(
    org_id: str,
    from_date: str = Query(..., alias="from"),
    to_date: str = Query(..., alias="to"),
    uid: str | None = Query(default=None),
    ctx: OrgContext = Depends(require_org_role("owner", "admin", "coach", "athlete")),
):
    target_uid = ctx.account.uid if ctx.role == "athlete" else uid
    pw_query: dict[str, Any] = {
        "org_id": org_id,
        "date": {"$gte": from_date, "$lte": to_date},
    }
    act_query: dict[str, Any] = {
        "org_id": org_id,
        "start_time": {
            "$gte": datetime.fromisoformat(f"{from_date}T00:00:00+00:00"),
            "$lte": datetime.fromisoformat(f"{to_date}T23:59:59+00:00"),
        },
    }
    goal_query: dict[str, Any] = {
        "org_id": org_id,
        "date": {"$gte": from_date, "$lte": to_date},
    }

    if target_uid:
        pw_query["uid"] = target_uid
        act_query["uid"] = target_uid
        goal_query["uid"] = target_uid

    planned = await db_mod.planned_workouts_col.find(pw_query).sort("date", 1).to_list(500)
    activities = await db_mod.activities_col.find(act_query).sort("start_time", 1).to_list(500)
    goals = await db_mod.goals_col.find(goal_query).sort("date", 1).to_list(100)

    # Agrégats hebdomadaires (clé = lundi YYYY-MM-DD)
    weekly: dict[str, dict[str, Any]] = {}

    def _week_key(date_str: str) -> str:
        d = datetime.fromisoformat(f"{date_str[:10]}T00:00:00+00:00").date()
        monday = d - timedelta(days=d.weekday())
        return monday.isoformat()

    for p in planned:
        wk = _week_key(p["date"])
        bucket = weekly.setdefault(
            wk,
            {"week_start": wk, "planned_duration_s": 0, "actual_duration_s": 0, "planned_tss": 0.0, "actual_tss": 0.0},
        )
        bucket["planned_duration_s"] += int(p.get("estimated_duration_s") or 0)
        bucket["planned_tss"] += float(p.get("estimated_tss") or 0)

    act_items = []
    for a in activities:
        st = a["start_time"]
        d_iso = st.date().isoformat() if isinstance(st, datetime) else str(st)[:10]
        wk = _week_key(d_iso)
        bucket = weekly.setdefault(
            wk,
            {"week_start": wk, "planned_duration_s": 0, "actual_duration_s": 0, "planned_tss": 0.0, "actual_tss": 0.0},
        )
        bucket["actual_duration_s"] += int(round(float(a.get("duration_s") or 0)))
        bucket["actual_tss"] += float((a.get("metrics") or {}).get("tss") or 0)
        act_items.append(
            {
                "id": str(a["_id"]),
                "uid": a.get("uid"),
                "date": d_iso,
                "sport": a.get("sport"),
                "title": a.get("title"),
                "duration_s": a.get("duration_s", 0),
                "distance_m": a.get("distance_m", 0),
                "tss": (a.get("metrics") or {}).get("tss"),
                "planned_workout_id": a.get("planned_workout_id"),
            }
        )

    for b in weekly.values():
        b["planned_tss"] = round(b["planned_tss"], 1)
        b["actual_tss"] = round(b["actual_tss"], 1)

    return {
        "planned_workouts": [_serialize_planned(p) for p in planned],
        "activities": act_items,
        "goals": [
            {
                "id": str(g["_id"]),
                "uid": g["uid"],
                "title": g["title"],
                "date": g["date"],
                "sport": g["sport"],
                "priority": g.get("priority", "A"),
                "distance_m": g.get("distance_m"),
                "notes": g.get("notes"),
            }
            for g in goals
        ],
        "weekly_totals": sorted(weekly.values(), key=lambda x: x["week_start"]),
    }
