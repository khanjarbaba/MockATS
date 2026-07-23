import os
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

load_dotenv()

from .db import Base, SessionLocal, engine  # noqa: E402
from .routers import logs, profiles, simulate, webhooks  # noqa: E402
from .seed import seed  # noqa: E402

app = FastAPI(title="Mock ATS Simulator", version="0.1.0")

Base.metadata.create_all(bind=engine)
with SessionLocal() as db:
    seed(db)

app.include_router(profiles.router)
app.include_router(simulate.router)
app.include_router(webhooks.router)
app.include_router(logs.router)

STATIC_DIR = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/api/status")
def status():
    return {
        "armed": os.getenv("ARMED", "false").lower() == "true",
        "shl_base_url": os.getenv("SHL_BASE_URL", "(not set)"),
        "callback_base_url": os.getenv("CALLBACK_BASE_URL", "(not set)"),
    }


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")
