# DealerBRAIN

**Agentic After-Sales Stock Rescue Copilot** · Team Stock Overflow

A hackathon POC foundation for an after-sales stock rescue copilot, structured
for readable, testable business tools and replaceable data adapters.

## Architecture

Streamlit UI → Application / Orchestrator → Safe Business Tools → Rules /
Guardrails → Repository / Data Access → DuckDB / SQLite.

The LLM is an orchestration and explanation layer. It is not the system of record.
See [PROJECT_RULES.md](PROJECT_RULES.md) for the engineering rules.

`app.py` provides the UI; `config.py` loads environment settings and defines
branding and paths. `data_loader.py` reads and fingerprints Excel,
`data_validation.py` reports structural errors and preserved anomalies, and
`database.py` separates atomic ingestion writes from read-only access.
`tools.py`, `rules.py`, `workflows.py`, `llm_client.py`, and `rag.py` reserve future layers.
SQLite is included in Python's standard library and needs no extra package.

## Local setup

Requires **Python 3.11+**. From the repository directory:

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt -r requirements-dev.txt
cp .env.example .env
```

The environment values can remain blank for this foundation. Keep real secrets
in environment variables and never commit them.

Run:

```sh
streamlit run app.py
```

Test:

```sh
pytest
```

Lint:

```sh
ruff check .
```

## Current POC status

Application, ingestion, repositories, and deterministic business rules are implemented.
AI and workflow layers remain
pending; this step makes no LLM/API calls or business recommendations.

Place the supplied workbook at `data/after_sales.xlsx` (the Excel file from
`After Sales.zip`). Initialize explicitly with:

```sh
python data_loader.py
```

The app also initializes the data layer. A missing/unreadable workbook or invalid
schema produces an actionable error; no replacement data is generated.
The eight operational sheets load into `runtime/dealerbrain.duckdb`.
`README` and `ANSWER_KEY` are never read as operational data or loaded into DuckDB.
The workbook remains unchanged. Source files in `data/` remain trackable;
runtime files are ignored.

Duplicate keys, unknown references, negative quantities, EUR currency mismatches,
and inconsistent inventory quantities produce warnings without correction.
Inventory is warehouse-based. Purchase-order uniqueness uses PO number plus line.
SHA-256, source filename, UTC ingestion time, schema, row counts, and validation
issues are stored in a separate metadata table. Identical validated source snapshots
reuse the existing database; changed snapshots replace operational tables atomically.
Future business tools and orchestration must retrieve operational records only
through `Repository` in `repositories.py`. `database.py` remains infrastructure
for ingestion, connection lifecycle, and technical verification.

Repository methods return `RepositoryResult(data, evidence)` with immutable
source-row models. Single-record misses return `data=None`; list misses return
`data=[]`, both with empty evidence. `get_purchase_order` returns all PO lines.
Evidence uses the source table and business key; PO evidence encodes
`[po_no, po_line_no]` as JSON to distinguish lines. Dates and blank strings retain
their source representation, and database nulls remain `None`.

Lookups use fixed, parameterized SELECTs through read-only connections. There is
no raw SQL or arbitrary-table repository API. Database/schema failures and
ambiguous duplicate single-record keys raise `RepositoryError`; ordinary missing
records do not. Repository tests use temporary synthetic databases and run with
the existing `pytest` command. No new dependencies are needed.

## Deterministic business rules

`RulesEngine` in `rules.py` evaluates stock, dealer/part ordering eligibility,
purchase orders, claim/shipment consistency, warranty dates, and data-quality
anomalies. Results contain a status, immutable facts, structured issues, and
operational evidence. Database failures still raise repository errors.

`tools.py` exposes `check_po`, `check_stock`, `check_dealer`, `check_part`,
`check_claim`, `check_warranty`, and `scan_anomalies`. Input validation is shared
with rule entry points. These endpoints retrieve facts only through repositories
and perform no transactions or network/LLM calls.

- Stock uses explicit `available_qty` across warehouses, preserving negatives.
  Missing/invalid inventory blocks calculation; shortages and negatives warn.
  PO demand for the same part is summed across lines before comparing availability.
- Active dealers/parts pass ordering eligibility. Suspended/inactive dealers and
  discontinued/inactive parts block. Unknown dealer status blocks; unknown part
  status warns. PO checks assess current new-order eligibility, without modifying
  historical orders. Scanner eligibility flags apply to Open, Confirmed, and
  Backordered orders only.
- PO unit prices and quantities must be positive. Currency mismatches against
  EUR-named price columns only warn; there is no supported-currency policy.
- Claims resolve shipments through PO lines matching both dealer and part.
  Missing/inconsistent links and Not Received/Delivered contradictions warn;
  they do not establish fraud or reject claims.
- Warranty uses purchase date plus calendar months, clamping month-end dates
  to the last valid day and including the calculated end date. This is a date-window
  convention, not claim approval. Missing/invalid inputs return NOT_APPLICABLE;
  a claim before purchase blocks; dates outside the window warn.

Run all rules and regression tests with `pytest`.

## Step 5 command center

Run `streamlit run app.py` from the activated virtual environment. The guided UI
supports PO, stock, dealer, part, and claim checks plus operational risk scans.
Demo Scenarios populate inputs only; press **Run check** to evaluate real data.

**Evidence & Decision Trace** shows the invoked tool, deterministic check outcomes,
issue codes, source tables, and record keys. It is factual provenance, not hidden
reasoning or chain-of-thought. No confidence score is invented.

Dataset Health remains available alongside tool availability. Session History
keeps the last 20 checks in memory only and clears when the dataset changes.
AI/LLM orchestration is not enabled. No write transactions or business actions
are enabled. The UI delegates decisions to existing tools and rules; pure
presentation helpers are tested without brittle browser automation tests.

GitHub Actions installs runtime and development dependencies on Python 3.11,
then runs Ruff and pytest for pull requests and pushes to `main`.

## Step 6 optional LLM adapter

The deterministic command center works without `.env` or LLM credentials.
`LLM_ENABLED=false` is the default. The UI reports Disabled, Not Configured, or
Configured from local settings only; Configured does not mean provider connectivity
has been tested. AI orchestration remains pending and no UI action invokes the LLM.

Use the placeholders in `.env.example` to configure `LLM_ENABLED`, `LLM_API_KEY`,
`LLM_BASE_URL`, `LLM_MODEL`, `LLM_TIMEOUT_SECONDS`, `LLM_MAX_OUTPUT_TOKENS`, and
`LLM_TEMPERATURE`. Never commit `.env` or API keys. Credentials remain environment-only.
The API base URL must use HTTPS; the adapter appends `/chat/completions` and sends
OpenAI-compatible text messages with Bearer authentication. All provider assumptions
stay in `llm_client.py`. No public endpoint is selected automatically.

Only an explicit `LLMClient().chat(messages)` call can contact the endpoint.
There are no startup probes, retries, redirects, or proxy forwarding. Each request
has a configured socket timeout, an output-token ceiling, a 32,000-character input
limit, and a 2 MB response limit. Provider/network failures map to safe adapter
exceptions without response bodies, credentials, or request details.

For offline development, instantiate `FakeLLMClient(text="Configured mock text")`.
It returns deterministic text and optional configured token counts without HTTP.
Tests intercept HTTP and prohibit external connections; they never spend LLMaaS budget.
The fake is explicit and is never silently substituted for a failed real request.

`client.usage.snapshot()` exposes in-memory attempt/success counts, provider token
totals, and estimated spend. Missing usage stays unavailable, rather than becoming
zero. Trackers retain counters only, not prompts or generated text. Real clients
share a process tracker by default; fake clients have isolated trackers.

Pricing is optional: set both `LLM_INPUT_PRICE_PER_MILLION` and
`LLM_OUTPUT_PRICE_PER_MILLION` only when authoritative rates are known. Without
prices, estimated spend is unavailable and `LLM_BUDGET_USD` cannot enforce a dollar
ceiling. With prices and a budget, calls are serialized and further requests stop
once reported spend reaches the soft ceiling. One request can exceed that ceiling;
this is not a prepaid/account-level cap. Missing usage or failed requests make
accounting uncertain and block further budget-controlled priced calls. Process
restart clears counters; provider billing remains authoritative.

`prompts.py` supplies concise evidence-grounded explanation instructions. The
adapter has no SQL executor, tool execution, routing, RAG, business decisions, or
transaction capability. Model text is not treated as an authoritative business fact.
