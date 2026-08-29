import logging
from pathlib import Path
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from starlette.middleware.sessions import SessionMiddleware

from backend.api.research import router as research_router
from backend.api.auth import router as auth_router
from backend.api.shopify import router as shopify_router
from backend.api.workers import router as workers_router
from backend.config import SESSION_SECRET
from backend.db import init_db
from backend.seed_demo import seed_demo
from backend.shopify import start_reconciliation_worker

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)

app = FastAPI(title="VentureCore AI")

app.add_middleware(SessionMiddleware, secret_key=SESSION_SECRET)

app.include_router(research_router)
app.include_router(auth_router)
app.include_router(shopify_router)
app.include_router(workers_router)

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"
app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")


@app.on_event("startup")
def startup_event():
    init_db()
    seed_demo()
    start_reconciliation_worker()


@app.get("/")
def root():
    return FileResponse(FRONTEND_DIR / "index.html")
