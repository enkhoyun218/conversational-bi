"""Streamlit UI: the self-service front door to the whole pipeline.

question -> question_to_sql() -> validate_sql() -> run_sql() -> suggest_chart()
-> render_chart(), all wired together in query.answer_question() /
chart.suggest_chart(). This file is presentation only -- no pipeline logic
lives here, so the same functions this app calls are independently testable
(see the __main__ blocks in db.py, llm.py, validate.py, query.py, chart.py).
"""
import anthropic
import streamlit as st

from chart import render_chart, suggest_chart
from db import get_connection, get_schema
from query import answer_question

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


st.title("\U0001F4CA Conversational BI")
st.caption(
    "Ask a plain-English question about the pension/disability dataset. "
    "Claude translates it to SQL, DuckDB runs it, and the result is charted automatically."
)

with st.sidebar:
    st.subheader("Try an example")
    for q in EXAMPLE_QUESTIONS:
        st.button(q, on_click=_set_question, args=(q,), use_container_width=True)

    st.divider()
    st.subheader("Available data")
    for table, columns in schema.items():
        st.markdown(f"**{table}**")
        st.caption(", ".join(name for name, _ in columns))

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

        st.subheader("Data")
        st.dataframe(df, use_container_width=True)
