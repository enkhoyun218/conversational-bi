"""Plain-English question -> SQL, via Claude Haiku.

This is the core of "conversational BI": instead of a human analyst
translating a business question into SQL, the LLM does it, grounded in
the actual table schema so it can't reference columns that don't exist
(mostly -- Step 5 adds a guardrail for when it does anyway).
"""
import os

import anthropic
from dotenv import load_dotenv

load_dotenv()

# Cheapest current Claude model. Text-to-SQL over a 3-table schema is a
# simple, well-specified task -- it doesn't need a larger model, and
# keeping cost low matters for a self-service tool that could get a lot
# of ad-hoc queries.
MODEL = "claude-haiku-4-5"

client = anthropic.Anthropic()

SYSTEM_PROMPT = """You are a SQL generator for a DuckDB database. Given a \
database schema and a plain-English question, write a single SQL query \
that answers the question.

Rules:
- Use only the tables and columns listed in the schema below. Never invent \
table or column names that aren't listed.
- Return ONLY the raw SQL query. No markdown code fences, no explanation, \
no commentary -- just the SQL.
- Write valid DuckDB SQL syntax.
- claims.application_end is NULL for claims that are still pending (not \
yet resolved). Account for this when relevant -- e.g. "pending" or "open" \
claims means application_end IS NULL.

Schema:
{schema}"""


def format_schema(schema: dict) -> str:
    """Render {table: [(column, type), ...]} as text for the prompt."""
    lines = []
    for table, columns in schema.items():
        lines.append(f"Table: {table}")
        for name, dtype in columns:
            lines.append(f"  - {name} ({dtype})")
        lines.append("")
    return "\n".join(lines)


def _clean_sql(sql: str) -> str:
    """Strip markdown code fences, in case the model adds them anyway."""
    sql = sql.strip()
    if sql.startswith("```"):
        lines = sql.split("\n")
        lines = lines[1:]  # drop opening fence (```sql or ```)
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        sql = "\n".join(lines).strip()
    return sql


def question_to_sql(question: str, schema: dict) -> str:
    """Translate a plain-English question into a SQL query via Claude Haiku."""
    system = SYSTEM_PROMPT.format(schema=format_schema(schema))

    response = client.messages.create(
        model=MODEL,
        max_tokens=500,
        system=system,
        messages=[{"role": "user", "content": question}],
    )

    raw_sql = next(block.text for block in response.content if block.type == "text")
    return _clean_sql(raw_sql)


if __name__ == "__main__":
    from db import get_connection, get_schema

    con = get_connection()
    schema = get_schema(con)

    questions = [
        "How many claims were filed per department?",
        "What is the average age of employees who filed a claim?",
        "Which injury type is most common?",
    ]

    for q in questions:
        print(f"Q: {q}")
        sql = question_to_sql(q, schema)
        print(f"SQL:\n{sql}\n")
