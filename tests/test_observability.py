import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("GEMINI_API_KEY", "test-key")
os.environ.setdefault("SESSION_SECRET", "test-session-secret")

from fastapi.testclient import TestClient
from google.genai import types

import backend.db as database
import backend.observability as observability
from backend.agents.tool_runtime import run_with_tools
from backend.main import app
from backend.tools import ToolContext


class UsageModels:
    def __init__(self):
        self.calls = 0

    def generate_content(self, **kwargs):
        self.calls += 1
        if self.calls == 1:
            call = SimpleNamespace(name="get_inventory_snapshot", args={"lookback_days": 30})
            content = types.Content(
                role="model",
                parts=[types.Part.from_function_call(
                    name="get_inventory_snapshot", args={"lookback_days": 30}
                )],
            )
            return SimpleNamespace(
                function_calls=[call], candidates=[SimpleNamespace(content=content)], text=None,
                usage_metadata=SimpleNamespace(
                    prompt_token_count=100, candidates_token_count=20,
                    thoughts_token_count=5, total_token_count=125,
                ),
            )
        return SimpleNamespace(
            function_calls=[], candidates=[], text="Inventory checked.",
            usage_metadata=SimpleNamespace(
                prompt_token_count=200, candidates_token_count=30,
                thoughts_token_count=0, total_token_count=230,
            ),
        )


class UsageClient:
    def __init__(self):
        self.models = UsageModels()


class ObservabilityTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_database = database.DB_PATH
        database.DB_PATH = Path(self.temp_dir.name) / "observability.db"
        self.client_context = TestClient(app)
        self.client = self.client_context.__enter__()

    def tearDown(self):
        self.client_context.__exit__(None, None, None)
        database.DB_PATH = self.original_database
        self.temp_dir.cleanup()

    def test_agent_run_records_actual_usage_tool_call_and_cost_once(self):
        original_rates = observability.GEMINI_COST_RATES.copy()
        observability.GEMINI_COST_RATES.clear()
        observability.GEMINI_COST_RATES["fixture-model"] = {
            "input_minor_per_million": 1_000_000,
            "output_minor_per_million": 1_000_000,
        }
        try:
            answer = run_with_tools(
                client=UsageClient(), model="fixture-model", question="How is stock?",
                system_prompt="Use the tool.", tool_names=["get_inventory_snapshot"],
                context=ToolContext(
                    organization_id=database.DEMO_ORGANIZATION_ID,
                    user_id=database.get_demo_user_id(),
                ),
            )
        finally:
            observability.GEMINI_COST_RATES.clear()
            observability.GEMINI_COST_RATES.update(original_rates)
        self.assertEqual(answer, "Inventory checked.")
        conn = database.get_connection()
        runs = conn.execute("SELECT * FROM agent_runs").fetchall()
        self.assertEqual(len(runs), 1)
        run = runs[0]
        self.assertEqual(run["status"], "success")
        self.assertEqual((run["input_tokens"], run["output_tokens"], run["total_tokens"]), (300, 55, 355))
        self.assertEqual(run["cost_minor"], 355)
        steps = conn.execute(
            "SELECT step_type, status FROM agent_run_steps WHERE run_id = ? ORDER BY step_index",
            (run["id"],),
        ).fetchall()
        conn.close()
        self.assertEqual([(row[0], row[1]) for row in steps], [
            ("model", "success"), ("tool", "success"), ("model", "success")
        ])

    def test_failed_run_is_single_redacted_trace_and_trace_failure_is_best_effort(self):
        def fail():
            raise RuntimeError("owner@example.com failed")

        with self.assertRaises(RuntimeError):
            observability.run_traced_agent("Fixture Agent", 1, "api_key=secret owner@example.com", fail)
        conn = database.get_connection()
        rows = conn.execute("SELECT * FROM agent_runs WHERE agent_name = 'Fixture Agent'").fetchall()
        conn.close()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["status"], "error")
        self.assertEqual(rows[0]["failure_mode"], "RuntimeError")
        self.assertNotIn("owner@example.com", rows[0]["trigger_text"])
        self.assertNotIn("secret", rows[0]["trigger_text"])

        with patch("backend.observability.get_connection", side_effect=OSError("database unavailable")):
            result = observability.run_traced_agent("Best effort", 1, "question", lambda: "answer")
        self.assertEqual(result, "answer")

    def test_admin_page_is_forbidden_to_normal_users(self):
        signup = self.client.post(
            "/auth/signup", json={"email": "member@example.com", "password": "strong-password"}
        )
        self.assertEqual(signup.status_code, 200)
        self.assertEqual(self.client.get("/internal/agent-runs").status_code, 403)
        conn = database.get_connection()
        conn.execute("UPDATE users SET role = 'admin' WHERE email = 'member@example.com'")
        conn.commit()
        conn.close()
        page = self.client.get("/internal/agent-runs")
        self.assertEqual(page.status_code, 200)
        self.assertIn("Agent runs", page.text)

    def test_retention_prunes_only_expired_traces(self):
        observability.run_traced_agent("Old Agent", 1, "old", lambda: "ok")
        observability.run_traced_agent("New Agent", 1, "new", lambda: "ok")
        conn = database.get_connection()
        conn.execute("UPDATE agent_runs SET created_at = '2000-01-01 00:00:00' WHERE agent_name = 'Old Agent'")
        conn.commit()
        conn.close()
        observability.prune_traces(days=30, force=True)
        conn = database.get_connection()
        names = [row[0] for row in conn.execute("SELECT agent_name FROM agent_runs").fetchall()]
        conn.close()
        self.assertNotIn("Old Agent", names)
        self.assertIn("New Agent", names)


if __name__ == "__main__":
    unittest.main()
