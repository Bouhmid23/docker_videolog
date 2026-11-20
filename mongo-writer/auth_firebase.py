"""Authentification Firebase et autorisations multi-tenant (MongoDB = source de vérité)."""
import asyncio
import logging
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from db import memberships_col, users_col

log = logging.getLogger(__name__)

FIREBASE_PROJECT_ID = os.environ.get("FIREBASE_PROJECT_ID")
FIREBASE_SERVICE_ACCOUNT_FILE = os.environ.get(
    "FIREBASE_SERVICE_ACCOUNT_FILE",
    "/run/secrets/firebase/firebase-service-account.json",
)
LEGACY_OPEN_ROOMS = os.environ.get("LEGACY_OPEN_ROOMS", "false").strip().lower() == "true"

bearer_scheme = HTTPBearer(auto_error=False)


def _firebase_app():
    import firebase_admin
    from firebase_admin import credentials

    try:
        return firebase_admin.get_app()
    except ValueError:
        if not os.path.isfile(FIREBASE_SERVICE_ACCOUNT_FILE):
            raise HTTPException(
                status_code=503,
                detail="Firebase Authentication is not configured on the server.",
            )
        options = {"projectId": FIREBASE_PROJECT_ID} if FIREBASE_PROJECT_ID else None
        try:
            return firebase_admin.initialize_app(
                credentials.Certificate(FIREBASE_SERVICE_ACCOUNT_FILE),
                options=options,
            )
        except Exception as error:
            log.exception("Firebase Admin SDK initialization failed")
            raise HTTPException(
                status_code=503,
                detail="Firebase Authentication is unavailable.",
            ) from error


async def firebase_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> dict:
    if not credentials or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=401,
            detail="A Firebase ID token is required.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    from firebase_admin import auth

    firebase_app = _firebase_app()
    try:
        claims = await asyncio.to_thread(
            auth.verify_id_token,
            credentials.credentials,
            check_revoked=True,
            clock_skew_seconds=10,
            app=firebase_app,
        )
    except (auth.InvalidIdTokenError, auth.ExpiredIdTokenError, auth.RevokedIdTokenError) as error:
        raise HTTPException(
            status_code=401,
            detail="The Firebase ID token is invalid or expired.",
            headers={"WWW-Authenticate": "Bearer"},
        ) from error
    except Exception as error:
        log.exception("Firebase ID token verification failed")
        raise HTTPException(
            status_code=503,
            detail="Firebase Authentication is temporarily unavailable.",
        ) from error

    if not claims.get("uid"):
        raise HTTPException(status_code=401, detail="Firebase token has no user identity.")
    return claims


async def mark_firebase_email_verified(uid: str) -> None:
    """Marque l'e-mail vérifié côté Firebase après acceptation d'invitation."""
    from firebase_admin import auth

    firebase_app = _firebase_app()
    await asyncio.to_thread(auth.update_user, uid, email_verified=True, app=firebase_app)


@dataclass
class MembershipInfo:
    org_id: str
    role: str
    group_ids: list[str] = field(default_factory=list)
    status: str = "active"


@dataclass
class Account:
    uid: str
    email: str | None
    email_verified: bool
    platform_admin: bool
    user_doc: dict | None
    memberships: list[MembershipInfo]

    def membership(self, org_id: str) -> MembershipInfo | None:
        for m in self.memberships:
            if m.org_id == org_id:
                return m
        return None

    @property
    def display_name(self) -> str:
        if self.user_doc:
            first = (self.user_doc.get("first_name") or "").strip()
            last = (self.user_doc.get("last_name") or "").strip()
            full = f"{first} {last}".strip()
            if full:
                return full
        return self.email or self.uid


@dataclass
class OrgContext:
    org_id: str
    role: str
    account: Account
    membership: MembershipInfo | None


async def current_account(claims: dict = Depends(firebase_user)) -> Account:
    uid = claims["uid"]
    email = (claims.get("email") or "").strip().lower() or None
    email_verified = bool(claims.get("email_verified"))
    platform_admin = bool(claims.get("platform_admin"))

    user_doc = await users_col.find_one({"_id": uid})
    if user_doc:
        now = datetime.now(UTC)
        last_login = user_doc.get("last_login_at")
        should_touch = True
        if isinstance(last_login, datetime):
            ll = last_login if last_login.tzinfo else last_login.replace(tzinfo=UTC)
            should_touch = (now - ll) > timedelta(hours=1)
        elif isinstance(last_login, str):
            try:
                ll = datetime.fromisoformat(last_login.replace("Z", "+00:00"))
                should_touch = (now - ll) > timedelta(hours=1)
            except ValueError:
                should_touch = True

        updates: dict = {}
        if should_touch:
            updates["last_login_at"] = now
        if email_verified and not user_doc.get("email_verified"):
            updates["email_verified"] = True
            user_doc["email_verified"] = True
        if updates:
            await users_col.update_one({"_id": uid}, {"$set": updates})

    cursor = memberships_col.find({"uid": uid, "status": "active"})
    raw_memberships = await cursor.to_list(length=100)
    memberships = [
        MembershipInfo(
            org_id=m["org_id"],
            role=m["role"],
            group_ids=list(m.get("group_ids") or []),
            status=m.get("status", "active"),
        )
        for m in raw_memberships
    ]

    return Account(
        uid=uid,
        email=email,
        email_verified=email_verified,
        platform_admin=platform_admin,
        user_doc=user_doc,
        memberships=memberships,
    )


def require_verified_email(account: Account = Depends(current_account)) -> Account:
    if not account.email_verified:
        raise HTTPException(
            status_code=403,
            detail="Veuillez vérifier votre adresse e-mail avant de continuer.",
        )
    return account


def require_org_role(*roles: str):
    async def dep(
        org_id: str,
        account: Account = Depends(require_verified_email),
    ) -> OrgContext:
        if account.platform_admin:
            return OrgContext(
                org_id=org_id,
                role="owner",
                account=account,
                membership=account.membership(org_id),
            )
        m = account.membership(org_id)
        if not m or m.role not in roles:
            raise HTTPException(
                status_code=403,
                detail="Accès refusé à cette organisation.",
            )
        return OrgContext(org_id=org_id, role=m.role, account=account, membership=m)

    return dep


def can_join_room(account: Account, room: dict) -> bool:
    if account.platform_admin:
        return True
    org_id = room.get("org_id")
    if not org_id:
        return LEGACY_OPEN_ROOMS
    m = account.membership(org_id)
    if not m:
        return False
    if m.role in {"owner", "admin"}:
        return True
    if account.uid in (room.get("participant_uids") or []):
        return True
    if account.uid == room.get("owner_uid"):
        return True
    if m.role == "coach":
        group_id = room.get("group_id")
        if not group_id or group_id in m.group_ids:
            return True
    return False


def can_control_recording(account: Account, room: dict) -> bool:
    if account.platform_admin:
        return True
    org_id = room.get("org_id")
    if not org_id:
        return LEGACY_OPEN_ROOMS
    m = account.membership(org_id)
    if not m:
        return False
    if m.role in {"owner", "admin"}:
        return True
    if m.role == "coach":
        if account.uid == room.get("owner_uid"):
            return True
        if account.uid in (room.get("recording_operator_uids") or []):
            return True
    return False
