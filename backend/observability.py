from __future__ import annotations

import json
import logging
import math
import re
import time
import uuid
from contextvars import ContextVar
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from statistics import median
from typing import Any, Callable, TypeVar

from backend.config import GEMINI_COST_RATES, TRACE_RETENTION_DAYS
from backend.db import get_connection


logger = logging.getLogger("agent_observability")
T = TypeVar("T")
_active_run: ContextVar["AgentTrace | None"] = ContextVar("active_agent_run", default=None)
_last_prune_at = 0.0
_SECRET_KEYS = re.compile(
    r"password|secret|api[_-]?key|authorization|cookie|token|connection[_-]?string|database[_-]?url|dsn",
    re.IGNORECASE,
)
_EMAIL = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)
_CREDENTIAL = re.compile(r"(?i)(AIza[\w-]{20,}|sk-[\w-]{12,}|(https?://)[^\s:/]+:[^\s@]+@)")
_SECRET_ASSIGNMENT = re.compile(
    r"(?i)\b(password|secret|api[_-]?key|authorization|access[_-]?token|refresh[_-]?token|dsn)"
    r"\s*[:=]\s*([^\s,;]+)"
)


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): "[REDACTED]" if _SECRET_KEYS.search(str(key)) else redact(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact(item) for item in value]
    if isinstance(value, str):
        text = _EMAIL.sub("[REDACTED_EMAIL]", value)
        text = _CREDENTIAL.sub("[REDACTED_CREDENTIAL]", text)
        return _SECRET_ASSIGNMENT.sub(lambda match: f"{match.group(1)}=[REDACTED]", text)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return redact(str(value))


def _json(value: Any, limit: int = 20000) -> str:
    text = json.dumps(redact(value), ensure_ascii=False, default=str)
    return text if len(text) <= limit else text[:limit] + "…"


def _text(value: Any, limit: int) -> str:
    if isinstance(value, str):
        result = redact(value)
    else:
        result = _json(value, limit)
    return result if len(result) <= limit else result[:limit] + "…"


def _usage(response: Any) -> tuple[int, int, int]:
    metadata = getattr(response, "usage_metadata", None)
    if metadata is None:
        return 0, 0, 0
    input_tokens = int(getattr(metadata, "prompt_token_count", 0) or 0)
    output_tokens = int(getattr(metadata, "candidates_token_count", 0) or 0)
    output_tokens += int(getattr(metadata, "thoughts_token_count", 0) or 0)
    total_tokens = int(getattr(metadata, "total_token_count", 0) or 0)
    if total_tokens <= 0:
        total_tokens = input_tokens + output_tokens
    return input_tokens, output_tokens, total_tokens


def _cost_minor(model: str, input_tokens: int, output_tokens: int) -> int:
    rates = GEMINI_COST_RATES.get(model, {})
    if not isinstance(rates, dict):
        return 0
    input_rate = Decimal(str(rates.get("input_minor_per_million", 0)))
    output_rate = Decimal(str(rates.get("output_minor_per_million", 0)))
    cost = (
        Decimal(input_tokens) * input_rate + Decimal(output_tokens) * output_rate
    ) / Decimal(1_000_000)
    return int(cost.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def prune_traces(days: int = TRACE_RETENTION_DAYS, *, force: bool = False) -> None:
    global _last_prune_at
    now = time.monotonic()
    if not force and now - _last_prune_at < 3600:
        return
    _last_prune_at = now
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    try:
        conn = get_connection()
        conn.execute("DELETE FROM agent_runs WHERE created_at < ?", (cutoff,))
        conn.commit()
        conn.close()
    except Exception as exc:
        logger.warning("Trace retention failed: %s", type(exc).__name__)


class AgentTrace:
    def __init__(self, agent_name: str, organization_id: int, trigger: str, job_id: str | None = None):
        self.id = str(uuid.uuid4())
        self.organization_id = organization_id
        self.agent_name = agent_name
        self.started = time.perf_counter()
        self.step_index = 0
        self.finished = False
        prune_traces()
        self._write(
            """INSERT INTO agent_runs
               (id, organization_id, agent_name, trigger_text, job_id)
               VALUES (?, ?, ?, ?, ?)""",
            (self.id, organization_id, agent_name, _text(trigger, 4000), job_id),
        )

    def _write(self, sql: str, params: tuple[Any, ...]) -> bool:
        conn = None
        try:
            conn = get_connection()
            conn.execute(sql, params)
            conn.commit()
            return True
        except Exception as exc:
            logger.warning("Trace persistence failed: %s", type(exc).__name__)
            return False
        finally:
            if conn is not None:
                conn.close()

    def _step(
        self, *, step_type: str, latency_ms: int, status: str, model_name: str | None = None,
        tool_name: str | None = None, arguments: Any = None, outcome: Any = None,
        failure: BaseException | None = None, response: Any = None,
    ) -> None:
        self.step_index += 1
        input_tokens, output_tokens, total_tokens = _usage(response)
        cost_minor = _cost_minor(model_name or "", input_tokens, output_tokens)
        self._write(
            """INSERT INTO agent_run_steps
               (run_id, organization_id, step_index, step_type, model_name, tool_name,
                arguments_json, outcome_json, status, failure_mode, input_tokens,
                output_tokens, total_tokens, cost_minor, latency_ms)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                self.id, self.organization_id, self.step_index, step_type, model_name, tool_name,
                _json(arguments) if arguments is not None else None,
                _json(outcome) if outcome is not None else None, status,
                type(failure).__name__ if failure else None, input_tokens, output_tokens,
                total_tokens, cost_minor, max(0, latency_ms),
            ),
        )
        if input_tokens or output_tokens or cost_minor:
            self._write(
                """UPDATE agent_runs SET input_tokens = input_tokens + ?,
                   output_tokens = output_tokens + ?, total_tokens = total_tokens + ?,
                   cost_minor = cost_minor + ? WHERE id = ?""",
                (input_tokens, output_tokens, total_tokens, cost_minor, self.id),
            )

    def model_step(self, model: str, latency_ms: int, response: Any = None, failure: BaseException | None = None) -> None:
        self._step(
            step_type="model", model_name=model, latency_ms=latency_ms,
            status="error" if failure else "success", response=response, failure=failure,
        )

    def tool_step(self, name: str, arguments: Any, outcome: Any, latency_ms: int) -> None:
        error = getattr(outcome, "error", None)
        ok = bool(getattr(outcome, "ok", False))
        self._step(
            step_type="tool", tool_name=name, arguments=arguments,
            outcome=outcome.model_dump(mode="json") if hasattr(outcome, "model_dump") else outcome,
            latency_ms=latency_ms, status="success" if ok else "error",
            failure=RuntimeError(error.code) if error else None,
        )

    def complete(self, result: Any = None, failure: BaseException | None = None) -> None:
        if self.finished:
            return
        self.finished = True
        self._write(
            """UPDATE agent_runs SET status = ?, failure_mode = ?, final_result = ?,
               latency_ms = ?, finished_at = CURRENT_TIMESTAMP WHERE id = ?""",
            (
                "error" if failure else "success",
                type(failure).__name__ if failure else None,
                _text(str(failure) if failure else result, 12000) if result is not None or failure else None,
                int((time.perf_counter() - self.started) * 1000), self.id,
            ),
        )


def active_trace() -> AgentTrace | None:
    return _active_run.get()


def run_traced_agent(
    agent_name: str, organization_id: int, trigger: str, callback: Callable[[], T], job_id: str | None = None,
) -> T:
    if active_trace() is not None:
        return callback()
    try:
        trace = AgentTrace(agent_name, organization_id, trigger, job_id)
    except Exception as exc:
        logger.warning("Trace initialization failed: %s", type(exc).__name__)
        return callback()
    token = _active_run.set(trace)
    try:
        result = callback()
        try:
            trace.complete(result=result)
        except Exception as exc:
            logger.warning("Trace completion failed: %s", type(exc).__name__)
        return result
    except Exception as exc:
        try:
            trace.complete(failure=exc)
        except Exception as trace_exc:
            logger.warning("Trace completion failed: %s", type(trace_exc).__name__)
        raise
    finally:
        _active_run.reset(token)


def record_tool_call(name: str, arguments: Any, outcome: Any, latency_ms: int) -> None:
    trace = active_trace()
    if trace is not None:
        try:
            trace.tool_step(name, arguments, outcome, latency_ms)
        except Exception as exc:
            logger.warning("Tool trace failed: %s", type(exc).__name__)


class _ModelsProxy:
    def __init__(self, target: Any):
        self._target = target

    def generate_content(self, *args: Any, **kwargs: Any) -> Any:
        model = str(kwargs.get("model") or (args[0] if args else "unknown"))
        started = time.perf_counter()
        trace = active_trace()
        try:
            response = self._target.generate_content(*args, **kwargs)
        except Exception as exc:
            if trace is not None:
                try:
                    trace.model_step(model, int((time.perf_counter() - started) * 1000), failure=exc)
                except Exception as trace_exc:
                    logger.warning("Model trace failed: %s", type(trace_exc).__name__)
            raise
        if trace is not None:
            try:
                trace.model_step(model, int((time.perf_counter() - started) * 1000), response=response)
            except Exception as exc:
                logger.warning("Model trace failed: %s", type(exc).__name__)
        return response

    def __getattr__(self, name: str) -> Any:
        return getattr(self._target, name)


class TracedClient:
    def __init__(self, client: Any):
        self._client = client
        self.models = _ModelsProxy(client.models)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._client, name)


def instrument_client(client: Any) -> Any:
    return client if isinstance(client, TracedClient) else TracedClient(client)


def dashboard_data(limit: int = 100) -> dict[str, Any]:
    conn = get_connection()
    runs = [dict(row) for row in conn.execute(
        "SELECT * FROM agent_runs ORDER BY created_at DESC LIMIT ?", (limit,)
    ).fetchall()]
    tools = [dict(row) for row in conn.execute(
        """SELECT tool_name, COUNT(*) AS calls FROM agent_run_steps
           WHERE step_type = 'tool' GROUP BY tool_name ORDER BY calls DESC"""
    ).fetchall()]
    conn.close()
    latencies = sorted(int(row["latency_ms"] or 0) for row in runs if row["latency_ms"] is not None)
    p95_index = max(0, math.ceil(len(latencies) * 0.95) - 1) if latencies else 0
    return {
        "runs": runs,
        "median_latency_ms": int(median(latencies)) if latencies else 0,
        "p95_latency_ms": latencies[p95_index] if latencies else 0,
        "error_rate": (sum(row["status"] == "error" for row in runs) / len(runs) * 100) if runs else 0,
        "tool_frequency": tools,
    }
