import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.metrics import collect_all

FRONTEND_DIR = Path(os.getenv("FRONTEND_DIR", Path(__file__).resolve().parent.parent.parent / "frontend"))

app = FastAPI(title="Server Monitor", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)

if FRONTEND_DIR.exists():
    app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.get("/api/metrics")
def metrics():
    return collect_all()


@app.get("/")
def index():
    index_file = FRONTEND_DIR / "index.html"
    if index_file.exists():
        return FileResponse(index_file)
    return {"message": "Server Monitor API", "docs": "/docs"}
