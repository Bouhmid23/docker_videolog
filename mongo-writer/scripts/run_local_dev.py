"""Lanceur de développement local (Windows / sans Docker ni MongoDB).

Usage :
  python scripts/run_local_dev.py
  python scripts/run_local_dev.py --demo-auth   # simule un utilisateur Firebase si secrets/ est absent
"""
import argparse
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from mongomock_motor import AsyncMongoMockClient
import uvicorn

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
API_DIR = Path(__file__).resolve().parent.parent
if str(API_DIR) not in sys.path:
    sys.path.insert(0, str(API_DIR))

load_dotenv(ROOT_DIR / ".env")
os.environ.setdefault("APP_PUBLIC_URL", "http://localhost:5173")
os.environ.setdefault(
    "FIREBASE_SERVICE_ACCOUNT_FILE",
    str(ROOT_DIR / "secrets" / "firebase-service-account.json"),
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Lancer l'API Videolog en local.")
    parser.add_argument(
        "--demo-auth",
        action="store_true",
        help="Simuler un utilisateur Firebase connecté (utile sans fichier secrets/).",
    )
    parser.add_argument("--port", type=int, default=8001)
    args = parser.parse_args()

    import db as db_mod
    import auth_firebase
    import api as api_mod
    from routers import accounts, invitations, organizations, sessions

    mock_client = AsyncMongoMockClient()
    mock_db = mock_client["videolog_local"]
    mapping = {
        "col": mock_db.telemetry,
        "recs": mock_db.recordings,
        "sess": mock_db.sessions,
        "alerts": mock_db.alerts,
        "rooms": mock_db.room_state,
        "users_col": mock_db.users,
        "orgs_col": mock_db.organizations,
        "memberships_col": mock_db.memberships,
        "groups_col": mock_db.groups,
        "invitations_col": mock_db.invitations,
    }
    for attr, coll in mapping.items():
        setattr(db_mod, attr, coll)
        for mod in (auth_firebase, api_mod, accounts, organizations, invitations, sessions):
            if hasattr(mod, attr):
                setattr(mod, attr, coll)

    if args.demo_auth:
        async def _demo_claims():
            return {
                "uid": "local-dev-user",
                "email": "coach.local@videolog.app",
                "email_verified": True,
                "platform_admin": False,
            }

        api_mod.app.dependency_overrides[auth_firebase.firebase_user] = _demo_claims
        print("Mode --demo-auth actif : utilisateur simulé coach.local@videolog.app")

    print(f"API locale démarrée sur http://127.0.0.1:{args.port} (MongoDB en mémoire)")
    uvicorn.run(api_mod.app, host="127.0.0.1", port=args.port, log_level="info")


if __name__ == "__main__":
    main()
