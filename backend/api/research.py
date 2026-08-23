import logging
import json
import io
import threading
from datetime import datetime
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
import markdown as markdown_lib
from xhtml2pdf import pisa

from backend.models.schemas import (
    ResearchRequest, StartResponse, StatusResponse,
    HistoryItem, JobDetailResponse, MessageItem,
    FollowUpRequest, FollowUpResponse,
    RenameRequest, FavoriteRequest,
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
    job_id = jobs.create_job(body.question)

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
def get_history(request: Request, q: str = "", favorites_only: bool = False):
    user_id = require_login(request)
    conn = get_connection()
    cursor = conn.cursor()

    query = "SELECT id, question, title, favorite, created_at FROM research_jobs WHERE user_id = ?"
    params = [user_id]

    if q:
        query += " AND (question LIKE ? OR title LIKE ?)"
        like = f"%{q}%"
        params.extend([like, like])

    if favorites_only:
        query += " AND favorite = 1"

    query += " ORDER BY favorite DESC, created_at DESC"

    cursor.execute(query, params)
    rows = cursor.fetchall()
    conn.close()

    return [
        HistoryItem(
            id=r["id"],
            question=r["question"],
            title=r["title"],
            favorite=bool(r["favorite"]),
            created_at=r["created_at"],
        )
        for r in rows
    ]


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
        title=row["title"],
        favorite=bool(row["favorite"]),
        report=row["report"],
        sections=sections,
        messages=messages,
    )


@router.put("/research/job/{job_id}/rename")
def rename_job(job_id: str, body: RenameRequest, request: Request):
    user_id = require_login(request)
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM research_jobs WHERE id = ? AND user_id = ?", (job_id, user_id))
    if cursor.fetchone() is None:
        conn.close()
        raise HTTPException(status_code=404, detail="Job not found")
    cursor.execute("UPDATE research_jobs SET title = ? WHERE id = ?", (body.title, job_id))
    conn.commit()
    conn.close()
    return {"status": "renamed"}


@router.put("/research/job/{job_id}/favorite")
def favorite_job(job_id: str, body: FavoriteRequest, request: Request):
    user_id = require_login(request)
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM research_jobs WHERE id = ? AND user_id = ?", (job_id, user_id))
    if cursor.fetchone() is None:
        conn.close()
        raise HTTPException(status_code=404, detail="Job not found")
    cursor.execute("UPDATE research_jobs SET favorite = ? WHERE id = ?", (1 if body.favorite else 0, job_id))
    conn.commit()
    conn.close()
    return {"status": "updated"}


@router.delete("/research/job/{job_id}")
def delete_job(job_id: str, request: Request):
    user_id = require_login(request)
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM research_jobs WHERE id = ? AND user_id = ?", (job_id, user_id))
    if cursor.fetchone() is None:
        conn.close()
        raise HTTPException(status_code=404, detail="Job not found")
    cursor.execute("DELETE FROM follow_up_messages WHERE job_id = ?", (job_id,))
    cursor.execute("DELETE FROM research_jobs WHERE id = ?", (job_id,))
    conn.commit()
    conn.close()
    return {"status": "deleted"}


def _get_report_and_question(job_id: str, user_id):
    if user_id is not None:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM research_jobs WHERE id = ? AND user_id = ?", (job_id, user_id))
        row = cursor.fetchone()
        conn.close()
        if row is None:
            return None, None
        return row["question"], row["report"]
    else:
        job = jobs.get_job(job_id)
        if job is None:
            return None, None
        return job["question"], job["report"]


@router.get("/research/job/{job_id}/pdf")
def export_pdf(job_id: str, request: Request):
    user_id = get_user_id(request)
    question, report = _get_report_and_question(job_id, user_id)

    if question is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if not report:
        raise HTTPException(status_code=400, detail="This report isn't ready yet.")

    body_html = markdown_lib.markdown(report, extensions=["tables"])
    prepared_date = datetime.now().strftime("%d %B %Y")

    html_content = f"""
    <html>
    <head>
    <style>
        body {{ font-family: Helvetica, Arial, sans-serif; font-size: 11px; color: #1a1a1a; }}
        h1 {{ color: #5b7cfa; font-size: 20px; margin-bottom: 2px; }}
        h2 {{ color: #5b7cfa; font-size: 15px; margin-top: 18px; margin-bottom: 6px; }}
        h3 {{ color: #5b7cfa; font-size: 13px; margin-top: 14px; }}
        p {{ line-height: 1.5; margin: 6px 0; }}
        table {{ width: 100%; border-collapse: collapse; margin: 10px 0; }}
        th, td {{ border: 1px solid #cccccc; padding: 6px 8px; text-align: left; font-size: 10px; }}
        th {{ background: #f0f0f0; font-weight: bold; }}
        hr {{ border: none; border-top: 1px solid #cccccc; margin: 16px 0; }}
        .meta {{ color: #666666; font-size: 10px; margin-bottom: 20px; }}
        a {{ color: #5b7cfa; }}
    </style>
    </head>
    <body>
        <h1>VentureCore AI</h1>
        <div class="meta">
            Business Intelligence Report<br/>
            {question}<br/>
            Prepared: {prepared_date}
        </div>
        {body_html}
    </body>
    </html>
    """

    buffer = io.BytesIO()
    pisa.CreatePDF(html_content, dest=buffer)
    pdf_bytes = buffer.getvalue()
    buffer.close()

    safe_name = "".join(c if c.isalnum() or c in " -_" else "" for c in question)[:50].strip() or "report"

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{safe_name}.pdf"'},
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
        question = job["question"]
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
