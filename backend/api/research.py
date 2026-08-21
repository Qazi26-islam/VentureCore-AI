import logging
import json
import threading
from fastapi import APIRouter, HTTPException, Request
from backend.models.schemas import (
    ResearchRequest, StartResponse, StatusResponse,
    HistoryItem, JobDetailResponse, MessageItem,
    FollowUpRequest, FollowUpResponse,
)
from backend.agents.coordinator import run_research
from backend.agents import followup
from backend import jobs
from backend.db import get_connection

router = APIRouter()
logger = logging.getLogger("research_api")


def get_user_id(request: Request):
    return request.session.get("user_id")


def require_login(request: Request) -> int:
    user_id = get_user_id(request)
    if user_id is None:
        raise HTTPException(status_code=401, detail="You must be logged in.")
    return user_id


@router.post("/research/start", response_model=StartResponse)
def start_research(request: Request, body: ResearchRequest) -> StartResponse:
    user_id = get_user_id(request)
    job_id = jobs.create_job()

    if user_id is not None:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO research_jobs (id, user_id, question, report, sections) VALUES (?, ?, ?, ?, ?)",
            (job_id, user_id, body.question, None, "{}"),
        )
        conn.commit()
        conn.close()

    def _background_run():
        try:
            run_research(body.question, job_id)
            if user_id is not None:
                job = jobs.get_job(job_id)
                conn2 = get_connection()
                cursor2 = conn2.cursor()
                cursor2.execute(
                    "UPDATE research_jobs SET report = ?, sections = ? WHERE id = ?",
                    (job["report"], json.dumps(job["sections"]), job_id),
                )
                conn2.commit()
                conn2.close()
        except Exception as e:
            logger.exception("Research pipeline crashed")
            jobs.fail_job(job_id, str(e))

    thread = threading.Thread(target=_background_run, daemon=True)
    thread.start()

    return StartResponse(job_id=job_id)


@router.get("/research/status/{job_id}", response_model=StatusResponse)
def get_status(job_id: str) -> StatusResponse:
    job = jobs.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")

    return StatusResponse(
        status=job["status"],
        stage=job["stage"],
        sections=job["sections"],
        report=job["report"],
        error=job["error"],
    )


@router.get("/research/history", response_model=list[HistoryItem])
def get_history(request: Request):
    user_id = require_login(request)
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id, question, created_at FROM research_jobs WHERE user_id = ? ORDER BY created_at DESC",
        (user_id,),
    )
    rows = cursor.fetchall()
    conn.close()
    return [HistoryItem(id=r["id"], question=r["question"], created_at=r["created_at"]) for r in rows]


@router.get("/research/job/{job_id}", response_model=JobDetailResponse)
def get_job_detail(job_id: str, request: Request):
    user_id = require_login(request)
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM research_jobs WHERE id = ? AND user_id = ?", (job_id, user_id))
    row = cursor.fetchone()
    if row is None:
        conn.close()
        raise HTTPException(status_code=404, detail="Job not found")

    cursor.execute(
        "SELECT role, content FROM follow_up_messages WHERE job_id = ? ORDER BY created_at ASC",
        (job_id,),
    )
    messages = [MessageItem(role=m["role"], content=m["content"]) for m in cursor.fetchall()]
    conn.close()

    sections = json.loads(row["sections"]) if row["sections"] else {}

    return JobDetailResponse(
        id=row["id"],
        question=row["question"],
        report=row["report"],
        sections=sections,
        messages=messages,
    )


@router.post("/research/job/{job_id}/message", response_model=FollowUpResponse)
def send_follow_up(job_id: str, body: FollowUpRequest, request: Request):
    user_id = get_user_id(request)

    if user_id is not None:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM research_jobs WHERE id = ? AND user_id = ?", (job_id, user_id))
        row = cursor.fetchone()
        if row is None:
            conn.close()
            raise HTTPException(status_code=404, detail="Job not found")

        cursor.execute(
            "SELECT role, content FROM follow_up_messages WHERE job_id = ? ORDER BY created_at ASC",
            (job_id,),
        )
        history = [{"role": m["role"], "content": m["content"]} for m in cursor.fetchall()]
        question = row["question"]
        report = row["report"] or ""
    else:
        job = jobs.get_job(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Job not found")
        history = job["messages"]
        question = ""
        report = job["report"] or ""

    try:
        reply = followup.run(
            question=question,
            report=report,
            history=history,
            new_message=body.message,
        )
    except Exception as e:
        if user_id is not None:
            conn.close()
        logger.exception("Follow-up failed")
        raise HTTPException(status_code=502, detail="Follow-up failed. Please try again.") from e

    if user_id is not None:
        cursor.execute(
            "INSERT INTO follow_up_messages (job_id, role, content) VALUES (?, ?, ?)",
            (job_id, "user", body.message),
        )
        cursor.execute(
            "INSERT INTO follow_up_messages (job_id, role, content) VALUES (?, ?, ?)",
            (job_id, "assistant", reply),
        )
        conn.commit()
        conn.close()
    else:
        jobs.add_message(job_id, "user", body.message)
        jobs.add_message(job_id, "assistant", reply)

    return FollowUpResponse(reply=reply)
