# Security

Priority: **SECURITY > STABILITY > REPRODUCIBILITY > DOCUMENTATION > CONVENIENCE**. The GitHub repository is private, and that changes nothing: personal financial data never enters it.

## Never in the repository

| Category | Examples | Control |
|---|---|---|
| Transactions / exports | MoneyPro CSV/XLSX exports | `.gitignore` (`*.csv`, `*.xlsx`) |
| Control totals | MoneyPro Control Totals workbooks | `*.xlsx` |
| Reports & dashboards | Dashboard PDFs, Net Worth Tracker PDFs, generated reports | `*.pdf` |
| Screenshots | MoneyPro Word closes, images | `*.docx`, `*.png`, `*.jpg` |
| Power BI files | `.pbix`, `.pbit` | ignored |
| Results JSON | `report_data_*.json`, `regression/`, `work/`, DAX results, validations | `*.json` is denied by default; `regression/` and `work/` are ignored |
| Secrets | `.env`, tokens, keys, certificates | `.env*`, `*.pem`, `*.key`, `*.pfx` |
| Logs | `*.log` | ignored |

`.gitignore` uses an **allowlist**: everything at the root is ignored except explicitly listed code/docs. A new data file is excluded unless someone deliberately allows it.

## `.env`

- `.env` lives next to `powerbi_rest_client.py`, is git-ignored and holds identifiers only (`POWERBI_DATASET_ID`, `POWERBI_TENANT_ID`, optional paths).
- `.env.example` is versioned with placeholders, never real values.
- No passwords, client secrets or tokens are ever written to `.env`.

## Azure / Power BI authentication

- Delegated authentication through the **existing Azure CLI session** (`az account get-access-token`).
- The access token lives in memory for the run only. It is never written to disk, JSON, logs, PDF or Notion (`common.scrub()` removes secret-looking keys before any JSON is saved).
- The pipeline never runs `az login` itself and never asks for passwords. When the session is invalid it stops with `AUTHENTICATION_REQUIRED`.
- Power BI access is **read-only** (`executeQueries` + refresh history GET). No model, measure, table, relationship or refresh operations.

## Identifiers

Dataset, tenant and workspace IDs and Notion page/database IDs are not credentials, but they identify private resources. They stay in `.env` / local configuration, never in code, docs or commit messages.

## Financial data in code and docs

- No real amounts, account names, counterparties, file names of real closes or personal paths in code, tests, docs or commit messages.
- Tests that need real data read **private fixtures** from git-ignored locations and are reported as SKIPPED when absent.
- Examples in `examples/` are synthetic.

## Before every commit

```
python scripts/repo_safety_check.py
```

It fails on forbidden extensions, ignored folders, `.env`, GUID-like identifiers, tokens/JWT, bearer strings, personal absolute paths, e-mail addresses and large currency-formatted amounts in versioned files. Fix or exclude; never bypass.

## If something sensitive was committed

Do not push. Remove it from the index (`git rm --cached <file>`), amend the commit, re-run the safety check. If it was already pushed, rotate whatever can be rotated and rewrite history (e.g. `git filter-repo`) before anyone clones it.
