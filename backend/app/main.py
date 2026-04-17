"""FastAPI application entry point."""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.routers import upload

app = FastAPI(title="Make It Lively", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(upload.router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
