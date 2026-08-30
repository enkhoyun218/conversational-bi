# Conversational BI

**Live demo: [conversational-bi-deploy.streamlit.app](https://conversational-bi-deploy.streamlit.app/)**

Ask a plain-English question about a dataset and get back the SQL that
answered it, a chart, and the underlying data — no analyst in the loop.

## The problem

In most organizations, "can I see how X breaks down by Y?" turns into a
Slack message to an analyst, a day or two of wait, and a one-off SQL
query that gets thrown away after it answers that one question. The
analyst becomes a bottleneck not because the questions are hard, but
because SQL is the barrier between a business question and an answer.
That bottleneck compounds: every ad-hoc question competes with the
analyst's actual project work, so most of them never get asked at all.

## The solution

This is a small self-service analytics tool: type a question in plain
English, and an LLM (Claude Haiku) translates it into SQL, runs it
against the data, and renders the result as a chart automatically. The
target user is non-technical — someone who knows what they want to know
but not how to write a `JOIN`.

It's deliberately narrow in scope: one dataset, no auth, no saved
dashboards. The goal was to prove the core loop — NL → SQL → guarded
execution → chart — works and is trustworthy enough to hand to someone
who can't read the SQL it generates. The "guarded" part matters as much
as the "works" part; see [Known limitations](#known-limitations) and the
guardrail discussion below.

## Architecture

```
Plain-English question
        |
        v
question_to_sql()   -- Claude Haiku, schema-grounded prompt -> raw SQL
        |
        v
validate_sql()      -- guardrail: single statement, SELECT/WITH only,
        |               DuckDB's own binder (via EXPLAIN) confirms every
        |               referenced table/column actually exists
        v
run_sql()           -- DuckDB executes against CSV-backed views
        |               (the simulated "data lake")
        v
suggest_chart()     -- Claude Haiku again: chart_type + x/y/color + title,
        |               validated against the actual result columns
        v
render_chart()      -- Plotly figure, or st.metric() / st.dataframe()
        |               for results a chart doesn't fit
        v
Streamlit UI: shows the SQL, the chart, and the data table
```

Two separate Claude Haiku calls happen per question — one to write the
SQL, one to pick a chart — because they're different, independently
verifiable decisions. A single combined prompt would save one round
trip but couple two things that fail differently: a bad SQL query and a
bad chart choice need different guardrails, and keeping them as two
calls made this obvious while building it.

### Design decisions worth calling out

- **DuckDB queries the CSVs directly (as views), not as imported
  tables.** `CREATE VIEW employees AS SELECT * FROM read_csv_auto(...)`
  means the CSVs stay the single source of truth, mirroring how a real
  data lake is queried — files on disk, read at query time, no ETL step
  to keep in sync.
- **Trust-but-verify on every LLM output, applied consistently.** The
  SQL guardrail validates generated SQL against the real schema before
  execution (via `EXPLAIN`, DuckDB's own binder — not a hand-rolled SQL
  parser, which would be fragile against aliases/CTEs/subqueries). The
  chart-suggestion step applies the same pattern: the model's suggested
  `x`/`y`/`color` columns are checked against the actual result
  dataframe, with a rule-based fallback if they don't match. Neither
  LLM output is ever used blind.
- **Haiku, not a larger model.** Text-to-SQL over a 3-table schema and
  chart-type selection are both simple, well-specified tasks. Using the
  cheapest current Claude model (`claude-haiku-4-5`) keeps a
  self-service tool cheap to run at volume — the whole point is to
  remove a human bottleneck, not trade it for API cost.

### Project structure

```
conversational-bi/
├── data/                  synthetic CSVs (the "data lake")
│   ├── departments.csv
│   ├── employees.csv
│   └── claims.csv
├── src/
│   ├── generate_data.py   builds the synthetic dataset (seeded, reproducible)
│   ├── db.py              DuckDB connection + schema introspection
│   ├── llm.py             question_to_sql() -- NL question -> SQL
│   ├── validate.py        SQL guardrail (safety + hallucination check)
│   ├── query.py           validate -> execute -> dataframe, end to end
│   ├── chart.py           suggest_chart() / render_chart()
│   └── app.py             Streamlit UI
├── requirements.txt
├── .env.example
└── README.md
```

Every module has a `__main__` block that exercises it standalone (e.g.
`python3 src/validate.py` runs it against a hallucinated column, a
`DROP TABLE`, and a real trick question) — the pipeline was built and
verified one stage at a time, not as one big script.

## The dataset

A synthetic public-pension / disability-retirement dataset, seeded for
reproducibility:

- **`departments`** (10 rows) — dimension table.
- **`employees`** (400 rows) — the covered population, with a realistic
  constraint (service credit can't exceed years since age 18).
- **`claims`** (155 rows) — disability applications filed by a *subset*
  of employees (140 of 400), so most useful questions require a `JOIN`.
  ~15% have a null `application_end` (still pending), which exercises
  `NULL` handling end to end.

## Setup

```bash
git clone <this-repo>
cd conversational-bi
python3 -m venv venv
source venv/bin/activate      # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env          # then edit .env and add your Anthropic API key
python3 src/generate_data.py  # regenerate the CSVs (optional -- already committed)
```

## Running it

```bash
streamlit run src/app.py
```

Opens at `http://localhost:8501`. Try one of the sidebar example
questions, or type your own — e.g. *"How many claims were filed per
department?"* or *"What's the breakdown of claims by injury type?"*

## Known limitations

- **Aggregation ambiguity isn't resolved, just executed.** "Average age
  of employees who filed a claim" is genuinely ambiguous — does an
  employee with two claims count once or twice? The model picks an
  interpretation and runs with it; it doesn't ask which one you meant.
  A production version would need the clarifying-questions stretch goal
  below.
- **The read-only keyword check is a blocklist, not a parser.** It scans
  for tokens like `DROP`/`DELETE`/`INSERT` anywhere in the query text,
  so a query with a string literal containing one of those words would
  be rejected as a false positive. Fails closed (blocks something safe)
  rather than open (runs something dangerous), which is the right
  trade-off for an MVP, but a real implementation would parse the query
  properly (e.g. with a SQL AST library) instead of scanning tokens.
- **No conversation memory.** Every question is independent — there's
  no "and by year?" follow-up that builds on the previous query. Each
  question re-sends only the schema, not prior questions or results.
- **No caching.** Two Haiku calls run on every question even if it's a
  near-duplicate of a previous one. Fine at demo scale; a real
  deployment would cache by normalized question text.
- **Single local user, no auth.** This runs on `localhost` with one
  DuckDB connection per Streamlit session. There's no multi-tenant
  isolation, and it's not meant to be deployed publicly as-is.
- **Simulated data lake.** The brief was explicit about this: CSVs
  standing in for cloud storage, not a real Parquet-on-S3 setup. The
  DuckDB access pattern (querying files directly) generalizes to that
  case, but nothing here talks to real cloud infrastructure.

## Stretch goals not implemented

Given the time box, these were left out:

- Clarifying questions for ambiguous requests (rather than picking one
  interpretation silently, as noted above).
- A dedicated "what data is available?" conversational helper — the
  sidebar's static schema listing covers the same need at a fraction of
  the complexity.
