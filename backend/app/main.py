"""FastAPI application entry point."""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app import storage
from app.routers import perception, segment, upload

app = FastAPI(title="Make It Lively", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(upload.router)
app.include_router(perception.router)
app.include_router(segment.router)

storage.LAYERS_DIR.mkdir(parents=True, exist_ok=True)
app.mount(
    "/storage/layers",
    StaticFiles(directory=storage.LAYERS_DIR),
    name="layers",
)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
