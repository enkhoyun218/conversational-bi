"""Execute validated SQL against the DuckDB data lake and return a
dataframe -- the last stop before charting/rendering (Step 7) and the
function the Streamlit app (Step 8) will call directly.
"""
import duckdb
import pandas as pd

from llm import question_to_sql
from validate import SQLValidationError, validate_sql


class QueryExecutionError(Exception):
    """Raised when SQL passes validation but still fails at execution
    time -- e.g. a runtime error EXPLAIN's dry-run doesn't surface."""


def run_sql(sql: str, con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    """Validate then execute `sql`, returning the result as a dataframe.

    Raises SQLValidationError if the query fails a guardrail check, or
    QueryExecutionError if it passes validation but still fails to run.
    """
    validate_sql(sql, con)

    try:
        return con.execute(sql).df()
    except duckdb.Error as e:
        raise QueryExecutionError(f"The query failed to run: {e}") from e


def answer_question(question: str, con: duckdb.DuckDBPyConnection, schema: dict) -> dict:
    """End-to-end: NL question -> SQL -> validated -> executed -> dataframe.

    Returns a dict the UI layer can render directly, success or failure:
      {"question", "sql", "dataframe" (or None), "error" (or None)}
    Never raises -- the caller always gets something displayable.
    """
    sql = question_to_sql(question, schema)
    result = {"question": question, "sql": sql, "dataframe": None, "error": None}

    try:
        result["dataframe"] = run_sql(sql, con)
    except (SQLValidationError, QueryExecutionError) as e:
        result["error"] = str(e)

    return result


if __name__ == "__main__":
    from db import get_connection, get_schema

    con = get_connection()
    schema = get_schema(con)

    print("--- Case 1: normal question ---")
    result = answer_question(
        "What is the average service credit by department, highest first?", con, schema
    )
    print(f"Q: {result['question']}")
    print(f"SQL:\n{result['sql']}")
    if result["error"]:
        print(f"ERROR: {result['error']}")
    else:
        print(result["dataframe"])

    print("\n--- Case 2: trick question (no matching column) ---")
    result = answer_question(
        "What is the average salary of employees in each department?", con, schema
    )
    print(f"Q: {result['question']}")
    print(f"SQL:\n{result['sql']}")
    if result["error"]:
        print(f"ERROR (handled gracefully): {result['error']}")
    else:
        print(result["dataframe"])
