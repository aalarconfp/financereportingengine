# Architecture

## Layers

| Layer | Components | Responsibility |
|---|---|---|
| **Data** | MoneyPro (transactions + month-close donut screenshots), Power BI semantic model (Cash Basis), Net Worth Tracker (market values) | Sources of record. Never modified by the engine. |
| **Integration** | `powerbi_rest_client.py` (Power BI REST `executeQueries`, refresh history), Azure CLI delegated session | Read-only transport. Token kept in memory only. |
| **Deterministic** | `dax_queries.md` (Q0–Q8), `pipeline/dax_bridge.py`, `pipeline/validate.py`, `pipeline/moneypro_control.py` | Queries, identities, reconciliation, independent liquidity runway, data quality. Same input → same output. |
| **Reporting** | `engine.py`, `rlib.py`, `charts.py`, `pipeline/regression.py`, QA in `pipeline/pipeline.py` | Presentation of already-validated `report_data`; gold-standard regression; QA. |
| **Governance** | `pipeline/readiness.py`, `pipeline/pipeline.py` (state machine), Human Confirmation, Human Release, Notion | Decide whether each stage may run; humans confirm ambiguities and approve publication. |

## Flow

```
readiness ─► moneypro control ─► dax_bridge (REST) ─► validate ─► [regression, once] ─► assemble report_data
          ─► engine.py (PDF) ─► qa ─► HUMAN RELEASE ─► Notion ─► complete
```

The engine (`engine.py`) never queries Power BI, never changes data and refuses to run unless the pipeline state is `READY_FOR_REPORT`.

## Power BI transport

- **REST is primary.** `dax_bridge.py run` resolves Q0–Q8 from `dax_queries.md` (placeholders `{Y} {M} {PY} {PM} {EOM} {PEOM} {SOY}`, `{D}` → EOM/PEOM variants) and executes them through the REST client.
- **Power BI Desktop is not required** for the REST flow. The local Desktop connector (`plan` + `ingest`) is an optional fallback with the same DAX text.
- **Azure CLI session is reused.** `az login --allow-no-subscriptions` is only needed when the session is invalid (`AUTHENTICATION_REQUIRED`). A missing Azure subscription is not a problem.
- **Retry/backoff:** only transient errors (DNS, timeout, connection, HTTP 429/500/502/503/504, Azure CLI without network) are retried — max 3 attempts, waits 1 s and 2 s, every attempt logged. HTTP 400/401/403, DAX errors and permissions are never retried. Exhausted retries ⇒ `BLOCKED — POWER BI TRANSPORT`, never a data-quality finding. `--retry-failed` re-runs only failed queries.
- **Q0** (`INFO.MEASURES`) may be `UNAVAILABLE_VIA_REST`; it never blocks. **Q1–Q8 are the operational core**: any of them missing blocks validation.

## Gates

| Gate | Blocks when | Passes with |
|---|---|---|
| Readiness | critical input missing, unreadable End Period, export older than the close, no Power BI transport | warnings (known), human confirmation only for real ambiguity |
| MoneyPro control | End Period ≠ close, donut totals ≠ Excel, monthly view instead of cumulative close | inherited End Period, new/not-seen categories (reported) |
| Validation | query unavailable after retries, authentication, wrong period, broken identity, model older than export | `REQUIRES_REVIEW` (documented findings), `PASS_WITH_WARNINGS` |
| Regression | validation BLOCKED, any difference vs gold standard | — |
| Report | state ≠ `READY_FOR_REPORT` | — |
| Notion | state ≠ `QA_PASSED` or no Human Release | — |

## Design decisions

- **Single source per responsibility:** DAX only in `dax_queries.md`; REST only in the client; presentation only in the engine.
- **No financial logic in presentation.** The engine formats; validation decides.
- **Transport ≠ data.** A network failure can stop the pipeline but can never produce or alter a financial conclusion.
- **Cumulative closes.** MoneyPro month-close donuts are snapshots accumulated to their End Period, never monthly movements.
