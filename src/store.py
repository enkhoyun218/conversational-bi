"""Persistence layer for saved charts.

Charts are stored as a JSON array in store/charts.json -- one record per
saved chart. The SQL is stored, not a data snapshot: re-rendering a
saved chart re-runs the query against DuckDB, so dashboards always show
live data instead of whatever the numbers happened to be when the chart
was first created. It also means re-rendering a saved chart costs zero
extra Claude API calls -- the chart_type/x/y/color decision Haiku made
at save time is stored and reused, only the SQL re-executes.

Writes are atomic (write to a temp file, then os.replace) so an
interrupted write can't leave charts.json half-written and unreadable.
"""
import json
import os
import uuid
from datetime import datetime, timezone

STORE_DIR = os.path.join(os.path.dirname(__file__), "..", "store")
CHARTS_FILE = os.path.join(STORE_DIR, "charts.json")


def _load_charts() -> list[dict]:
    if not os.path.exists(CHARTS_FILE):
        return []
    with open(CHARTS_FILE, "r") as f:
        return json.load(f)


def _save_charts(charts: list[dict]) -> None:
    os.makedirs(STORE_DIR, exist_ok=True)
    tmp_path = CHARTS_FILE + ".tmp"
    with open(tmp_path, "w") as f:
        json.dump(charts, f, indent=2)
    os.replace(tmp_path, CHARTS_FILE)


def save_chart(question: str, sql: str, chart_spec: dict) -> dict:
    """Persist a newly generated chart. Returns the saved record."""
    charts = _load_charts()
    record = {
        "id": str(uuid.uuid4()),
        "name": question,  # editable display name -- defaults to the question
        "question": question,
        "sql": sql,
        "chart_type": chart_spec.get("chart_type"),
        "chart_spec": chart_spec,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    charts.append(record)
    _save_charts(charts)
    return record


def list_charts() -> list[dict]:
    """All saved charts, most recently created first."""
    return sorted(_load_charts(), key=lambda c: c["created_at"], reverse=True)


def get_chart(chart_id: str) -> dict | None:
    for chart in _load_charts():
        if chart["id"] == chart_id:
            return chart
    return None


def delete_chart(chart_id: str) -> bool:
    """Remove a chart by id. Returns True if it existed."""
    charts = _load_charts()
    remaining = [c for c in charts if c["id"] != chart_id]
    if len(remaining) == len(charts):
        return False
    _save_charts(remaining)
    return True


if __name__ == "__main__":
    from chart import suggest_chart
    from db import get_connection, get_schema
    from query import answer_question

    con = get_connection()
    schema = get_schema(con)

    print("--- Save a chart ---")
    result = answer_question("How many claims were filed per department?", con, schema)
    spec = suggest_chart(result["question"], result["sql"], result["dataframe"])
    saved = save_chart(result["question"], result["sql"], spec)
    print(f"Saved chart id={saved['id']}")
    print(f"  name: {saved['name']}")
    print(f"  chart_type: {saved['chart_type']}")

    print("\n--- Reload from disk (simulates an app restart) ---")
    reloaded = list_charts()
    print(f"{len(reloaded)} chart(s) in store")
    for c in reloaded:
        print(f"  [{c['id'][:8]}] {c['name']} ({c['chart_type']})")

    print("\n--- get_chart() round-trip ---")
    fetched = get_chart(saved["id"])
    assert fetched is not None
    assert fetched["sql"] == result["sql"]
    print("OK -- fetched record matches what was saved")

    print("\n--- delete_chart() ---")
    # Save a throwaway second record to delete, so the first stays around
    # for Step 2's "Saved Charts" view demo.
    throwaway = save_chart("test question for delete", "SELECT 1", {"chart_type": "table"})
    deleted = delete_chart(throwaway["id"])
    print(f"deleted: {deleted}")
    assert get_chart(throwaway["id"]) is None
    print("OK -- remaining charts:", len(list_charts()))
