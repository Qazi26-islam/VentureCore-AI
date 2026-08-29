from __future__ import annotations

import secrets
from typing import Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, ConfigDict, Field

from backend import config
from backend.db import DEFAULT_ORGANIZATION_ID, get_connection
from backend.demo_briefing import stored_demo_briefing
from backend.workers import organization_from_unsubscribe_token, run_due_jobs


router = APIRouter(tags=["scheduled-workers"])


class DeliveryPreferenceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool
    email: str = Field(pattern=r"^[^\s@]+@[^\s@]+\.[^\s@]+$", max_length=254)
    quiet_start: str = Field(pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    quiet_end: str = Field(pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    timezone: str = Field(min_length=1, max_length=100)
    briefing_hour: int = Field(ge=0, le=23)


def _identity(request: Request) -> tuple[int, int]:
    if request.session.get("demo_mode"):
        raise HTTPException(status_code=403, detail="Email delivery is unavailable in the sample workspace.")
    user_id = request.session.get("user_id")
    if user_id is None:
        raise HTTPException(status_code=401, detail="Sign in to manage briefing delivery.")
    return int(user_id), DEFAULT_ORGANIZATION_ID


@router.get("/briefings/demo")
def get_demo_briefing(request: Request):
    if not request.session.get("demo_mode"):
        raise HTTPException(status_code=404, detail="The sample briefing is available in demo mode.")
    briefing = stored_demo_briefing()
    if briefing is None:
        raise HTTPException(status_code=503, detail="The sample briefing has not been prepared yet.")
    return briefing


@router.get("/briefings/preferences")
def get_preferences(request: Request):
    user_id, organization_id = _identity(request)
    conn = get_connection()
    row = conn.execute(
        """SELECT enabled, email, quiet_start, quiet_end, timezone, briefing_hour
             FROM delivery_preferences
            WHERE user_id = ? AND organization_id = ?""",
        (user_id, organization_id),
    ).fetchone()
    if row is None:
        user = conn.execute("SELECT email FROM users WHERE id = ?", (user_id,)).fetchone()
        result = {
            "enabled": False,
            "email": user["email"],
            "quiet_start": "22:00",
            "quiet_end": "07:00",
            "timezone": "Asia/Kuala_Lumpur",
            "briefing_hour": 8,
        }
    else:
        result = dict(row)
        result["enabled"] = bool(result["enabled"])
    conn.close()
    return result


@router.put("/briefings/preferences")
def save_preferences(request: Request, body: DeliveryPreferenceRequest):
    user_id, organization_id = _identity(request)
    try:
        ZoneInfo(body.timezone)
    except ZoneInfoNotFoundError as exc:
        raise HTTPException(status_code=422, detail="Choose a valid IANA timezone.") from exc
    conn = get_connection()
    conn.execute(
        """INSERT INTO delivery_preferences
           (organization_id, user_id, email, enabled, quiet_start, quiet_end,
            timezone, briefing_hour, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
           ON CONFLICT(organization_id) DO UPDATE SET
             user_id = excluded.user_id, email = excluded.email, enabled = excluded.enabled,
             quiet_start = excluded.quiet_start, quiet_end = excluded.quiet_end,
             timezone = excluded.timezone, briefing_hour = excluded.briefing_hour,
             updated_at = CURRENT_TIMESTAMP""",
        (
            organization_id, user_id, str(body.email), int(body.enabled), body.quiet_start,
            body.quiet_end, body.timezone, body.briefing_hour,
        ),
    )
    conn.commit()
    conn.close()
    return {"saved": True, **body.model_dump(mode="json")}


@router.get("/briefings/unsubscribe", response_class=HTMLResponse)
def unsubscribe(token: str):
    try:
        organization_id = organization_from_unsubscribe_token(token)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    conn = get_connection()
    conn.execute(
        """UPDATE delivery_preferences SET enabled = 0, updated_at = CURRENT_TIMESTAMP
            WHERE organization_id = ?""",
        (organization_id,),
    )
    conn.commit()
    conn.close()
    return HTMLResponse(
        "<!doctype html><title>Unsubscribed</title><h1>Email briefings paused</h1>"
        "<p>You will no longer receive VentureCore briefings. You can enable them again in your workspace.</p>"
    )


@router.post("/internal/jobs/run")
def run_scheduled_jobs(authorization: Optional[str] = Header(default=None)):
    if not config.JOB_RUNNER_SECRET:
        raise HTTPException(status_code=503, detail="Scheduled worker authentication is not configured.")
    expected = f"Bearer {config.JOB_RUNNER_SECRET}"
    if authorization is None or not secrets.compare_digest(authorization, expected):
        raise HTTPException(status_code=401, detail="Invalid scheduled worker credential.")
    return run_due_jobs()
