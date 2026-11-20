"""Connexion MongoDB partagée et création idempotente des index."""
import logging
import os

from motor.motor_asyncio import AsyncIOMotorClient

log = logging.getLogger(__name__)

MONGO_URL = os.environ.get("MONGO_URL", "mongodb://mongo:27017/videolog")
MONGO_DB_NAME = os.environ.get("MONGO_DB_NAME", "videolog")

mongo = AsyncIOMotorClient(MONGO_URL)
db = mongo[MONGO_DB_NAME]

# Collections existantes
col = db.telemetry
recs = db.recordings
sess = db.sessions
alerts = db.alerts
rooms = db.room_state

# Nouvelles collections Back-office / Multi-tenant
users_col = db.users
orgs_col = db.organizations
memberships_col = db.memberships
groups_col = db.groups
invitations_col = db.invitations
markers_col = db.markers

# Collections Phase 1 : Seuils datés, Activités, Flux 1 Hz, Commentaires & Vidéos
threshold_history_col = db.threshold_history
activities_col = db.activities
activity_streams_col = db.activity_streams
activity_comments_col = db.activity_comments
video_assets_col = db.video_assets

# Collections Phase 2 : Planification (séances, calendrier, plans, objectifs)
workout_templates_col = db.workout_templates
planned_workouts_col = db.planned_workouts
workout_comments_col = db.workout_comments
training_plans_col = db.training_plans
goals_col = db.goals

# Collections Phase 3 : PMC quotidien & Forme / VFC
daily_metrics_col = db.daily_metrics
wellness_entries_col = db.wellness_entries
notifications_col = db.notifications
join_requests_col = db.join_requests


async def ensure_indexes() -> None:
    """Crée les index requis au démarrage (idempotent sur MongoDB existant)."""
    try:
        await users_col.create_index("email", unique=True, sparse=True)
        await memberships_col.create_index(
            [("org_id", 1), ("uid", 1)], unique=True
        )
        await memberships_col.create_index([("uid", 1), ("status", 1)])
        await groups_col.create_index("org_id")
        await invitations_col.create_index("token_hash", unique=True)
        await invitations_col.create_index([("org_id", 1), ("status", 1)])
        await rooms.create_index([("org_id", 1), ("updated_at", -1)])
        await rooms.create_index("participant_uids")
        await col.create_index([("room_name", 1), ("pts_ms", 1)])
        await markers_col.create_index(
            [("org_id", 1), ("room_name", 1), ("pts_ms", 1)]
        )
        await threshold_history_col.create_index(
            [("uid", 1), ("sport", 1), ("effective_from", -1)]
        )
        await threshold_history_col.create_index([("org_id", 1), ("uid", 1)])
        await activities_col.create_index(
            [("org_id", 1), ("uid", 1), ("start_time", -1)]
        )
        await activities_col.create_index([("org_id", 1), ("start_time", -1)])
        await activities_col.create_index([("org_id", 1), ("dedup_hash", 1)])
        await activity_streams_col.create_index("activity_id", unique=True)
        await activity_comments_col.create_index(
            [("activity_id", 1), ("created_at", 1)]
        )
        await video_assets_col.create_index([("activity_id", 1), ("created_at", 1)])
        await workout_templates_col.create_index([("org_id", 1), ("sport", 1)])
        await planned_workouts_col.create_index([("org_id", 1), ("uid", 1), ("date", 1)])
        await planned_workouts_col.create_index([("org_id", 1), ("date", 1)])
        await training_plans_col.create_index([("org_id", 1), ("sport", 1)])
        await goals_col.create_index([("org_id", 1), ("uid", 1), ("date", 1)])
        await daily_metrics_col.create_index(
            [("org_id", 1), ("uid", 1), ("date", 1)], unique=True
        )
        await wellness_entries_col.create_index(
            [("org_id", 1), ("uid", 1), ("date", 1)], unique=True
        )
        await orgs_col.create_index([("status", 1), ("is_public", 1), ("type", 1)])
        await join_requests_col.create_index(
            [("org_id", 1), ("status", 1), ("created_at", -1)]
        )
        await join_requests_col.create_index(
            [("uid", 1), ("status", 1), ("created_at", -1)]
        )
        await join_requests_col.create_index(
            [("org_id", 1), ("uid", 1), ("status", 1)]
        )
        await notifications_col.create_index(
            [("recipient_uid", 1), ("created_at", -1)]
        )
        await notifications_col.create_index(
            [("recipient_uid", 1), ("read", 1)]
        )
    except Exception:
        log.exception("Failed to ensure MongoDB indexes")
