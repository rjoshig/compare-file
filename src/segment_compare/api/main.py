"""FastAPI app factory for the Phase 3 backend.

Run locally::

    uvicorn segment_compare.api.main:app --reload --port 8000

The Vue dev server proxies ``/api/*`` requests here (see
``ui/vite.config.js``).
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from segment_compare.api.routes import router

app = FastAPI(
    title="segment-compare API",
    version="0.0.1",
    description=(
        "Backend for the Phase 3 Vue UI. Serves template layouts, "
        "persists user configs, runs the comparison engine, and "
        "serves the resulting HTML report."
    ),
)

# Dev-time CORS — the Vue Vite server runs on :5173 by default.
# Production deployment should narrow this to the actual UI origin.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)
