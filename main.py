"""Entry point — FastAPI application."""

from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.routes import router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
)

app = FastAPI(
    title="NL Analytics — Databricks Lakebase",
    description="Ask questions in plain English and get answers from your Databricks Lakebase.",
    version="0.1.0",
)

app.include_router(router)

# Serve the frontend as static files (must come after API routes)
app.mount("/", StaticFiles(directory="static", html=True), name="static")
