# Personal Finance Reporting Engine

Technical constitution for Claude Code working in this repository. Read it before any change.

## Purpose

Automated, controlled monthly financial reporting (personal finance, Cash Basis, Historical Cost):

```
MoneyPro → Power BI → Power BI REST API → DAX (Q0–Q8) → Validation → Report Engine → QA → Human Release → Notion
```

The deterministic code calculates and validates. Language models may interpret validated data; they never produce financial values.

## Architecture Principles

1. Power BI REST is the primary transport.
2. Power BI Desktop is an optional fallback only.
3. `dax_queries.md` is the ONLY source of production DAX. Never copy DAX into Python.
4. Do not create new DAX measures unless explicitly requested.
5. Do not modify the Power BI model unless explicitly requested (the pipeline is read-only).
6. Financial calculations must remain deterministic.
7. LLMs/agents may interpret validated data but must not invent financial values.
8. Validation failures must never be silently ignored.
9. Transport errors must be distinguished from financial/data errors.
10. PDF generation requires the appropriate readiness and validation gates.
11. Notion publication requires Human Release.
12. Never store credentials or tokens.
13. Never commit personal financial data.
14. Never push to GitHub without explicit user authorization.
15. Run tests after code changes.
16. Do not modify financial methodology without explicit approval.

## Repository map

| Path | Role |
|---|---|
| `dax_queries.md` | Q0–Q8 read-only DAX library (single source) |
| `powerbi_rest_client.py` | Read-only Power BI REST client (Azure CLI session; `.env` for IDs) |
| `pipeline/readiness.py` | Data / Human-in-the-Loop Readiness gate |
| `pipeline/moneypro_control.py` | MoneyPro control totals: extract, update (new file), validate |
| `pipeline/dax_bridge.py` | dax_queries.md → plan → REST (retry/backoff) or connector ingest |
| `pipeline/validate.py` | Identities, independent runway, reconciliation, data quality → gate |
| `pipeline/regression.py` | September 2026 gold-standard regression (data + PDF + QA) |
| `pipeline/pipeline.py` | State machine: status, confirm, assemble, qa, release, complete, certify |
| `engine.py`, `rlib.py`, `charts.py` | Presentation only: report_data → PDF (refuses without READY_FOR_REPORT) |
| `pipeline/tests/run_tests.py` | Offline test suite (no network, no Power BI, no Notion) |
| `docs/` | Architecture, methodology, operations, security, Claude Code workflow, roadmap |

Private data (never in the repo, never read unless the task needs it): the parent data folder (`FINANCE_DATA_ROOT`), `regression/`, `work/`, `report_data_*.json`, `.env`.

## Development Workflow

Before modifying:
- inspect;
- explain plan;
- identify affected files.

After modifying:
- run tests: `python pipeline/tests/run_tests.py` (must not FAIL; SKIPs only when private fixtures are absent);
- run `python scripts/repo_safety_check.py`;
- inspect diff (`git diff`);
- summarize changes;
- identify risks.

Do not commit automatically unless explicitly instructed. Never `git push`, `git remote add` or `gh repo create` without explicit authorization. Never `git add .` — add explicit paths.

## Hard limits for Claude Code

- Do not modify Power BI (no measure/table/relationship writes, no refresh).
- Do not publish or write to Notion.
- Do not handle, print, log or persist credentials or tokens.
- Do not open personal financial files (CSV, Excel, PDF, Word, regression JSON) unless the task strictly requires it; never copy their contents into code, docs, tests or commit messages.
- Do not change financial methodology (Cash Basis, Historical Cost, runway definition, reconciliation classes) or create DAX without authorization.
- Do not weaken a gate or a test to make it pass.

## Status vocabulary (do not rename)

Readiness: PASS · PASS_WITH_WARNINGS · WARNING · HUMAN_CONFIRMATION_REQUIRED · REQUIRES_REVIEW · BLOCKED
Queries: OK · RETRY_SUCCESS · TRANSPORT_ERROR · AUTHENTICATION_ERROR · DAX_ERROR · UNAVAILABLE_VIA_REST
Validation: PASS · PASS_WITH_WARNINGS · REQUIRES_REVIEW · BLOCKED
Pipeline: BLOCKED · READY · RUNNING · VALIDATION · READY_FOR_REPORT · REPORT_GENERATED · QA_PASSED · COMPLETED · FAILED
