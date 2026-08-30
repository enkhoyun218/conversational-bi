"""Streamlit UI: the self-service front door to the whole pipeline.

question -> question_to_sql() -> validate_sql() -> run_sql() -> suggest_chart()
-> render_chart(), all wired together in query.answer_question() /
chart.suggest_chart(). This file is presentation only -- no pipeline logic
lives here, so the same functions this app calls are independently testable
(see the __main__ blocks in db.py, llm.py, validate.py, query.py, chart.py,
store.py).

Every chart generated from a successful question is auto-saved (store.py)
so it shows up in the "Saved Charts" view -- and later, dashboards -- without
the user needing a separate "save" action.
"""
import anthropic
import pandas as pd
import streamlit as st

import store
from chart import render_chart, suggest_chart
from db import get_connection, get_schema
from grouping import group_charts
from query import QueryExecutionError, answer_question, run_sql
from validate import SQLValidationError

st.set_page_config(page_title="Conversational BI", page_icon="\U0001F4CA", layout="wide")


@st.cache_resource
def load_data():
    con = get_connection()
    schema = get_schema(con)
    return con, schema


con, schema = load_data()

EXAMPLE_QUESTIONS = [
    "How many claims were filed per department?",
    "What is the average age of employees who filed a claim?",
    "Which injury type is most common?",
    "How many claims are still pending?",
]


def _set_question(q: str) -> None:
    st.session_state["question"] = q


def display_chart_result(df: pd.DataFrame, spec: dict) -> None:
    """Render a chart/metric/table for a validated spec + dataframe.

    Called from inside whatever Streamlit container the caller wants it
    in (a column, a tab, the page body) -- bare st.* calls route into the
    active `with` block automatically, so this doesn't need a container
    argument. Shared by the "Ask" flow, "Saved Charts", and (Step 4) the
    dashboard view, instead of three copies of the same branch.
    """
    if spec["chart_type"] == "metric":
        value = df.iloc[0][spec["y"]] if spec.get("y") else df.iloc[0, 0]
        if isinstance(value, float):
            value = round(value, 2)
        st.metric(spec.get("title", "Result"), value)
    elif spec["chart_type"] == "table":
        st.dataframe(df, use_container_width=True)
    else:
        fig = render_chart(df, spec)
        st.plotly_chart(fig, use_container_width=True)


def render_ask_view() -> None:
    st.title("\U0001F4CA Conversational BI")
    st.caption(
        "Ask a plain-English question about the pension/disability dataset. "
        "Claude translates it to SQL, DuckDB runs it, and the result is charted automatically."
    )

    with st.form("question_form"):
        question = st.text_input(
            "Ask a question about the data",
            key="question",
            placeholder="e.g. How many claims were filed per department?",
        )
        submitted = st.form_submit_button("Ask", type="primary")

    if submitted and not question:
        st.warning("Type a question first.")

    if submitted and question:
        with st.spinner("Translating to SQL..."):
            try:
                result = answer_question(question, con, schema)
            except anthropic.AuthenticationError:
                st.error("Invalid Anthropic API key -- check your .env file.")
                st.stop()
            except anthropic.RateLimitError:
                st.error("Rate limited by the Claude API. Try again in a moment.")
                st.stop()
            except anthropic.APIConnectionError:
                st.error("Couldn't reach the Claude API. Check your network connection.")
                st.stop()
            except anthropic.APIStatusError as e:
                st.error(f"Claude API error: {e.message}")
                st.stop()
            except Exception as e:  # last-resort UI safety net, not a substitute for the above
                st.error(f"Unexpected error: {e}")
                st.stop()

        st.subheader("Generated SQL")
        st.code(result["sql"], language="sql")

        if result["error"]:
            st.error(result["error"])
        else:
            df = result["dataframe"]
            spec = suggest_chart(result["question"], result["sql"], df)

            st.subheader("Chart")
            display_chart_result(df, spec)

            store.save_chart(result["question"], result["sql"], spec)
            st.toast("Saved to your charts", icon="✅")

            st.subheader("Data")
            st.dataframe(df, use_container_width=True)


def render_chart_grid(charts: list[dict], cols_per_row: int = 2, show_delete: bool = False) -> None:
    """Render a grid of chart cards: name, a live-refreshed chart (its
    SQL re-runs against DuckDB right here), and an optional delete
    button. Shared by "Saved Charts" and the dashboard view below --
    both are "show these charts in a grid," they just differ in which
    subset of charts and whether delete makes sense.
    """
    for row_start in range(0, len(charts), cols_per_row):
        row = charts[row_start : row_start + cols_per_row]
        cols = st.columns(cols_per_row)
        for col, chart in zip(cols, row):
            with col, st.container(border=True):
                st.markdown(f"**{chart['name']}**")
                if chart["name"] != chart["question"]:
                    st.caption(chart["question"])

                try:
                    df = run_sql(chart["sql"], con)
                    display_chart_result(df, chart["chart_spec"])
                except (SQLValidationError, QueryExecutionError) as e:
                    st.error(f"Couldn't refresh this chart: {e}")

                if show_delete and st.button("Delete", key=f"delete_{chart['id']}"):
                    store.delete_chart(chart["id"])
                    st.rerun()


def render_saved_charts_view() -> None:
    st.title("Saved Charts")
    charts = store.list_charts()

    if not charts:
        st.info("No charts saved yet -- ask a question first and it'll show up here.")
        return

    st.caption(f"{len(charts)} saved chart(s). Each re-runs its query on load, so data stays current.")
    render_chart_grid(charts, show_delete=True)


def render_dashboards_view() -> None:
    st.title("Dashboards")
    charts = store.list_charts()
    dashboards = store.list_dashboards()

    if not charts:
        st.info("No saved charts yet -- ask a question first, then come back to build a dashboard.")
        return

    button_label = "Rebuild Dashboard" if dashboards else "Build Dashboard"
    if st.button(button_label, type="primary"):
        with st.spinner("Grouping your charts with Claude..."):
            groups = group_charts(charts)
            dashboards = store.save_dashboards(groups)

    if not dashboards:
        st.info(f'Click "{button_label}" to have Claude group your saved charts into pages automatically.')
        return

    # A chart can be deleted from "Saved Charts" after a dashboard was
    # built around it -- skip ids that no longer resolve instead of
    # crashing on a stale reference.
    charts_by_id = {c["id"]: c for c in charts}

    def render_page(page: dict) -> None:
        page_charts = [charts_by_id[cid] for cid in page["chart_ids"] if cid in charts_by_id]
        if not page_charts:
            st.caption("No charts on this page.")
        else:
            render_chart_grid(page_charts)

    if len(dashboards) == 1:
        st.subheader(dashboards[0]["title"])
        render_page(dashboards[0])
    else:
        tabs = st.tabs([d["title"] for d in dashboards])
        for tab, page in zip(tabs, dashboards):
            with tab:
                render_page(page)


with st.sidebar:
    view = st.radio("View", ["Ask a Question", "Saved Charts", "Dashboards"], key="view")

    st.divider()
    st.subheader("Try an example")
    for q in EXAMPLE_QUESTIONS:
        st.button(q, on_click=_set_question, args=(q,), use_container_width=True)

    st.divider()
    st.subheader("Available data")
    for table, columns in schema.items():
        st.markdown(f"**{table}**")
        st.caption(", ".join(name for name, _ in columns))

if view == "Ask a Question":
    render_ask_view()
elif view == "Saved Charts":
    render_saved_charts_view()
else:
    render_dashboards_view()
