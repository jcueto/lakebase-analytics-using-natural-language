"""Application configuration loaded from environment variables and Databricks CLI."""

from __future__ import annotations

import os
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()


@dataclass
class Settings:
    """Application settings for Lakebase and LLM."""

    # Lakebase Connection
    lakebase_project: str
    lakebase_branch: str
    lakebase_endpoint: str
    lakebase_host: str
    lakebase_database: str
    lakebase_role: str
    lakebase_token: str
    lakebase_schemas: list[str]

    # LLM Settings
    gemini_api_key: str | None


def load_settings() -> Settings:
    """Load settings from environment."""
    schemas_raw = os.getenv("LAKEBASE_SCHEMAS", "public")
    schemas = [s.strip() for s in schemas_raw.split(",") if s.strip()]

    return Settings(
        lakebase_project=os.getenv("LAKEBASE_PROJECT", ""),
        lakebase_branch=os.getenv("LAKEBASE_BRANCH", "production"),
        lakebase_endpoint=os.getenv("LAKEBASE_ENDPOINT", "primary"),
        lakebase_host=os.getenv("LAKEBASE_HOST", ""),
        lakebase_database=os.getenv("LAKEBASE_DATABASE", "databricks_postgres"),
        lakebase_role=os.getenv("LAKEBASE_ROLE", ""),
        lakebase_token=os.getenv("LAKEBASE_TOKEN", ""),
        lakebase_schemas=schemas,
        gemini_api_key=os.getenv("GEMINI_API_KEY", ""),
    )
