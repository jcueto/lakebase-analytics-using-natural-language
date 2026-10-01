"""Pydantic models for API request/response shapes compatible with Python 3.9."""

from __future__ import annotations

from typing import Any, List, Optional
from pydantic import BaseModel


class QueryRequest(BaseModel):
    """Natural-language analytics question from the user."""

    question: str


class ColumnInfo(BaseModel):
    """A single column in a table."""

    name: str
    data_type: str


class TableInfo(BaseModel):
    """Metadata for one table in the database."""

    schema_name: str
    table_name: str
    columns: List[ColumnInfo]


class SchemaResponse(BaseModel):
    """Full schema context sent to the frontend."""

    project_id: str
    schemas: List[str]
    tables: List[TableInfo]


class QueryResponse(BaseModel):
    """Result of a natural-language query."""

    question: str
    sql: str
    columns: List[str]
    rows: List[List[Any]]
    row_count: int
    explanation: str


class UnexecutableQueryResponse(BaseModel):
    """Returned when a natural language request cannot be executed."""

    question: str
    error_type: str
    rationale: str
    proposed_sql: Optional[str] = None
    suggested_alternative: Optional[str] = None


class ErrorResponse(BaseModel):
    """Structured error returned to the frontend."""

    error: str
    detail: Optional[str] = None
    sql: Optional[str] = None
    rationale: Optional[str] = None
