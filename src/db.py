"""DuckDB access layer -- the "data lake" query engine.

Rather than importing the CSVs into DuckDB's own storage, each table is
registered as a SQL view directly over its CSV file (`read_csv_auto`).
DuckDB can query CSV/Parquet files in place, so this mirrors how a real
data lake works: the files are the source of truth, and the engine reads
them at query time instead of holding a separate copy in sync.
"""
import os

import duckdb

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")

TABLES = ["departments", "employees", "claims"]


def get_connection() -> duckdb.DuckDBPyConnection:
    """Return an in-memory DuckDB connection with the data-lake CSVs
    registered as views: departments, employees, claims."""
    con = duckdb.connect(database=":memory:")
    for table in TABLES:
        csv_path = os.path.join(DATA_DIR, f"{table}.csv")
        con.execute(f"CREATE VIEW {table} AS SELECT * FROM read_csv_auto('{csv_path}')")
    return con


def get_schema(con: duckdb.DuckDBPyConnection) -> dict[str, list[tuple[str, str]]]:
    """Return {table_name: [(column_name, column_type), ...]}.

    This is the schema context handed to the LLM in Step 4, and also
    the source of truth the SQL-validation guardrail checks against
    in Step 5.
    """
    schema = {}
    for table in TABLES:
        rows = con.execute(f"DESCRIBE {table}").fetchall()
        schema[table] = [(row[0], row[1]) for row in rows]
    return schema


if __name__ == "__main__":
    con = get_connection()

    print("Tables loaded:", TABLES)
    for table, columns in get_schema(con).items():
        print(f"\n{table}:")
        for name, dtype in columns:
            print(f"  {name}: {dtype}")

    print("\n--- Test query: claims per department ---")
    result = con.execute("""
        SELECT d.department_name, COUNT(*) AS claim_count
        FROM claims c
        JOIN employees e ON c.employee_id = e.employee_id
        JOIN departments d ON e.department_id = d.department_id
        GROUP BY d.department_name
        ORDER BY claim_count DESC
    """).df()
    print(result)
