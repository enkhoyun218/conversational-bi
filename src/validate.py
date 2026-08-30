"""Guardrails for LLM-generated SQL before it touches the data lake.

Two distinct risks, checked before anything is executed:

1. Hallucination -- the model invents a table or column that doesn't
   exist (e.g. asked about "salary" when there is no salary column).
2. Scope -- we're handing raw model output to a general-purpose SQL
   engine. The prompt only asks for SELECT, but a prompt is not a
   security boundary; nothing stops a bad response from containing
   something else, so this is checked independently of what we asked
   the model to do.

Rather than writing a custom SQL parser to extract table/column
references (fragile -- breaks on aliases, CTEs, subqueries, functions),
this uses DuckDB's own binder as the validator, via EXPLAIN. EXPLAIN
parses and binds the query plan -- which is exactly what surfaces an
unknown table/column -- without executing it or touching data.
"""
import re

import duckdb

# Statement types this app should never run. Checked with a plain keyword
# scan, so a string literal that happens to contain one of these words
# (e.g. a filter on text containing "delete") could be flagged as a false
# positive. That's an acceptable trade-off for an MVP guardrail: it fails
# closed (blocks a query it shouldn't) rather than open (runs something
# dangerous), and this schema has no free-text columns where that's likely.
FORBIDDEN_KEYWORDS = {
    "INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "CREATE", "ATTACH",
    "DETACH", "COPY", "PRAGMA", "EXPORT", "IMPORT", "TRUNCATE", "GRANT",
    "CALL", "INSTALL", "LOAD", "SET",
}


class SQLValidationError(Exception):
    """Raised when generated SQL fails a guardrail check. The message is
    written to be shown directly to the end user, not just logged."""


def validate_sql(sql: str, con: duckdb.DuckDBPyConnection) -> None:
    """Raise SQLValidationError if `sql` is unsafe, multi-statement, or
    references tables/columns that don't exist. Never executes the query
    -- callers execute separately (Step 6) once this passes."""
    _check_single_statement(sql)
    _check_read_only(sql)
    _check_binds(sql, con)


def _check_single_statement(sql: str) -> None:
    body = sql.strip().rstrip(";")
    if ";" in body:
        raise SQLValidationError("Only a single SQL statement is allowed.")


def _check_read_only(sql: str) -> None:
    if not re.match(r"^\s*(SELECT|WITH)\b", sql, re.IGNORECASE):
        raise SQLValidationError("Only SELECT queries are allowed.")

    tokens = set(re.findall(r"[A-Za-z_]+", sql.upper()))
    forbidden = tokens & FORBIDDEN_KEYWORDS
    if forbidden:
        raise SQLValidationError(
            f"Query contains disallowed keyword(s): {', '.join(sorted(forbidden))}"
        )


def _check_binds(sql: str, con: duckdb.DuckDBPyConnection) -> None:
    try:
        con.execute(f"EXPLAIN {sql}")
    except duckdb.Error as e:
        raise SQLValidationError(
            f"Generated SQL references a table or column that doesn't exist: {e}"
        ) from e


if __name__ == "__main__":
    from db import get_connection, get_schema
    from llm import question_to_sql

    con = get_connection()
    schema = get_schema(con)

    print("--- Case 1: valid LLM-generated SQL should pass ---")
    sql = question_to_sql("How many employees are in each department?", schema)
    print(sql)
    validate_sql(sql, con)
    print("PASSED validation\n")

    print("--- Case 2: hallucinated column should be rejected ---")
    bad_sql = "SELECT salary FROM employees"
    try:
        validate_sql(bad_sql, con)
        print("did not raise (unexpected)")
    except SQLValidationError as e:
        print(f"REJECTED: {e}\n")

    print("--- Case 3: non-SELECT statement should be rejected ---")
    bad_sql = "DROP TABLE employees"
    try:
        validate_sql(bad_sql, con)
        print("did not raise (unexpected)")
    except SQLValidationError as e:
        print(f"REJECTED: {e}\n")

    print("--- Case 4: real hallucination pressure-test ---")
    print("Asking the LLM about a column that doesn't exist in the schema...")
    trick_question = "What is the average salary of employees in each department?"
    trick_sql = question_to_sql(trick_question, schema)
    print(f"Q: {trick_question}")
    print(f"Generated SQL:\n{trick_sql}")
    try:
        validate_sql(trick_sql, con)
        print("PASSED validation (model avoided the trap)")
    except SQLValidationError as e:
        print(f"REJECTED: {e}")
