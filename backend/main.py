import logging
import secrets
from pathlib import Path
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from starlette.middleware.sessions import SessionMiddleware

from backend.api.research import router as research_router
from backend.api.auth import router as auth_router
from backend.db import init_db

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)

app = FastAPI(title="Multi-Agent Business Research System")

app.add_middleware(SessionMiddleware, secret_key=secrets.token_hex(32))

app.include_router(research_router)
app.include_router(auth_router)

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"
app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")


@app.on_event("startup")
def startup_event():
    init_db()


@app.get("/")
def root():
    return FileResponse(FRONTEND_DIR / "index.html")
