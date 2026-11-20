"""
Dashboard API — Videolog
Point d'entrée FastAPI modulaire : assemblage des routers et cycle de vie Redis/Mongo.
"""
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.openapi.docs import (
    get_swagger_ui_html,
    get_swagger_ui_oauth2_redirect_html,
)
import redis.asyncio as aioredis

from core_config import REDIS_URL
from db import ensure_indexes
from routers import (
    accounts,
    activities,
    admin_ops,
    analytics,
    directory,
    invitations,
    join_requests,
    live_rooms,
    live_token,
    notifications,
    organizations,
    recordings,
    sessions as org_sessions,
    thresholds,
    training_plans,
    workouts,
)


@asynccontextmanager
async def lifespan(application: FastAPI):
    redis_client = aioredis.from_url(REDIS_URL, decode_responses=True)
    application.state.redis = redis_client
    await ensure_indexes()
    try:
        yield
    finally:
        await redis_client.aclose()


app = FastAPI(title="Videolog Dashboard API", docs_url=None, lifespan=lifespan)

app.include_router(accounts.router)
app.include_router(directory.router)
app.include_router(organizations.router)
app.include_router(invitations.router)
app.include_router(join_requests.router)
app.include_router(org_sessions.router)
app.include_router(thresholds.router)
app.include_router(activities.router)
app.include_router(workouts.router)
app.include_router(training_plans.router)
app.include_router(analytics.router)
app.include_router(notifications.router)
app.include_router(live_token.router)
app.include_router(live_rooms.router)
app.include_router(recordings.router)
app.include_router(admin_ops.router)


@app.get("/docs", include_in_schema=False)
async def api_docs():
    return get_swagger_ui_html(
        openapi_url="/api/openapi.json",
        title=f"{app.title} - Swagger UI",
        oauth2_redirect_url="/api/docs/oauth2-redirect",
    )


@app.get("/docs/oauth2-redirect", include_in_schema=False)
async def api_docs_oauth_redirect():
    return get_swagger_ui_oauth2_redirect_html()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8001, log_level="info")
