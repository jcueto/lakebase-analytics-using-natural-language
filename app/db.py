"""Databricks Lakebase client with automatic OAuth token generation via Databricks CLI."""

from __future__ import annotations

import json
import logging
import os
import subprocess
from contextlib import contextmanager
from typing import Generator

import psycopg2
import psycopg2.extras

from app.config import Settings
from app.models import ColumnInfo, TableInfo

logger = logging.getLogger(__name__)


class LakebaseClient:
    """Wrapper around psycopg2 with automatic OAuth credential refresh."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._schema_cache: list[TableInfo] | None = None
        self._cached_token: str | None = None

    def _get_active_token(self) -> str:
        """Retrieve token from env, or generate a fresh one via Databricks CLI."""
        # 1. If explicit token is provided in settings/env and not the placeholder, try it
        if self._settings.lakebase_token and self._settings.lakebase_token != "paste_your_oauth_token_here":
            return self._settings.lakebase_token

        # 2. Use cached token if available
        if self._cached_token:
            return self._cached_token

        # 3. Request fresh token via Databricks CLI
        databricks_bin = os.path.expanduser("~/bin/databricks")
        target_endpoint = (
            f"projects/{self._settings.lakebase_project}/"
            f"branches/{self._settings.lakebase_branch}/"
            f"endpoints/{self._settings.lakebase_endpoint}"
        )
        logger.info("Generating fresh Lakebase OAuth token for endpoint: %s", target_endpoint)
        
        env = os.environ.copy()
        env["PATH"] = f"{os.path.expanduser('~/bin')}:{env.get('PATH', '')}"

        res = subprocess.run(
            [databricks_bin, "postgres", "generate-database-credential", target_endpoint, "-o", "json"],
            capture_output=True,
            text=True,
            env=env,
        )

        if res.returncode != 0:
            raise RuntimeError(
                f"Databricks CLI failed to generate database credential: {res.stderr or res.stdout}"
            )

        data = json.loads(res.stdout)
        self._cached_token = data["token"]
        logger.info("Generated token successfully (expires: %s)", data.get("expire_time"))
        return self._cached_token

    @contextmanager
    def _connect(self) -> Generator[psycopg2.extensions.connection, None, None]:
        """Yield a PostgreSQL connection with token refresh on auth failure."""
        token = self._get_active_token()
        try:
            conn = psycopg2.connect(
                host=self._settings.lakebase_host,
                dbname=self._settings.lakebase_database,
                user=self._settings.lakebase_role,
                password=token,
                sslmode="require",
                connect_timeout=30,
            )
        except psycopg2.OperationalError as exc:
            # If authentication expired/failed, invalidate cached token and retry once
            if "password authentication failed" in str(exc) or "expired" in str(exc).lower():
                logger.warning("Token may be expired. Refreshing token via Databricks CLI...")
                self._cached_token = None
                self._settings.lakebase_token = ""
                fresh_token = self._get_active_token()
                conn = psycopg2.connect(
                    host=self._settings.lakebase_host,
                    dbname=self._settings.lakebase_database,
                    user=self._settings.lakebase_role,
                    password=fresh_token,
                    sslmode="require",
                    connect_timeout=30,
                )
            else:
                raise

        try:
            yield conn
        finally:
            conn.close()

    # ── Schema introspection ──────────────────────────────────────────

    def get_schema(self, *, refresh: bool = False) -> list[TableInfo]:
        """Introspect tables and columns across all configured schemas."""
        if self._schema_cache is not None and not refresh:
            return self._schema_cache

        schemas = self._settings.lakebase_schemas
        tables: list[TableInfo] = []

        with self._connect() as conn:
            with conn.cursor() as cur:
                # Query user-facing tables
                cur.execute(
                    """
                    SELECT table_schema, table_name
                    FROM information_schema.tables
                    WHERE table_schema = ANY(%s)
                      AND table_type IN ('BASE TABLE', 'VIEW')
                    ORDER BY table_schema, table_name
                    """,
                    (schemas,),
                )
                table_rows = cur.fetchall()

                for schema_name, table_name in table_rows:
                    cur.execute(
                        """
                        SELECT column_name, data_type
                        FROM information_schema.columns
                        WHERE table_schema = %s
                          AND table_name = %s
                        ORDER BY ordinal_position
                        """,
                        (schema_name, table_name),
                    )
                    col_rows = cur.fetchall()
                    columns = [
                        ColumnInfo(name=c_name, data_type=c_type)
                        for c_name, c_type in col_rows
                    ]

                    tables.append(
                        TableInfo(
                            schema_name=schema_name,
                            table_name=table_name,
                            columns=columns,
                        )
                    )

        self._schema_cache = tables
        logger.info(
            "Introspected %d tables across schemas %s", len(tables), schemas
        )
        return tables

    def get_schema_ddl(self) -> str:
        """Return schema DDL representation formatted for the LLM."""
        tables = self.get_schema()
        parts: list[str] = []
        for t in tables:
            cols = ", ".join(f'"{c.name}" {c.data_type}' for c in t.columns)
            # Use double quotes for schema and table to handle hyphenated names like "sales-analytics-data"
            parts.append(f'"{t.schema_name}"."{t.table_name}" (\n  {cols}\n);')
        return "\n\n".join(parts)

    # ── Query execution ───────────────────────────────────────────────

    def execute_query(
        self, sql: str, *, max_rows: int = 5_000
    ) -> tuple[list[str], list[list]]:
        """Execute SELECT query against Lakebase and return columns and rows."""
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(sql)
                if cur.description is None:
                    return ["status"], [["Command executed successfully"]]
                columns = [desc[0] for desc in cur.description]
                rows = [list(row) for row in cur.fetchmany(max_rows)]
                return columns, rows
