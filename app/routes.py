"""API routes for natural language analytics on Databricks Lakebase."""

from __future__ import annotations

import logging
from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

from app.config import Settings, load_settings
from app.db import LakebaseClient
from app.llm import LLMClient
from app.models import (
    ErrorResponse,
    QueryRequest,
    QueryResponse,
    SchemaResponse,
    UnexecutableQueryResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api")

_settings: Settings | None = None
_db: LakebaseClient | None = None
_llm: LLMClient | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = load_settings()
    return _settings


def get_db() -> LakebaseClient:
    global _db
    if _db is None:
        _db = LakebaseClient(get_settings())
    return _db


def get_llm() -> LLMClient:
    global _llm
    if _llm is None:
        _llm = LLMClient(get_settings())
    return _llm


@router.get("/schema", response_model=SchemaResponse)
async def get_schema(refresh: bool = False):
    """Return database schema across configured Lakebase schemas."""
    try:
        db = get_db()
        settings = get_settings()
        tables = db.get_schema(refresh=refresh)
        return SchemaResponse(
            project_id=settings.lakebase_project,
            schemas=settings.lakebase_schemas,
            tables=tables,
        )
    except Exception as exc:
        logger.exception("Failed to fetch Lakebase schema")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/query")
async def run_query(req: QueryRequest):
    """
    Evaluate natural-language question.
    Returns QueryResponse if executable, or UnexecutableQueryResponse with rationale.
    """
    db = get_db()
    llm = get_llm()

    # 1. Translate question to SQL or detect unexecutable request
    try:
        schema_ddl = db.get_schema_ddl()
        llm_result = llm.natural_language_to_sql(req.question, schema_ddl)
    except Exception as exc:
        logger.exception("Error translating request to SQL")
        return JSONResponse(
            status_code=400,
            content=ErrorResponse(
                error="LLM Translation Error",
                detail=str(exc),
            ).model_dump(),
        )

    # 2. Check if the LLM concluded the query is unexecutable
    if llm_result.get("status") == "UNEXECUTABLE":
        return JSONResponse(
            status_code=422,
            content=UnexecutableQueryResponse(
                question=req.question,
                error_type=llm_result.get("error_type", "UNEXECUTABLE_INTENT"),
                rationale=llm_result.get("rationale", "This query cannot be fulfilled on the database."),
                proposed_sql=llm_result.get("proposed_sql"),
                suggested_alternative=llm_result.get("suggested_alternative"),
            ).model_dump(),
        )

    sql = llm_result["sql"]
    explanation = llm_result.get("explanation", "")

    # 3. Execute against Lakebase
    try:
        columns, rows = db.execute_query(sql)
    except Exception as exc:
        logger.exception("Database query execution error on SQL: %s", sql)
        # Format a helpful unexecutable response with database engine rationale
        return JSONResponse(
            status_code=422,
            content=UnexecutableQueryResponse(
                question=req.question,
                error_type="DATABASE_EXECUTION_ERROR",
                rationale=f"PostgreSQL syntax or execution rejected the query: {str(exc).strip()}",
                proposed_sql=sql,
                suggested_alternative="Try reformulating the question or referencing existing table columns.",
            ).model_dump(),
        )

    # 4. Serialize for JSON
    def _serialize(v):
        if v is None:
            return None
        if isinstance(v, (int, float, bool, str)):
            return v
        return str(v)

    serialized_rows = [[_serialize(val) for val in row] for row in rows]

    return QueryResponse(
        question=req.question,
        sql=sql,
        columns=columns,
        rows=serialized_rows,
        row_count=len(serialized_rows),
        explanation=explanation,
    )
