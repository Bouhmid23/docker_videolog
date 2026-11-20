"""Rattache les rooms existantes sans org_id à une organisation cible."""
import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db import orgs_col, rooms, sess  # noqa: E402


async def run(org_id: str, owner_uid: str | None) -> int:
    org = await orgs_col.find_one({"_id": org_id})
    if not org:
        print(f"Erreur : organisation {org_id} introuvable.", file=sys.stderr)
        return 1
    effective_owner = owner_uid or org.get("owner_uid")
    filter_q = {"$or": [{"org_id": {"$exists": False}}, {"org_id": None}]}
    res_rooms = await rooms.update_many(
        filter_q,
        {
            "$set": {
                "org_id": org_id,
                "owner_uid": effective_owner,
            }
        },
    )
    res_sess = await sess.update_many(filter_q, {"$set": {"org_id": org_id}})
    print(
        f"Rattaché {res_rooms.modified_count} room(s) et {res_sess.modified_count} session(s) à {org['name']} ({org_id})."
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Rattacher les anciennes rooms à une organisation.")
    parser.add_argument("org_id", help="Identifiant de l'organisation cible.")
    parser.add_argument("--owner-uid", default=None, help="UID propriétaire (défaut : owner de l'org).")
    args = parser.parse_args()
    return asyncio.run(run(args.org_id, args.owner_uid))


if __name__ == "__main__":
    sys.exit(main())
