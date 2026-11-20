"""Routes /v1/me/thresholds et /v1/orgs/{org_id}/athletes/{uid}/thresholds."""
from datetime import UTC, datetime
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request

from auth_firebase import Account, OrgContext, current_account, require_org_role
import db as db_mod
from models import ThresholdProfileIn
from services.metrics import compute_activity_metrics
from services.zones import resolve_thresholds_for_date, serialize_threshold_doc

router = APIRouter(tags=["thresholds"])


async def _recalculate_since(uid: str, org_id: str, sport: str, since_iso: str) -> int:
    since_dt = datetime.fromisoformat(f"{since_iso}T00:00:00+00:00")
    cursor = db_mod.activities_col.find(
        {"org_id": org_id, "uid": uid, "sport": sport, "start_time": {"$gte": since_dt}}
    )
    updated = 0
    async for act in cursor:
        streams = await db_mod.activity_streams_col.find_one(
            {"activity_id": act["_id"]}, {"_id": 0}
        )
        if not streams:
            continue
        thresholds = await resolve_thresholds_for_date(uid, sport, act["start_time"])
        metrics = compute_activity_metrics(
            streams,
            duration_s=float(act.get("duration_s") or 0),
            distance_m=float(act.get("distance_m") or 0),
            sport=sport,
            thresholds=thresholds,
        )
        await db_mod.activities_col.update_one(
            {"_id": act["_id"]},
            {
                "$set": {
                    "thresholds_snapshot": thresholds,
                    "metrics": metrics,
                    "updated_at": datetime.now(UTC),
                }
            },
        )
        updated += 1
    return updated


@router.get("/v1/me/thresholds")
async def get_my_thresholds(account: Account = Depends(current_account)):
    docs = (
        await db_mod.threshold_history_col.find({"uid": account.uid})
        .sort([("effective_from", -1), ("created_at", -1)])
        .to_list(100)
    )
    return {"items": [serialize_threshold_doc(d) for d in docs]}


@router.get("/v1/orgs/{org_id}/athletes/{uid}/thresholds")
async def get_athlete_thresholds(
    org_id: str,
    uid: str,
    ctx: OrgContext = Depends(require_org_role("owner", "admin", "coach", "athlete")),
):
    if ctx.role == "athlete" and ctx.account.uid != uid:
        raise HTTPException(
            status_code=403,
            detail="Un athlète ne peut consulter que ses propres seuils.",
        )
    membership = await db_mod.memberships_col.find_one(
        {"org_id": org_id, "uid": uid, "status": "active"}
    )
    if not membership:
        raise HTTPException(
            status_code=404, detail="Athlète introuvable dans cette organisation."
        )
    docs = (
        await db_mod.threshold_history_col.find({"uid": uid})
        .sort([("effective_from", -1), ("created_at", -1)])
        .to_list(100)
    )
    return {"items": [serialize_threshold_doc(d) for d in docs]}


@router.put("/v1/orgs/{org_id}/athletes/{uid}/thresholds")
async def upsert_athlete_thresholds(
    org_id: str,
    uid: str,
    body: ThresholdProfileIn,
    request: Request,
    ctx: OrgContext = Depends(require_org_role("owner", "admin", "coach", "athlete")),
):
    if ctx.role == "athlete" and ctx.account.uid != uid:
        raise HTTPException(
            status_code=403,
            detail="Un athlète ne peut modifier que ses propres seuils.",
        )
    membership = await db_mod.memberships_col.find_one(
        {"org_id": org_id, "uid": uid, "status": "active"}
    )
    if not membership:
        raise HTTPException(
            status_code=404, detail="Athlète introuvable dans cette organisation."
        )

    eff_iso = body.effective_from.isoformat()
    now = datetime.now(UTC)
    existing = await db_mod.threshold_history_col.find_one(
        {"uid": uid, "sport": body.sport, "effective_from": eff_iso}
    )
    payload = {
        "org_id": org_id,
        "uid": uid,
        "sport": body.sport,
        "effective_from": eff_iso,
        "ftp_w": body.ftp_w,
        "lthr_bpm": body.lthr_bpm,
        "threshold_pace_s_per_km": body.threshold_pace_s_per_km,
        "pace_500m_s": body.pace_500m_s,
        "css_s_per_100m": body.css_s_per_100m,
        "hr_max": body.hr_max,
        "hr_rest": body.hr_rest,
        "zones_model": body.zones_model,
        "updated_at": now,
        "updated_by": ctx.account.uid,
    }
    if existing:
        await db_mod.threshold_history_col.update_one(
            {"_id": existing["_id"]}, {"$set": payload}
        )
        doc_id = existing["_id"]
    else:
        doc_id = uuid.uuid4().hex
        payload["_id"] = doc_id
        payload["created_at"] = now
        await db_mod.threshold_history_col.insert_one(payload)

    r = getattr(request.app.state, "redis", None)
    if r is not None:
        try:
            await r.delete(f"athlete:thresholds:{uid}")
        except Exception:
            pass

    recalculated = 0
    if body.recalculate_activities:
        recalculated = await _recalculate_since(uid, org_id, body.sport, eff_iso)

    saved = await db_mod.threshold_history_col.find_one({"_id": doc_id})
    return {
        "threshold": serialize_threshold_doc(saved),
        "recalculated_activities": recalculated,
    }
