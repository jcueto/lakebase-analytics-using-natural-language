"""LLM integration — converts natural language questions into PostgreSQL queries for Lakebase,
or evaluates if a query is unexecutable and provides rationale.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from google import genai

from app.config import Settings

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
You are an expert PostgreSQL data analyst connected to the Databricks Lakebase 'ecommerce' database.
Your job is to analyze the user's natural language request against the provided database schema.

The primary synced table is:
"ecommerce_transactions"."ecommerce_transactions_synced_table"

Columns include:
order_id, order_datetime, order_status, sales_channel, customer_id, customer_name,
customer_age, gender, customer_segment, customer_type, customer_city, customer_state,
customer_country, region, customer_postal_code, payment_method, payment_status, currency,
shipping_method, warehouse, delivery_days, estimated_delivery_days, delivery_status,
return_status, return_reason, customer_rating, review_sentiment, customer_review,
marketing_channel, campaign_name, coupon_code, loyalty_points_earned, loyalty_points_redeemed,
quantity, gross_sales, discount_amount, tax_amount, shipping_cost, net_sales,
product_cost, profit, profit_margin_percentage, customer_lifetime_value,
is_repeat_customer, customer_order_count.

You must choose between two outcomes:
1. EXECUTABLE: You can construct a valid, safe, read-only PostgreSQL query to fulfill the user's request.
2. UNEXECUTABLE: The requested query CANNOT or SHOULD NOT be executed on this database.

### Database Dialect & Rules:
- PostgreSQL dialect (version 17).
- ALWAYS double-quote schema and table names! Example: `"ecommerce_transactions"."ecommerce_transactions_synced_table"`.
- Use double quotes for column names if needed.
- ONLY SELECT queries (including CTEs `WITH ... SELECT`) are allowed.
- Default to `LIMIT 500` unless the question specifies a specific count or asks for aggregated totals.
- Note date column is `order_datetime` (timestamp with time zone). Use `DATE_TRUNC('month', order_datetime)` or `order_datetime::date`.
- Use standard PostgreSQL functions: `DATE_TRUNC`, `EXTRACT`, `COALESCE`, `ROUND`, `COUNT(DISTINCT ...)`, etc.

### When to mark UNEXECUTABLE:
- The user requests data modification (INSERT, UPDATE, DELETE, DROP, TRUNCATE, ALTER, GRANT).
- The user asks for information or entities that do not exist in the schema and cannot be computed from available columns (e.g. asking for "employee salaries", "stock inventory levels", "real-time GPS coordinates" when the tables only have transactions and metadata).
- The request is completely ambiguous or lacks sufficient criteria to formulate a sensible query.
- The user asks for administrative operations on the Lakebase instance or operating system.

### Response JSON Format:
You must respond with ONLY valid JSON with no markdown backticks or commentary outside the JSON.

If EXECUTABLE:
{
  "status": "EXECUTABLE",
  "sql": "<PostgreSQL SELECT query>",
  "explanation": "<1-2 sentence plain-English explanation of how this query computes the result>"
}

If UNEXECUTABLE:
{
  "status": "UNEXECUTABLE",
  "error_type": "<WRITE_ATTEMPT | MISSING_COLUMNS | UNEXECUTABLE_INTENT | AMBIGUOUS_REQUEST>",
  "rationale": "<Clear explanation of why this query cannot be executed on the Lakebase database, specifically naming what is missing, forbidden, or uncomputable>",
  "proposed_sql": "<Optional: If there is a theoretical or hypothetical query or close approximation, provide it here; otherwise null>",
  "suggested_alternative": "<A recommended query the user COULD ask that relates to the available tables/columns, or null>"
}
"""


class LLMClient:
    """Handles NL-to-SQL conversion with unexecutable detection using Google Gemini."""

    def __init__(self, settings: Settings) -> None:
        self._api_key = settings.gemini_api_key
        self._client: genai.Client | None = None
        if self._api_key and self._api_key != "your_gemini_api_key_here":
            self._client = genai.Client(api_key=self._api_key)
        self._model = "gemini-2.5-flash"

    def is_configured(self) -> bool:
        return self._client is not None

    def natural_language_to_sql(
        self, question: str, schema_ddl: str
    ) -> dict[str, Any]:
        """Convert a natural-language question to an executable query or rationale explanation."""
        if not self.is_configured():
            return self._heuristic_fallback(question, schema_ddl)

        user_message = (
            f"## Database Schema (PostgreSQL 17 on Lakebase 'ecommerce')\n{schema_ddl}\n\n"
            f"## User Request\n{question}"
        )

        response = self._client.models.generate_content(
            model=self._model,
            contents=user_message,
            config=genai.types.GenerateContentConfig(
                system_instruction=SYSTEM_PROMPT,
                temperature=0.0,
            ),
        )

        raw = response.text.strip()
        cleaned = re.sub(r"^```(?:json)?\s*", "", raw)
        cleaned = re.sub(r"\s*```$", "", cleaned)

        try:
            parsed = json.loads(cleaned)
        except json.JSONDecodeError as exc:
            raise ValueError(f"LLM returned non-JSON response: {raw}") from exc

        status = parsed.get("status", "EXECUTABLE").upper()

        if status == "UNEXECUTABLE":
            return {
                "status": "UNEXECUTABLE",
                "error_type": parsed.get("error_type", "UNEXECUTABLE_INTENT"),
                "rationale": parsed.get(
                    "rationale",
                    "This proposed query cannot be executed against the Lakebase database with the current schema.",
                ),
                "proposed_sql": parsed.get("proposed_sql"),
                "suggested_alternative": parsed.get("suggested_alternative"),
            }

        sql = parsed.get("sql", "").strip()
        explanation = parsed.get("explanation", "")

        if not sql:
            return {
                "status": "UNEXECUTABLE",
                "error_type": "UNEXECUTABLE_INTENT",
                "rationale": explanation or "The database does not contain sufficient tables or columns to answer this question.",
                "proposed_sql": None,
                "suggested_alternative": "Try asking about orders, net sales, customer segments, or delivery times in 'ecommerce_transactions_synced_table'.",
            }

        # Guard check: Ensure no destructive statements
        first_token = sql.lstrip().split()[0].upper() if sql.split() else ""
        if first_token not in ("SELECT", "WITH", "SHOW", "EXPLAIN"):
            return {
                "status": "UNEXECUTABLE",
                "error_type": "WRITE_ATTEMPT",
                "rationale": (
                    f"Write and mutation operations ('{first_token}') are strictly prohibited. "
                    "Only read-only analytical queries (SELECT) can be executed on this Lakebase instance."
                ),
                "proposed_sql": sql,
                "suggested_alternative": "You can query the current state of records with a SELECT statement instead.",
            }

        return {
            "status": "EXECUTABLE",
            "sql": sql,
            "explanation": explanation,
        }

    def _heuristic_fallback(self, question: str, schema_ddl: str) -> dict[str, Any]:
        """Provides smart baseline queries and explanations when GEMINI_API_KEY is not configured."""
        q = question.lower().strip()

        # Reject write commands
        write_words = ["drop", "delete", "update", "insert", "alter", "truncate", "create"]
        if any(w in q.split() for w in write_words):
            return {
                "status": "UNEXECUTABLE",
                "error_type": "WRITE_ATTEMPT",
                "rationale": "Data modification statements are blocked for safety. Only read-only SELECT analytics queries can be executed.",
                "proposed_sql": f"-- Blocked action: {question}",
                "suggested_alternative": "Ask for totals or summaries using SELECT queries.",
            }

        # If asking for something totally outside schema
        missing_domains = ["employee", "salary", "payroll", "inventory stock", "server temperature"]
        if any(m in q for m in missing_domains):
            return {
                "status": "UNEXECUTABLE",
                "error_type": "MISSING_COLUMNS",
                "rationale": (
                    "The database does not contain tables or columns related to internal human resources, payroll, "
                    "or inventory counts. The available data in 'ecommerce_transactions.ecommerce_transactions_synced_table' "
                    "focuses on e-commerce transaction orders, customer metrics, financial figures, and delivery SLAs."
                ),
                "proposed_sql": None,
                "suggested_alternative": "What are the top 10 customers by revenue and order count?",
            }

        # Top customers query
        if "top" in q and ("customer" in q or "buyer" in q):
            return {
                "status": "EXECUTABLE",
                "sql": """SELECT customer_id, customer_name, customer_segment, 
       ROUND(SUM(net_sales)::numeric, 2) AS total_net_sales, 
       ROUND(SUM(profit)::numeric, 2) AS total_profit, 
       COUNT(order_id) AS total_orders
FROM "ecommerce_transactions"."ecommerce_transactions_synced_table"
GROUP BY customer_id, customer_name, customer_segment
ORDER BY total_net_sales DESC
LIMIT 10;""",
                "explanation": "Aggregates total net sales, profit, and order count per customer and returns the top 10 customers from ecommerce_transactions_synced_table.",
            }

        # Monthly trends
        if "monthly" in q or "trend" in q or "month" in q:
            return {
                "status": "EXECUTABLE",
                "sql": """SELECT DATE_TRUNC('month', order_datetime) AS transaction_month,
       COUNT(order_id) AS total_orders,
       ROUND(SUM(net_sales)::numeric, 2) AS total_net_sales,
       ROUND(SUM(profit)::numeric, 2) AS total_profit
FROM "ecommerce_transactions"."ecommerce_transactions_synced_table"
GROUP BY 1
ORDER BY 1;""",
                "explanation": "Computes month-by-month order count, net sales, and profit totals using order_datetime.",
            }

        # Sales channel breakdown
        if "channel" in q or "sales channel" in q:
            return {
                "status": "EXECUTABLE",
                "sql": """SELECT sales_channel,
       COUNT(order_id) AS order_volume,
       ROUND(SUM(net_sales)::numeric, 2) AS total_sales,
       ROUND(SUM(profit)::numeric, 2) AS total_profit,
       ROUND(AVG(customer_rating)::numeric, 2) AS avg_rating
FROM "ecommerce_transactions"."ecommerce_transactions_synced_table"
GROUP BY sales_channel
ORDER BY total_sales DESC;""",
                "explanation": "Breaks down order volume, net revenue, and ratings by sales channel.",
            }

        # Delivery delay query
        if "delivery" in q or "delay" in q or "warehouse" in q:
            return {
                "status": "EXECUTABLE",
                "sql": """SELECT warehouse, shipping_method,
       COUNT(order_id) AS order_count,
       ROUND(AVG(delivery_days)::numeric, 2) AS avg_delivery_days,
       ROUND(AVG(delivery_days - estimated_delivery_days)::numeric, 2) AS avg_delay_days
FROM "ecommerce_transactions"."ecommerce_transactions_synced_table"
GROUP BY warehouse, shipping_method
ORDER BY order_count DESC;""",
                "explanation": "Calculates average fulfillment days and delay differences by warehouse and shipping method.",
            }

        # Default overview
        return {
            "status": "EXECUTABLE",
            "sql": """SELECT order_id, order_datetime, customer_name, customer_segment, 
       sales_channel, region, net_sales, profit, delivery_status
FROM "ecommerce_transactions"."ecommerce_transactions_synced_table"
ORDER BY order_datetime DESC
LIMIT 25;""",
            "explanation": "Retrieves recent orders and key financial indicators from the ecommerce transactions synced table.",
        }
