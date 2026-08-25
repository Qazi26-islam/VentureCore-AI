from google import genai
from google.genai import types

from backend.config import GEMINI_API_KEY

client = genai.Client(api_key=GEMINI_API_KEY)

QUICK_PROMPT = (
    "You are a Market Research Agent doing a QUICK SCAN. Given a business "
    "idea, write ONE short paragraph (3-4 sentences) covering the most "
    "important market demand signal and one key trend. Use your general "
    "knowledge, be direct and fast. No sources needed."
)

STANDARD_PROMPT = (
    "You are a Market Research Agent. Given a business idea or question, "
    "research current market demand, relevant trends, market size, and "
    "target customer profile using web search. Be concise and factual. "
    "Write 3-5 short paragraphs. Do not discuss competitors or finances — "
    "other agents handle those. Focus only on market demand, size, and trends. "
    "At the very end, output one machine-readable line using exactly this format: "
    "MARKET_CHART_DATA: {\"years\":[2026,2027,2028,2029,2030],\"values\":[10,12,14,17,20],\"unit\":\"USD millions\"}. "
    "Replace the example numbers and unit with your evidence-based market projection. Output valid JSON on one line."
)

DEEP_PROMPT = STANDARD_PROMPT + (
    " This is a DEEP RESEARCH request — be more thorough than usual. "
    "Cover market size (TAM/SAM/SOM if data allows), specific growth "
    "rates with numbers where you can find them, detailed customer "
    "segments, and 2-3 notable industry trends with brief explanations. "
    "Aim for 5-7 well-developed paragraphs."
)


def _extract_sources(response) -> list[str]:
    sources = []
    try:
        candidate = response.candidates[0]
        grounding = getattr(candidate, "grounding_metadata", None)
        if grounding and grounding.grounding_chunks:
            for chunk in grounding.grounding_chunks:
                if chunk.web:
                    title = chunk.web.title or chunk.web.uri
                    uri = chunk.web.uri
                    if uri:
                        sources.append(f"- [{title}]({uri})")
    except Exception:
        pass
    seen = set()
    unique = []
    for s in sources:
        if s not in seen:
            seen.add(s)
            unique.append(s)
    return unique[:8]


def run(question: str, depth: str = "standard") -> str:
    if depth == "quick":
        response = client.models.generate_content(
            model="gemini-3.5-flash-lite",
            contents=question,
            config=types.GenerateContentConfig(system_instruction=QUICK_PROMPT),
        )
        return response.text

    system_prompt = DEEP_PROMPT if depth == "deep" else STANDARD_PROMPT

    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=question,
        config=types.GenerateContentConfig(
            system_instruction=system_prompt,
            tools=[types.Tool(google_search=types.GoogleSearch())],
        ),
    )
    text = response.text
    sources = _extract_sources(response)
    if sources:
        text += "\n\n**Sources:**\n" + "\n".join(sources)
    return text
import logging
import json
import io
import threading
import re
from datetime import datetime
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
import markdown as markdown_lib
from xhtml2pdf import pisa
from docx import Document
from docx.shared import Inches, Pt, RGBColor
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment

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
            run_research(body.question, job_id, mode=body.mode, depth=body.depth)
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


from backend.agents import opportunity_finder
from backend.models.schemas import OpportunityRequest, OpportunityResponse, OpportunityItem


@router.post("/research/opportunities", response_model=OpportunityResponse)
def find_opportunities(body: OpportunityRequest):
    items = opportunity_finder.run(body.query)
    return OpportunityResponse(items=[OpportunityItem(**item) for item in items])


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


def _get_research_data(job_id: str, user_id):
    if user_id is not None:
        conn = get_connection()
        row = conn.execute(
            "SELECT question, report, sections FROM research_jobs WHERE id = ? AND user_id = ?",
            (job_id, user_id),
        ).fetchone()
        conn.close()
        if row is None:
            return None, None, {}
        return row["question"], row["report"], json.loads(row["sections"] or "{}")
    job = jobs.get_job(job_id)
    if job is None:
        return None, None, {}
    return job["question"], job["report"], job.get("sections", {})


def _clean_export_text(text: str) -> str:
    text = re.sub(r"^\s*(MARKET|COMPETITOR|FINANCIAL)_CHART_DATA:\s*\{.*\}\s*$", "", text or "", flags=re.MULTILINE)
    text = re.sub(r"\[([^]]+)\]\(([^)]+)\)", r"\1 (\2)", text)
    return text.strip()


def _safe_report_name(question: str) -> str:
    return "".join(c if c.isalnum() or c in " -_" else "" for c in question)[:50].strip() or "report"


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


@router.get("/research/job/{job_id}/docx")
def export_docx(job_id: str, request: Request):
    question, report, _ = _get_research_data(job_id, get_user_id(request))
    if question is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if not report:
        raise HTTPException(status_code=400, detail="This report isn't ready yet.")

    document = Document()
    section = document.sections[0]
    section.top_margin = Inches(0.7)
    section.bottom_margin = Inches(0.7)
    title = document.add_heading("VENTURECORE AI", 0)
    title.runs[0].font.color.rgb = RGBColor(91, 124, 250)
    document.add_paragraph("Business Intelligence Report", style="Subtitle")
    document.add_paragraph(question)
    document.add_paragraph(f"Prepared: {datetime.now().strftime('%d %B %Y')}")
    document.add_page_break()

    for raw_line in _clean_export_text(report).splitlines():
        line = raw_line.strip()
        if not line or line == "---":
            continue
        if line.startswith("**") and line.endswith("**"):
            document.add_heading(line.strip("*").rstrip(":"), level=1)
        elif line.startswith("### "):
            document.add_heading(line[4:], level=2)
        elif line.startswith("## "):
            document.add_heading(line[3:], level=1)
        elif line.startswith(("- ", "* ")):
            document.add_paragraph(line[2:], style="List Bullet")
        elif line.startswith("|"):
            paragraph = document.add_paragraph(line.strip("|").replace("|", "  |  "))
            paragraph.style = document.styles["No Spacing"]
        else:
            document.add_paragraph(line.replace("**", ""))

    styles = document.styles
    styles["Normal"].font.name = "Arial"
    styles["Normal"].font.size = Pt(10.5)
    buffer = io.BytesIO()
    document.save(buffer)
    return Response(
        content=buffer.getvalue(),
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": f'attachment; filename="{_safe_report_name(question)}.docx"'},
    )


@router.get("/research/job/{job_id}/xlsx")
def export_xlsx(job_id: str, request: Request):
    question, report, sections = _get_research_data(job_id, get_user_id(request))
    if question is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if not report:
        raise HTTPException(status_code=400, detail="This report isn't ready yet.")

    workbook = Workbook()
    workbook.remove(workbook.active)
    sheet_data = [
        ("Executive Report", report),
        ("Market Research", sections.get("market_research", "")),
        ("Competitors", sections.get("competitor_analysis", "")),
        ("Financial Analysis", sections.get("financial_analysis", "")),
    ]
    header_fill = PatternFill("solid", fgColor="5B7CFA")
    for sheet_name, content in sheet_data:
        sheet = workbook.create_sheet(sheet_name)
        sheet.append(["VENTURECORE AI", sheet_name])
        sheet.append(["Business", question])
        sheet.append(["Prepared", datetime.now().strftime("%d %B %Y")])
        sheet.append([])
        sheet.append(["Report content"])
        for line in _clean_export_text(content).splitlines():
            if line.strip():
                sheet.append([line.replace("**", "")])
        for cell in sheet[1]:
            cell.fill = header_fill
            cell.font = Font(color="FFFFFF", bold=True)
        sheet.column_dimensions["A"].width = 110
        sheet.column_dimensions["B"].width = 70
        for row in sheet.iter_rows():
            for cell in row:
                cell.alignment = Alignment(vertical="top", wrap_text=True)

    sources_sheet = workbook.create_sheet("Sources")
    sources_sheet.append(["Source", "URL", "Quality"])
    source_number = 1
    combined = "\n".join([report] + list(sections.values()))
    seen_urls = set()
    for label, url in re.findall(r"\[([^]]+)\]\((https?://[^)]+)\)", combined):
        if url in seen_urls:
            continue
        seen_urls.add(url)
        domain = url.lower()
        quality = "Official / Government" if any(token in domain for token in [".gov", ".edu", "worldbank.org", "who.int", "oecd.org"]) else "Industry / Company source"
        sources_sheet.append([f"[{source_number}] {label}", url, quality])
        source_number += 1
    for cell in sources_sheet[1]:
        cell.fill = header_fill
        cell.font = Font(color="FFFFFF", bold=True)
    sources_sheet.column_dimensions["A"].width = 50
    sources_sheet.column_dimensions["B"].width = 75
    sources_sheet.column_dimensions["C"].width = 25
    sources_sheet.freeze_panes = "A2"

    buffer = io.BytesIO()
    workbook.save(buffer)
    return Response(
        content=buffer.getvalue(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{_safe_report_name(question)}.xlsx"'},
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
import logging
import json
import io
import threading
import re
from datetime import datetime
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
import markdown as markdown_lib
from xhtml2pdf import pisa
from docx import Document
from docx.shared import Inches, Pt, RGBColor
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment

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
            run_research(body.question, job_id, mode=body.mode, depth=body.depth)
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


from backend.agents import opportunity_finder
from backend.models.schemas import OpportunityRequest, OpportunityResponse, OpportunityItem


@router.post("/research/opportunities", response_model=OpportunityResponse)
def find_opportunities(body: OpportunityRequest):
    items = opportunity_finder.run(body.query)
    return OpportunityResponse(items=[OpportunityItem(**item) for item in items])


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


def _get_research_data(job_id: str, user_id):
    if user_id is not None:
        conn = get_connection()
        row = conn.execute(
            "SELECT question, report, sections FROM research_jobs WHERE id = ? AND user_id = ?",
            (job_id, user_id),
        ).fetchone()
        conn.close()
        if row is None:
            return None, None, {}
        return row["question"], row["report"], json.loads(row["sections"] or "{}")
    job = jobs.get_job(job_id)
    if job is None:
        return None, None, {}
    return job["question"], job["report"], job.get("sections", {})


def _clean_export_text(text: str) -> str:
    text = re.sub(
        r"^\s*(MARKET|COMPETITOR|FINANCIAL)_CHART_DATA:[^\n]*(?:\n(?!\s*(?:\*\*Sources|Sources:|---|\*\*[A-Za-z]))[^\n]*)*",
        "",
        text or "",
        flags=re.MULTILINE | re.IGNORECASE,
    )
    text = re.sub(r"\[([^]]+)\]\(([^)]+)\)", r"\1 (\2)", text)
    return text.strip()


def _safe_report_name(question: str) -> str:
    return "".join(c if c.isalnum() or c in " -_" else "" for c in question)[:50].strip() or "report"


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


@router.get("/research/job/{job_id}/docx")
def export_docx(job_id: str, request: Request):
    question, report, _ = _get_research_data(job_id, get_user_id(request))
    if question is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if not report:
        raise HTTPException(status_code=400, detail="This report isn't ready yet.")

    document = Document()
    section = document.sections[0]
    section.top_margin = Inches(0.7)
    section.bottom_margin = Inches(0.7)
    title = document.add_heading("VENTURECORE AI", 0)
    title.runs[0].font.color.rgb = RGBColor(91, 124, 250)
    document.add_paragraph("Business Intelligence Report", style="Subtitle")
    document.add_paragraph(question)
    document.add_paragraph(f"Prepared: {datetime.now().strftime('%d %B %Y')}")
    document.add_page_break()

    for raw_line in _clean_export_text(report).splitlines():
        line = raw_line.strip()
        if not line or line == "---":
            continue
        if line.startswith("**") and line.endswith("**"):
            document.add_heading(line.strip("*").rstrip(":"), level=1)
        elif line.startswith("### "):
            document.add_heading(line[4:], level=2)
        elif line.startswith("## "):
            document.add_heading(line[3:], level=1)
        elif line.startswith(("- ", "* ")):
            document.add_paragraph(line[2:], style="List Bullet")
        elif line.startswith("|"):
            paragraph = document.add_paragraph(line.strip("|").replace("|", "  |  "))
            paragraph.style = document.styles["No Spacing"]
        else:
            document.add_paragraph(line.replace("**", ""))

    styles = document.styles
    styles["Normal"].font.name = "Arial"
    styles["Normal"].font.size = Pt(10.5)
    buffer = io.BytesIO()
    document.save(buffer)
    return Response(
        content=buffer.getvalue(),
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": f'attachment; filename="{_safe_report_name(question)}.docx"'},
    )


@router.get("/research/job/{job_id}/xlsx")
def export_xlsx(job_id: str, request: Request):
    question, report, sections = _get_research_data(job_id, get_user_id(request))
    if question is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if not report:
        raise HTTPException(status_code=400, detail="This report isn't ready yet.")

    workbook = Workbook()
    workbook.remove(workbook.active)
    sheet_data = [
        ("Executive Report", report),
        ("Market Research", sections.get("market_research", "")),
        ("Competitors", sections.get("competitor_analysis", "")),
        ("Financial Analysis", sections.get("financial_analysis", "")),
    ]
    header_fill = PatternFill("solid", fgColor="5B7CFA")
    for sheet_name, content in sheet_data:
        sheet = workbook.create_sheet(sheet_name)
        sheet.append(["VENTURECORE AI", sheet_name])
        sheet.append(["Business", question])
        sheet.append(["Prepared", datetime.now().strftime("%d %B %Y")])
        sheet.append([])
        sheet.append(["Report content"])
        for line in _clean_export_text(content).splitlines():
            if line.strip():
                sheet.append([line.replace("**", "")])
        for cell in sheet[1]:
            cell.fill = header_fill
            cell.font = Font(color="FFFFFF", bold=True)
        sheet.column_dimensions["A"].width = 110
        sheet.column_dimensions["B"].width = 70
        for row in sheet.iter_rows():
            for cell in row:
                cell.alignment = Alignment(vertical="top", wrap_text=True)

    sources_sheet = workbook.create_sheet("Sources")
    sources_sheet.append(["Source", "URL", "Quality"])
    source_number = 1
    combined = "\n".join([report] + list(sections.values()))
    seen_urls = set()
    for label, url in re.findall(r"\[([^]]+)\]\((https?://[^)]+)\)", combined):
        if url in seen_urls:
            continue
        seen_urls.add(url)
        domain = url.lower()
        quality = "Official / Government" if any(token in domain for token in [".gov", ".edu", "worldbank.org", "who.int", "oecd.org"]) else "Industry / Company source"
        sources_sheet.append([f"[{source_number}] {label}", url, quality])
        source_number += 1
    for cell in sources_sheet[1]:
        cell.fill = header_fill
        cell.font = Font(color="FFFFFF", bold=True)
    sources_sheet.column_dimensions["A"].width = 50
    sources_sheet.column_dimensions["B"].width = 75
    sources_sheet.column_dimensions["C"].width = 25
    sources_sheet.freeze_panes = "A2"

    buffer = io.BytesIO()
    workbook.save(buffer)
    return Response(
        content=buffer.getvalue(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{_safe_report_name(question)}.xlsx"'},
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
