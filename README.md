# DealerBRAIN

**Agentic After-Sales Stock Rescue Copilot** — turn natural-language after-sales questions into evidence-backed decisions, warehouse fulfillment plans, and human-confirmed POC actions.

## Problem

After-sales teams must reconcile purchase orders, stock shortages, dealer eligibility, claims, and operating procedures across disconnected records. A useful answer needs both an actionable plan and traceable evidence.

## Solution

DealerBRAIN combines a Streamlit chat copilot with controlled deterministic tools. It extracts structured intent, checks operational facts, plans network fulfillment, retrieves relevant Knowledge guidance, and explains results with source citations. Guided checks remain available without configured AI.

## Why DealerBRAIN Is Different

- **Facts before prose:** deterministic tools establish business facts; the LLM interprets requests and explains verified results.
- **Honest fulfillment:** allocations use authoritative warehouse/location inventory, with no fabricated warehouse-to-dealer ownership mapping.
- **Grounded guidance:** deterministic lexical retrieval searches the operational Knowledge table without embeddings or a vector database.
- **Human control:** proposed fulfillment requires explicit confirmation; duplicate submissions reuse the same simulated action ID.

## Golden Demo Workflow

Ask **“Can PO-2026-1026 be fulfilled?”** and inspect the fulfillment plan and evidence:

**20 requested → 4 network available → Pune 3 + Frankfurt 1 → 4 planned → 16 unresolved → human-confirmed POC simulation.**

The authoritative sources are `WH-IN-PUN` and `WH-EU-FRA`. Review the proposal, then select **Confirm simulated fulfillment**. The initial action returns `CREATED`; a duplicate returns `ALREADY_EXISTS` with the same action ID. Nothing has physically shipped or transferred.

For governance, ask **“Process PO-2026-1106”**: the PO is blocked because dealer **D007 is suspended**. Ask **“What is the procedure for a suspended dealer?”** to retrieve grounded Knowledge guidance.

## Architecture

```text
Streamlit chat → structured intent → allowlisted router → deterministic tools
                                                        ├─ rules / governance
                                                        ├─ network fulfillment planner
                                                        └─ Knowledge retrieval
                                                                  ↓
                                                        repositories → DuckDB
                                                                  ↓
                                         grounded response + evidence trace

Explicit human confirmation → revalidation → SQLite POC action store
```

A provider-neutral, OpenAI-compatible LLM adapter handles interpretation and explanation. Excel ingestion validates and fingerprints the supplied dataset; DuckDB serves operational reads, while SQLite stores separate simulated actions.

## Safety & Governance

- The LLM does not calculate authoritative business facts or authorize transactions.
- Every fulfillment action requires explicit human confirmation and deterministic revalidation.
- **POC simulated actions do not modify ERP, physical inventory, shipments, or source PO data.**
- `ANSWER_KEY` and workbook `README` are excluded from runtime operational access.
- Repositories expose fixed, parameterized read operations, not arbitrary SQL.
- Anomaly and governance checks preserve source inconsistencies rather than silently correcting them.
- Rendering does not trigger LLM calls or business writes. A submitted copilot request uses at most one intent call and one response call, with no retries.
- Secrets stay environment-only; never commit `.env` or credentials. Provider failures use safe messages and deterministic response fallbacks.

See [PROJECT_RULES.md](PROJECT_RULES.md) for engineering constraints.

## Evidence & Decision Trace

The right-side panel preserves deterministic outcomes, invoked tools/checks, issue codes, source tables, record IDs, and POC action audit details. Citations come from tool results, never from LLM prose. Composite PO keys are formatted for readability without changing their underlying identifiers. No chain-of-thought or invented confidence scores are shown.

## Tech Stack

Python 3.11+, Streamlit, DuckDB, SQLite, Excel ingestion, standard-library lexical retrieval, and an OpenAI-compatible HTTP LLM adapter. Pytest and Ruff support validation; GitHub Actions runs them for pull requests and pushes to `main`.

## Quick Start

From the repository root:

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt -r requirements-dev.txt
cp .env.example .env  # First setup only; preserve existing settings.
```

Place the supplied workbook from `After Sales.zip` at `data/after_sales.xlsx`, then:

```sh
python data_loader.py
streamlit run app.py
```

The app also initializes data when needed. Missing or invalid source data produces an error, not replacement data. Runtime databases are Git-ignored.

`LLM_ENABLED=false` keeps guided deterministic checks usable. For chat, configure `LLM_ENABLED`, `LLM_API_KEY`, `LLM_BASE_URL`, and `LLM_MODEL` using `.env.example`; retain the configured timeout and output-token limits. Provider URLs require HTTPS, except HTTP loopback development at `localhost`, `127.0.0.1`, or `::1`. Local Ollama can use `http://localhost:11434/v1` with the existing compatible adapter. No provider endpoint or pricing is assumed; “Configured” indicates settings availability, not verified connectivity.

## Demo Readiness

```sh
python scripts/demo_smoke.py
python evaluation.py
```

Recorded demo results: **Demo smoke: 7/7**; **live local Qwen golden evaluation: 10/10**. These are observed results, not guarantees for every model or configuration. Live evaluation requires configured AI and may contact its endpoint.

For repository quality checks, run `pytest` and `ruff check .`. Dataset Health, system diagnostics, session history, and recent POC actions remain available in secondary UI panels.

## EC2 Hackathon Deployment

On Ubuntu EC2 with Python 3.11+, clone the team's repository using its actual Git URL, or run `git pull` in an existing checkout. From the repository root:

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env  # First setup only; preserve an existing local .env.
```

Keep `LLM_ENABLED=false` until organizer/provider details are supplied. Configure the base URL, model, and credential locally when available; non-loopback providers require HTTPS. No organizer endpoint or pricing is assumed. The commented Ollama example is for local development; this launch does not start Ollama. Never commit credentials.

Ensure `data/after_sales.xlsx` exists, then run:

```sh
python scripts/preflight.py
python data_loader.py
./scripts/run_ec2.sh
```

Preflight checks local prerequisites; ingestion generates runtime DuckDB data. The foreground launch binds `0.0.0.0:8501`; use `PORT=8502 ./scripts/run_ec2.sh` for another port. Access `http://<EC2-host>:8501` subject to hackathon and security-group rules. Runtime databases remain local and Git-ignored. Public or production exposure needs proper TLS and a reverse proxy, outside this POC deployment setup.

## Project Structure

| Area | Files |
| --- | --- |
| Copilot presentation | `app.py`, `ui.py`, `ui_models.py` |
| Intent, routing, explanation | `intent.py`, `router.py`, `responder.py`, `prompts.py`, `llm_client.py` |
| Deterministic operations | `tools.py`, `rules.py`, `fulfillment.py`, `rag.py` |
| Data and POC actions | `models.py`, `repositories.py`, `database.py`, `data_loader.py`, `data_validation.py`, `transactions.py` |
| Configuration and verification | `config.py`, `evaluation.py`, `scripts/`, `tests/` |
| Source and runtime storage | `data/`, `runtime/` |

## Team Stock Overflow

Built by **Team Stock Overflow** as a hackathon POC for evidence-grounded after-sales decisions and human-controlled stock rescue.
