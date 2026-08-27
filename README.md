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
