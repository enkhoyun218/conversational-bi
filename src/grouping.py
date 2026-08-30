"""AI auto-grouping: cluster saved charts into dashboard pages via Claude Haiku.

Same trust-but-verify pattern used for SQL generation (validate.py) and
chart suggestion (chart.py): the model's grouping is checked against the
real set of chart ids before being used. A hallucinated id is dropped;
any chart the model leaves out of every group is swept into a fallback
group instead of silently vanishing from every dashboard.
"""
import json

import anthropic
from dotenv import load_dotenv

load_dotenv()

MODEL = "claude-haiku-4-5"
client = anthropic.Anthropic()

SYSTEM_PROMPT = """You group a user's saved BI charts into a small number \
of dashboard pages by topic, based on their question text.

Respond with ONLY a JSON array -- no markdown, no commentary. Each \
element:
- "title": a short, human-readable dashboard page title (e.g. \
"Claims Overview", "Workforce Demographics")
- "chart_ids": a list of the chart ids (given below) that belong on \
that page

Rules:
- Every chart id given to you must appear in exactly one group.
- Group by topic/theme, not by chart type.
- Prefer 2-4 groups for a typical set. Don't invent a group for one
  unrelated chart -- put leftover charts that don't fit a real theme
  in a group titled "Other" instead.
"""


def _clean_json_block(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    return text


def _fallback_groups(charts: list[dict]) -> list[dict]:
    """Degenerate but always-valid grouping: everything on one page."""
    return [{"title": "All Charts", "chart_ids": [c["id"] for c in charts]}]


def _validate_groups(groups, charts: list[dict]) -> list[dict]:
    if not isinstance(groups, list) or not groups:
        return _fallback_groups(charts)

    valid_ids = {c["id"] for c in charts}
    seen_ids = set()
    cleaned = []

    for group in groups:
        if not isinstance(group, dict):
            continue
        title = group.get("title")
        chart_ids = group.get("chart_ids")
        if not title or not isinstance(chart_ids, list):
            continue
        # Drop hallucinated ids and any id already placed in an earlier
        # group, so a chart can't end up on two pages at once.
        kept = [cid for cid in chart_ids if cid in valid_ids and cid not in seen_ids]
        if not kept:
            continue
        seen_ids.update(kept)
        cleaned.append({"title": title, "chart_ids": kept})

    # Anything the model omitted (including "it returned nothing usable")
    leftover = [cid for cid in valid_ids if cid not in seen_ids]
    if leftover:
        cleaned.append({"title": "Other", "chart_ids": leftover})

    return cleaned if cleaned else _fallback_groups(charts)


def group_charts(charts: list[dict]) -> list[dict]:
    """Ask Claude Haiku to group saved charts into dashboard pages.

    Returns a validated list of {"title": str, "chart_ids": [str, ...]}.
    Every chart id passed in ends up in exactly one group.
    """
    if not charts:
        return []

    chart_list_text = "\n".join(
        f'- id: {c["id"]}, question: "{c["question"]}", chart_type: {c["chart_type"]}'
        for c in charts
    )

    response = client.messages.create(
        model=MODEL,
        max_tokens=1000,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": f"Charts:\n{chart_list_text}"}],
    )
    raw = next(block.text for block in response.content if block.type == "text")

    try:
        groups = json.loads(_clean_json_block(raw))
    except json.JSONDecodeError:
        return _fallback_groups(charts)

    return _validate_groups(groups, charts)


if __name__ == "__main__":
    from store import list_charts, save_dashboards

    charts = list_charts()
    if not charts:
        print("No saved charts to group -- create some via the app first.")
    else:
        print(f"Grouping {len(charts)} saved chart(s)...")
        groups = group_charts(charts)

        id_to_question = {c["id"]: c["question"] for c in charts}
        for g in groups:
            print(f"\n{g['title']}")
            for cid in g["chart_ids"]:
                print(f"  - {id_to_question.get(cid, '???')}")

        saved = save_dashboards(groups)
        print(f"\nSaved {len(saved)} dashboard page(s) to the store.")
