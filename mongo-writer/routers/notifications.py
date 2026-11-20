"""Routes /v1/me/notifications : centre de notifications unifié."""
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException

from auth_firebase import Account, current_account
import db as db_mod

router = APIRouter(prefix="/v1/me/notifications", tags=["notifications"])


@router.get("")
async def list_notifications(
    account: Account = Depends(current_account),
):
    """Retourne les 30 dernières notifications de l'utilisateur et le compteur non lu."""
    docs = (
        await db_mod.notifications_col.find({"recipient_uid": account.uid})
        .sort("created_at", -1)
        .to_list(30)
    )
    unread_count = await db_mod.notifications_col.count_documents(
        {"recipient_uid": account.uid, "read": False}
    )
    items = []
    for d in docs:
        created = d.get("created_at")
        items.append(
            {
                "id": str(d["_id"]),
                "type": d.get("type", "info"),
                "title": d.get("title", ""),
                "body": d.get("body", ""),
                "link": d.get("link"),
                "org_id": d.get("org_id"),
                "read": bool(d.get("read", False)),
                "created_at": created.isoformat() if isinstance(created, datetime) else created,
            }
        )
    return {"items": items, "unread_count": unread_count}


@router.post("/read-all")
async def mark_all_notifications_read(
    account: Account = Depends(current_account),
):
    await db_mod.notifications_col.update_many(
        {"recipient_uid": account.uid, "read": False},
        {"$set": {"read": True, "read_at": datetime.now(UTC)}},
    )
    return {"status": "ok"}


@router.post("/{notif_id}/read")
async def mark_notification_read(
    notif_id: str,
    account: Account = Depends(current_account),
):
    res = await db_mod.notifications_col.update_one(
        {"_id": notif_id, "recipient_uid": account.uid},
        {"$set": {"read": True, "read_at": datetime.now(UTC)}},
    )
    if res.matched_count == 0:
        raise HTTPException(status_code=404, detail="Notification introuvable.")
    return {"status": "ok", "id": notif_id}
