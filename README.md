# VentureCore AI

## Development

Use Python 3.11, then install the application and development dependencies:

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
cp .env.example .env
uvicorn backend.main:app --reload
```

Set a real `GEMINI_API_KEY` in `.env` before starting the application. Set a stable
`SESSION_SECRET` in production.

## Agent observability

Every agent invocation records one best-effort, redacted trace with model and tool steps,
provider-reported token usage, latency, outcome, and cost. Administrators can review recent
runs at `/internal/agent-runs`; ordinary accounts and demo visitors cannot access this page.

`TRACE_RETENTION_DAYS` controls automatic trace cleanup and defaults to 30 days.
`GEMINI_COST_RATES_JSON` maps an exact model name to its current input and output rates in
integer USD cents per million tokens. Keep this configuration aligned with the provider's
pricing; an empty mapping records actual tokens and a zero cost, which is appropriate for a
free-tier deployment.

## Public demo

Startup automatically creates or refreshes the fictional, read-only Harbour & Pine demo
workspace. Visitors can open it from the root page without an account. To seed a specific
database manually, run:

```bash
python -m backend.seed_demo --database /path/to/app.db
```

The command is idempotent: it replaces only organisation `2` demo records and never changes a
real account's organisation data.

## Database migrations

Startup applies pending migrations automatically. To run or reverse the current migration
explicitly against a database copy:

```bash
python -m backend.migrations upgrade --database /path/to/app.db
python -m backend.migrations downgrade --database /path/to/app.db
```

The migration logs each legacy monetary value that requires half-up rounding at its currency's
minor-unit boundary.

## Verification

```bash
python -m unittest discover -s tests -v
ruff check backend tests
```

The normal test discovery command includes the offline numeric agent evaluations in
`tests/test_agent_evals.py`; they use the committed deterministic fixture and do not call Gemini.
