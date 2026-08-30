"""Suggest a chart type/columns via Claude Haiku, then render with Plotly.

Same trust-but-verify pattern as the SQL guardrail (Step 5): the LLM's
suggested chart spec is checked against the *actual* result dataframe
before being used. If the model's answer is missing, malformed, or
points at a column that doesn't exist in the result, a rule-based
fallback picks a sane default instead of crashing or rendering garbage.
"""
import json

import anthropic
import pandas as pd
import plotly.express as px
from dotenv import load_dotenv

load_dotenv()

MODEL = "claude-haiku-4-5"
CHART_TYPES = {"bar", "line", "pie", "scatter", "metric", "table"}

client = anthropic.Anthropic()

SYSTEM_PROMPT = """You are a data visualization assistant. Given a plain-\
English question, the SQL that answered it, and the resulting data's \
columns, suggest the best way to visualize the result.

Respond with ONLY a JSON object -- no markdown, no commentary. Fields:
- "chart_type": one of "bar", "line", "pie", "scatter", "metric", "table"
- "x": column name for the x-axis / category (or null)
- "y": column name for the y-axis / value (or null)
- "color": optional column name to group/color by, or null
- "title": a short, human-readable chart title

Guidance:
- Exactly one row and one column -> "metric" (a single KPI number).
- Use "line" only if there is a clear date/sequence column.
- Use "pie" only for <= 8 categories summing to a meaningful whole.
- Default to "bar" for categorical comparisons.
- If the data doesn't visualize well (many columns, free text), use "table".
"""


def _clean_json_block(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    return text


def _fallback_spec(df: pd.DataFrame) -> dict:
    """Rule-based default used when the LLM's suggestion is missing,
    malformed, or invalid for this dataframe."""
    if df.shape == (1, 1):
        return {"chart_type": "metric", "x": None, "y": df.columns[0],
                 "color": None, "title": df.columns[0]}
    if df.shape[1] >= 2:
        numeric_cols = df.select_dtypes("number").columns.tolist()
        y = numeric_cols[0] if numeric_cols else df.columns[1]
        x = next((c for c in df.columns if c != y), df.columns[0])
        return {"chart_type": "bar", "x": x, "y": y, "color": None,
                 "title": f"{y} by {x}"}
    return {"chart_type": "table", "x": None, "y": None, "color": None,
             "title": "Result"}


def _validate_spec(spec: dict, df: pd.DataFrame) -> dict:
    """Confirm the LLM's suggested chart_type and columns are actually
    usable against the real result dataframe; fall back if not."""
    if not isinstance(spec, dict) or spec.get("chart_type") not in CHART_TYPES:
        return _fallback_spec(df)

    columns = set(df.columns)
    for key in ("x", "y", "color"):
        value = spec.get(key)
        if value is not None and value not in columns:
            return _fallback_spec(df)

    if spec["chart_type"] == "metric":
        # A metric needs one unambiguous value to display. That's only
        # safe to infer when the result is exactly one row and one
        # column -- otherwise we need the model to have named a "y".
        if spec.get("y") is None and df.shape != (1, 1):
            return _fallback_spec(df)
    elif spec.get("x") is None and spec.get("y") is None:
        return _fallback_spec(df)

    spec.setdefault("title", "Result")
    spec.setdefault("color", None)
    return spec


def suggest_chart(question: str, sql: str, df: pd.DataFrame) -> dict:
    """Ask Claude Haiku how to visualize a query result. The response is
    validated against `df` before being returned -- never trusted as-is."""
    columns_desc = "\n".join(f"- {col} ({dtype})" for col, dtype in df.dtypes.items())
    sample = df.head(3).to_dict(orient="records")

    user_message = f"""Question: {question}

SQL:
{sql}

Result columns:
{columns_desc}

Sample rows:
{sample}"""

    response = client.messages.create(
        model=MODEL,
        max_tokens=300,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_message}],
    )
    raw = next(block.text for block in response.content if block.type == "text")

    try:
        spec = json.loads(_clean_json_block(raw))
    except json.JSONDecodeError:
        return _fallback_spec(df)

    return _validate_spec(spec, df)


def render_chart(df: pd.DataFrame, spec: dict):
    """Build a Plotly figure from a validated chart spec, or None for
    'metric'/'table' -- the caller renders those two cases directly."""
    chart_type = spec["chart_type"]

    if chart_type in ("metric", "table"):
        return None

    if chart_type == "pie":
        return px.pie(df, names=spec["x"], values=spec["y"], title=spec.get("title"))

    kwargs = {"title": spec.get("title")}
    if spec.get("color"):
        kwargs["color"] = spec["color"]

    if chart_type == "bar":
        return px.bar(df, x=spec["x"], y=spec["y"], **kwargs)
    if chart_type == "line":
        return px.line(df, x=spec["x"], y=spec["y"], **kwargs)
    if chart_type == "scatter":
        return px.scatter(df, x=spec["x"], y=spec["y"], **kwargs)

    return None


if __name__ == "__main__":
    from db import get_connection, get_schema
    from query import answer_question

    con = get_connection()
    schema = get_schema(con)

    questions = [
        "How many claims were filed per department?",
        "What is the average age of employees who filed a claim?",
        "How many claims were filed per month?",
        "What's the breakdown of claims by injury type?",
    ]

    for q in questions:
        result = answer_question(q, con, schema)
        print(f"Q: {q}")
        if result["error"]:
            print(f"  ERROR: {result['error']}\n")
            continue

        df = result["dataframe"]
        spec = suggest_chart(q, result["sql"], df)
        print(f"  chart spec: {spec}")

        fig = render_chart(df, spec)
        if fig is not None:
            print(f"  figure built OK ({spec['chart_type']}), {len(fig.data)} trace(s)")
        else:
            print(f"  chart_type '{spec['chart_type']}' -- not a Plotly figure, rendered directly by the caller")
        print()
