from __future__ import annotations

import hashlib
import html
import json
import logging
import smtplib
import time
import uuid
from datetime import date, datetime, timedelta, timezone
from email.message import EmailMessage
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from itsdangerous import BadSignature, URLSafeSerializer

from backend import config
from backend.db import DEMO_ORGANIZATION_ID, get_connection
from backend.money import minor_to_major
from backend.observability import active_trace, record_tool_call, run_traced_agent
from backend.tools import ToolContext, invoke_tool


logger = logging.getLogger("scheduled_workers")


class TransientJobError(RuntimeError):
    pass


class JobTimeout(RuntimeError):
    pass


def briefing_tool_arguments(as_of: date) -> dict[str, Any]:
    return {
        "as_of": as_of.isoformat(),
        "velocity_days": 30,
        "stockout_days": config.ALERT_STOCKOUT_DAYS,
        "expense_period_days": 30,
        "baseline_periods": 3,
        "receivable_min_minor": config.ALERT_RECEIVABLE_MIN_MINOR,
        "expense_increase_percent": config.ALERT_EXPENSE_INCREASE_PERCENT,
        "expense_increase_min_minor": config.ALERT_EXPENSE_INCREASE_MIN_MINOR,
        "cash_drop_percent": config.ALERT_CASH_DROP_PERCENT,
        "cash_drop_min_minor": config.ALERT_CASH_DROP_MIN_MINOR,
    }


def _format_money(value_minor: int, currency: str) -> str:
    return f"{currency} {minor_to_major(value_minor, currency):,.2f}"


def _invoke_metrics(organization_id: int, user_id: int, as_of: date) -> dict[str, Any]:
    arguments = briefing_tool_arguments(as_of)
    started = time.perf_counter()
    result = invoke_tool(
        "get_daily_briefing_metrics",
        ToolContext(organization_id=organization_id, user_id=user_id),
        arguments,
    )
    record_tool_call(
        "get_daily_briefing_metrics",
        arguments,
        result,
        int((time.perf_counter() - started) * 1000),
    )
    if not result.ok:
        raise RuntimeError(result.error.message if result.error else "Metrics tool failed.")
    return result.data or {}


def build_briefing(metrics: dict[str, Any], period: str) -> tuple[str, str, list[str]]:
    currency = metrics["currency"]
    cash = metrics["cash"]
    receivables = metrics["overdue_receivables"]
    stockouts = metrics["stockout_products"]
    anomalies = metrics["expense_anomalies"]
    actions: list[str] = []
    actions.extend(
        f"Follow up with {item['customer_name']} about {_format_money(item['amount_minor'], item['currency'])}."
        for item in receivables[:3]
    )
    actions.extend(
        f"Review replenishment for {item['name']} ({item['days_of_cover']} days of cover)."
        for item in stockouts[:3]
    )
    actions.extend(
        f"Investigate {item['category']} spending, up {item['increase_percent']}% against its trailing baseline."
        for item in anomalies[:3]
    )
    if not actions:
        actions.append("No material exception needs immediate attention; continue monitoring operations.")

    trend = cash["change_percent"]
    trend_text = "not comparable" if trend is None else f"{trend:+d}% versus the previous period"
    sections = [
        f"<h1>VentureCore daily briefing — {html.escape(period)}</h1>",
        "<h2>Cash</h2>",
        f"<p>Recorded cash position: <strong>{_format_money(cash['recorded_cash_balance_minor'], currency)}</strong>. "
        f"Current net cash flow: {_format_money(cash['current_net_cash_flow_minor'], currency)}, {trend_text}.</p>",
        "<h2>Receivables needing attention</h2>",
        "<ul>" + "".join(
            f"<li>{html.escape(item['customer_name'])}: {_format_money(item['amount_minor'], item['currency'])}, due {html.escape(item['due_date'])}</li>"
            for item in receivables
        ) + "</ul>" if receivables else "<p>None overdue.</p>",
        "<h2>Products approaching a stockout</h2>",
        "<ul>" + "".join(
            f"<li>{html.escape(item['name'])}: {item['current_stock']:g} units, {item['days_of_cover']} days of cover</li>"
            for item in stockouts
        ) + "</ul>" if stockouts else "<p>No material stockout risk.</p>",
        "<h2>Expense anomalies</h2>",
        "<ul>" + "".join(
            f"<li>{html.escape(item['category'])}: {_format_money(item['current_amount_minor'], currency)}, up {item['increase_percent']}% from {_format_money(item['baseline_average_minor'], currency)}</li>"
            for item in anomalies
        ) + "</ul>" if anomalies else "<p>No material expense anomaly.</p>",
        "<h2>Priorities</h2><ol>" + "".join(f"<li>{html.escape(action)}</li>" for action in actions) + "</ol>",
    ]
    return f"VentureCore daily briefing — {period}", "".join(sections), actions


def get_or_create_briefing(
    organization_id: int,
    user_id: int,
    period: str,
    as_of: date,
) -> dict[str, Any]:
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM briefing_cache WHERE organization_id = ? AND period = ?",
        (organization_id, period),
    ).fetchone()
    conn.close()
    if row:
        return dict(row)

    metrics = _invoke_metrics(organization_id, user_id, as_of)
    subject, html_body, actions = build_briefing(metrics, period)
    payload = json.dumps({"metrics": metrics, "actions": actions}, default=str, sort_keys=True)
    conn = get_connection()
    try:
        conn.execute(
            """INSERT OR IGNORE INTO briefing_cache
               (organization_id, period, subject, content_json, html_body,
                source, external_id, last_synced_at)
               VALUES (?, ?, ?, ?, ?, 'computed', ?, CURRENT_TIMESTAMP)""",
            (organization_id, period, subject, payload, html_body, f"daily:{period}"),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM briefing_cache WHERE organization_id = ? AND period = ?",
            (organization_id, period),
        ).fetchone()
        return dict(row)
    finally:
        conn.close()


def _serializer() -> URLSafeSerializer:
    return URLSafeSerializer(config.SESSION_SECRET, salt="briefing-unsubscribe")


def unsubscribe_token(organization_id: int) -> str:
    return _serializer().dumps({"organization_id": organization_id})


def organization_from_unsubscribe_token(token: str) -> int:
    try:
        payload = _serializer().loads(token)
        return int(payload["organization_id"])
    except (BadSignature, KeyError, TypeError, ValueError) as exc:
        raise ValueError("Invalid unsubscribe link.") from exc


def send_email(recipient: str, subject: str, html_body: str, unsubscribe_url: str) -> None:
    if not config.SMTP_HOST or not config.SMTP_FROM_EMAIL:
        raise TransientJobError("Email delivery is not configured.")
    message = EmailMessage()
    message["From"] = config.SMTP_FROM_EMAIL
    message["To"] = recipient
    message["Subject"] = subject
    message["List-Unsubscribe"] = f"<{unsubscribe_url}>"
    message.set_content("This briefing is best viewed as HTML. Unsubscribe: " + unsubscribe_url)
    message.add_alternative(
        html_body
        + f'<hr><p><a href="{html.escape(unsubscribe_url, quote=True)}">Unsubscribe from VentureCore briefings</a></p>',
        subtype="html",
    )
    try:
        with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=30) as smtp:
            if config.SMTP_USE_TLS:
                smtp.starttls()
            if config.SMTP_USERNAME:
                smtp.login(config.SMTP_USERNAME, config.SMTP_PASSWORD)
            smtp.send_message(message)
    except smtplib.SMTPResponseException as exc:
        if 400 <= exc.smtp_code < 500:
            raise TransientJobError("Email provider temporarily rejected delivery.") from exc
        raise RuntimeError("Email provider rejected delivery.") from exc
    except (OSError, smtplib.SMTPException) as exc:
        raise TransientJobError("Email delivery is temporarily unavailable.") from exc


def _unsubscribe_url(organization_id: int) -> str:
    return f"{config.APP_URL}/briefings/unsubscribe?token={unsubscribe_token(organization_id)}"


def _claim_job(organization_id: int, job_type: str, period: str) -> dict[str, Any] | None:
    conn = get_connection()
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            """SELECT * FROM scheduled_job_runs
                WHERE organization_id = ? AND job_type = ? AND period = ?""",
            (organization_id, job_type, period),
        ).fetchone()
        if row:
            stale_before = (
                datetime.now(timezone.utc) - timedelta(seconds=int(row["timeout_seconds"]))
            ).strftime("%Y-%m-%d %H:%M:%S")
            running_is_fresh = row["status"] == "running" and row["started_at"] > stale_before
            if (
                row["status"] == "success"
                or running_is_fresh
                or int(row["attempts"]) >= config.JOB_MAX_ATTEMPTS
                or (row["next_retry_at"] and row["next_retry_at"] > now)
            ):
                conn.rollback()
                return None
        job_id = row["id"] if row else str(uuid.uuid4())
        if row:
            conn.execute(
                """UPDATE scheduled_job_runs SET status = 'running', attempts = attempts + 1,
                          started_at = CURRENT_TIMESTAMP, finished_at = NULL, failure_mode = NULL
                    WHERE id = ? AND organization_id = ?""",
                (job_id, organization_id),
            )
        else:
            conn.execute(
                """INSERT INTO scheduled_job_runs
                   (id, organization_id, job_type, period, status, attempts, timeout_seconds)
                   VALUES (?, ?, ?, ?, 'running', 1, ?)""",
                (job_id, organization_id, job_type, period, config.JOB_TIMEOUT_SECONDS),
            )
        conn.commit()
        claimed = conn.execute(
            "SELECT * FROM scheduled_job_runs WHERE id = ? AND organization_id = ?",
            (job_id, organization_id),
        ).fetchone()
        return dict(claimed)
    finally:
        conn.close()


def _finish_job(job: dict[str, Any], outcome: dict[str, Any]) -> None:
    conn = get_connection()
    conn.execute(
        """UPDATE scheduled_job_runs SET status = 'success', outcome_json = ?,
                  next_retry_at = NULL, finished_at = CURRENT_TIMESTAMP
            WHERE id = ? AND organization_id = ?""",
        (json.dumps(outcome, default=str), job["id"], job["organization_id"]),
    )
    conn.commit()
    conn.close()


def _fail_job(job: dict[str, Any], exc: BaseException) -> None:
    attempts = int(job["attempts"])
    retryable = isinstance(exc, (TransientJobError, JobTimeout))
    can_retry = retryable and attempts < config.JOB_MAX_ATTEMPTS
    delay = min(3600, 60 * (2 ** max(0, attempts - 1)))
    next_retry = (
        (datetime.now(timezone.utc) + timedelta(seconds=delay)).strftime("%Y-%m-%d %H:%M:%S")
        if can_retry
        else None
    )
    status = "retry" if can_retry else ("timed_out" if isinstance(exc, JobTimeout) else "failed")
    conn = get_connection()
    conn.execute(
        """UPDATE scheduled_job_runs SET status = ?, next_retry_at = ?, failure_mode = ?,
                  outcome_json = ?, finished_at = CURRENT_TIMESTAMP
            WHERE id = ? AND organization_id = ?""",
        (status, next_retry, type(exc).__name__, json.dumps({"error": str(exc)}), job["id"], job["organization_id"]),
    )
    conn.commit()
    conn.close()


def _check_deadline(deadline: float) -> None:
    if time.monotonic() > deadline:
        raise JobTimeout("Scheduled job exceeded its timeout.")


def _deliver_once(
    organization_id: int,
    kind: str,
    key: str,
    recipient: str,
    subject: str,
    html_body: str,
) -> bool:
    conn = get_connection()
    try:
        existing = conn.execute(
            """SELECT status FROM notification_deliveries
                WHERE organization_id = ? AND kind = ? AND idempotency_key = ?""",
            (organization_id, kind, key),
        ).fetchone()
        if existing and existing["status"] == "sent":
            return False
        conn.execute(
            """INSERT INTO notification_deliveries
               (organization_id, kind, idempotency_key, recipient, status, attempts)
               VALUES (?, ?, ?, ?, 'sending', 1)
               ON CONFLICT(organization_id, kind, idempotency_key) DO UPDATE SET
                 recipient = excluded.recipient, status = 'sending', attempts = attempts + 1,
                 last_error = NULL""",
            (organization_id, kind, key, recipient),
        )
        conn.commit()
    finally:
        conn.close()
    try:
        send_email(recipient, subject, html_body, _unsubscribe_url(organization_id))
    except Exception as exc:
        conn = get_connection()
        conn.execute(
            """UPDATE notification_deliveries SET status = 'failed', last_error = ?
                WHERE organization_id = ? AND kind = ? AND idempotency_key = ?""",
            (str(exc), organization_id, kind, key),
        )
        conn.commit()
        conn.close()
        raise
    conn = get_connection()
    conn.execute(
        """UPDATE notification_deliveries SET status = 'sent', sent_at = CURRENT_TIMESTAMP
            WHERE organization_id = ? AND kind = ? AND idempotency_key = ?""",
        (organization_id, kind, key),
    )
    conn.commit()
    conn.close()
    return True


def run_daily_briefing(
    organization_id: int,
    user_id: int,
    recipient: str,
    period: str,
    as_of: date,
) -> dict[str, Any]:
    job = _claim_job(organization_id, "daily_briefing", period)
    if job is None:
        return {"status": "already_processed"}
    deadline = time.monotonic() + int(job["timeout_seconds"])

    def execute() -> dict[str, Any]:
        trace = active_trace()
        if trace:
            conn = get_connection()
            conn.execute(
                """UPDATE scheduled_job_runs SET trace_run_id = ?
                    WHERE id = ? AND organization_id = ?""",
                (trace.id, job["id"], organization_id),
            )
            conn.commit()
            conn.close()
        briefing = get_or_create_briefing(organization_id, user_id, period, as_of)
        _check_deadline(deadline)
        sent = _deliver_once(
            organization_id, "briefing", period, recipient,
            briefing["subject"], briefing["html_body"],
        )
        _check_deadline(deadline)
        return {"status": "sent" if sent else "already_sent", "briefing_id": briefing["id"]}

    try:
        outcome = run_traced_agent(
            "Daily Briefing Worker", organization_id, f"daily briefing {period}", execute, job["id"]
        )
        _finish_job(job, outcome)
        return outcome
    except Exception as exc:
        _fail_job(job, exc)
        raise


def _quiet(preference: dict[str, Any], now_utc: datetime) -> bool:
    try:
        local = now_utc.astimezone(ZoneInfo(preference["timezone"]))
    except ZoneInfoNotFoundError:
        return True
    current = local.strftime("%H:%M")
    start, end = preference["quiet_start"], preference["quiet_end"]
    return start <= current < end if start < end else current >= start or current < end


def _alert_conditions(metrics: dict[str, Any]) -> dict[str, dict[str, Any]]:
    conditions: dict[str, dict[str, Any]] = {}
    if metrics["cash"]["material_drop"]:
        conditions["cash_drop"] = metrics["cash"]
    for item in metrics["overdue_receivables"]:
        if item["material"]:
            conditions[f"receivable:{item['id']}"] = item
    for item in metrics["stockout_products"]:
        conditions[f"stockout:{item['product_id']}"] = item
    for item in metrics["expense_anomalies"]:
        conditions[f"expense:{item['category']}"] = item
    return conditions


def run_threshold_alerts(
    organization_id: int,
    user_id: int,
    recipient: str,
    period: str,
    as_of: date,
    *,
    quiet: bool = False,
) -> dict[str, Any]:
    job = _claim_job(organization_id, "threshold_alerts", period)
    if job is None:
        return {"status": "already_processed"}

    def execute() -> dict[str, Any]:
        metrics = _invoke_metrics(organization_id, user_id, as_of)
        conditions = _alert_conditions(metrics)
        conn = get_connection()
        previous = {
            row["alert_key"]: bool(row["active"])
            for row in conn.execute(
                "SELECT alert_key, active FROM alert_states WHERE organization_id = ?",
                (organization_id,),
            ).fetchall()
        }
        crossings = {key: value for key, value in conditions.items() if not previous.get(key, False)}
        if quiet and crossings:
            conn.close()
            return {"status": "deferred_quiet_hours", "crossings": len(crossings)}
        if crossings:
            digest = hashlib.sha256("|".join(sorted(crossings)).encode()).hexdigest()[:16]
            body = "<h1>VentureCore material alert</h1><ul>" + "".join(
                f"<li>{html.escape(key)}</li>" for key in sorted(crossings)
            ) + "</ul>"
            _deliver_once(organization_id, "alert", f"{period}:{digest}", recipient, "VentureCore material business alert", body)
        for key in set(previous) | set(conditions):
            active = key in conditions
            value = json.dumps(conditions.get(key, {}), default=str, sort_keys=True)
            conn.execute(
                """INSERT INTO alert_states
                   (organization_id, alert_key, active, value_json, source, external_id,
                    last_synced_at, last_alerted_at)
                   VALUES (?, ?, ?, ?, 'computed', ?, CURRENT_TIMESTAMP,
                           CASE WHEN ? THEN CURRENT_TIMESTAMP ELSE NULL END)
                   ON CONFLICT(organization_id, alert_key) DO UPDATE SET
                     active = excluded.active, value_json = excluded.value_json,
                     last_synced_at = CURRENT_TIMESTAMP,
                     last_alerted_at = CASE WHEN ? THEN CURRENT_TIMESTAMP ELSE alert_states.last_alerted_at END""",
                (organization_id, key, int(active), value, key, int(key in crossings), int(key in crossings)),
            )
        conn.commit()
        conn.close()
        return {"status": "sent" if crossings else "no_new_crossing", "crossings": len(crossings)}

    try:
        outcome = run_traced_agent(
            "Threshold Alert Worker", organization_id, f"threshold alerts {period}", execute, job["id"]
        )
        _finish_job(job, outcome)
        return outcome
    except Exception as exc:
        _fail_job(job, exc)
        raise


def run_due_jobs(now_utc: datetime | None = None) -> dict[str, Any]:
    now_utc = now_utc or datetime.now(timezone.utc)
    conn = get_connection()
    preferences = [
        dict(row)
        for row in conn.execute(
            """SELECT p.*, u.id AS resolved_user_id
                 FROM delivery_preferences p
                 JOIN users u ON u.id = p.user_id
                WHERE p.enabled = 1 AND p.organization_id != ?""",
            (DEMO_ORGANIZATION_ID,),
        ).fetchall()
    ]
    conn.close()
    outcomes = []
    for preference in preferences:
        try:
            local = now_utc.astimezone(ZoneInfo(preference["timezone"]))
        except ZoneInfoNotFoundError:
            outcomes.append({"organization_id": preference["organization_id"], "error": "Invalid timezone"})
            continue
        quiet = _quiet(preference, now_utc)
        alert_period = local.strftime("%Y-%m-%dT%H")
        try:
            outcomes.append(run_threshold_alerts(
                preference["organization_id"], preference["resolved_user_id"], preference["email"],
                alert_period, local.date(), quiet=quiet,
            ))
            if local.hour >= int(preference["briefing_hour"]) and not quiet:
                outcomes.append(run_daily_briefing(
                    preference["organization_id"], preference["resolved_user_id"], preference["email"],
                    local.date().isoformat(), local.date(),
                ))
        except Exception as exc:
            logger.warning("Scheduled job failed for organization %s: %s", preference["organization_id"], type(exc).__name__)
            outcomes.append({"organization_id": preference["organization_id"], "error": type(exc).__name__})
    return {"organizations": len(preferences), "outcomes": outcomes}
