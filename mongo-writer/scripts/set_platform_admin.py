"""Pose ou retire le custom claim Firebase `platform_admin` sur un utilisateur."""
import argparse
import os
import sys

import firebase_admin
from firebase_admin import auth, credentials


def main() -> int:
    parser = argparse.ArgumentParser(description="Gérer le rôle platform_admin Firebase.")
    parser.add_argument("email", help="Adresse e-mail du compte Firebase.")
    parser.add_argument("--revoke", action="store_true", help="Retirer le privilège.")
    args = parser.parse_args()

    sa_file = os.environ.get(
        "FIREBASE_SERVICE_ACCOUNT_FILE",
        "/run/secrets/firebase/firebase-service-account.json",
    )
    project_id = os.environ.get("FIREBASE_PROJECT_ID")
    options = {"projectId": project_id} if project_id else None
    firebase_admin.initialize_app(credentials.Certificate(sa_file), options=options)

    user = auth.get_user_by_email(args.email)
    claims = dict(user.custom_claims or {})
    if args.revoke:
        claims.pop("platform_admin", None)
    else:
        claims["platform_admin"] = True
    auth.set_custom_user_claims(user.uid, claims)
    state = "retiré" if args.revoke else "accordé"
    print(f"platform_admin {state} pour {user.email} (uid={user.uid})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
