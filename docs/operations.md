# Operations — monthly run

All commands run on the PC from the repository folder (PowerShell). `<k>` = `YYYY_MM`, `<P>` = `YYYY-MM`, `<DATA>` = private data root (`FINANCE_DATA_ROOT`, by default the parent folder of the repository).

## Prerequisites

- Python ≥ 3.10 and `pip install -r requirements.txt`.
- Azure CLI installed, with a valid session. Run `az login --allow-no-subscriptions` **only** if the pipeline reports `AUTHENTICATION_REQUIRED`.
- `.env` created from `.env.example` (`POWERBI_DATASET_ID` required).
- Fonts: Calibri (Windows) or Carlito (Linux).
- Power BI Desktop is **not** required (optional fallback only).

## Monthly sequence

| # | Step | Who | Command / action |
|---|---|---|---|
| 1 | MoneyPro export | user | Export transactions after the close (CSV) into `<DATA>\Transacciones Money Pro\` |
| 2 | Power BI refresh | user | Refresh the model and publish/refresh the Service dataset |
| 3 | Inputs | user | Dashboard PDF, Net Worth Tracker PDF, MoneyPro Word with the 4 donuts of the close |
| 4 | DAX Q0–Q8 (REST) | engine | `python pipeline\dax_bridge.py run --period <P> --dax dax_queries.md --out work\<k>\dax_results_<k>.json` |
| 5 | MoneyPro control | Claude + engine | `python pipeline\moneypro_control.py extract-media --docx "<Word>" --to work\<k>\moneypro_media` → donut reading into `work\<k>\donuts_<k>.json` → `python pipeline\moneypro_control.py update --period <P> --donuts work\<k>\donuts_<k>.json --update-from "<previous close xlsx>" --write "<new close xlsx>" --out work\<k>\moneypro_update_<k>.json` → `python pipeline\moneypro_control.py --period <P> --donuts work\<k>\donuts_<k>.json --excel "<new close xlsx>" --out work\<k>\moneypro_control_<k>.json` |
| 6 | Data Readiness | engine | `python pipeline\readiness.py --period <P> --scan "<DATA>" --powerbi rest --dax-results work\<k>\dax_results_<k>.json --moneypro work\<k>\moneypro_control_<k>.json --notion MISSING --out work\<k>\readiness_<k>.json` |
| 7 | Validation | engine | `python pipeline\validate.py --period <P> --results work\<k>\dax_results_<k>.json --moneypro work\<k>\moneypro_control_<k>.json --export "<latest export csv>" --out work\<k>\validations_<k>.json` |
| 8 | State | engine | `python pipeline\pipeline.py status --period <P> --work work\<k>` → must be `READY_FOR_REPORT` |
| 9 | Analysis | Claude | Write `analysis_<k>.json` (only validated figures) → `python pipeline\pipeline.py assemble --period <P> --work work\<k> --analysis <file>` |
| 10 | Report | engine | `python engine.py work\<k>\report_data_<k>.json "<dashboard pdf>" "<output pdf>"` |
| 11 | QA | engine | `python pipeline\pipeline.py qa --period <P> --work work\<k> --pdf "<output pdf>" --dashboard "<dashboard pdf>"` |
| 12 | Human Release | user | Review the PDF, then `python pipeline\pipeline.py release --period <P> --work work\<k> --by "<name>"` |
| 13 | Notion | Claude (authorized) | Write the Monthly Financial Review, re-read, then `python pipeline\pipeline.py complete --period <P> --work work\<k> --notion <verification.json>` |

Regression (once, before the first real month, and after any engine change):

```
python pipeline\dax_bridge.py run --period 2026-09 --dax dax_queries.md --out regression\dax_results_2026_09.json
python pipeline\validate.py --period 2026-09 --results regression\dax_results_2026_09.json --moneypro regression\moneypro_control_2026_09.json --export "<export used for the gold standard>" --out regression\validations_2026_09.json
python pipeline\regression.py --baseline regression\baseline_2026_09.json --validations regression\validations_2026_09.json --engine-dir . --gold report_data_2026_09_GOLD.json --dashboard "<gold dashboard pdf>" --published "<published gold report pdf>" --out-pdf regression\regen_2026_09.pdf --out regression\regression_2026_09.json
python pipeline\tests\run_tests.py
python pipeline\pipeline.py certify --period 2026-09 --work work\2026_09
```

## Recovery

| Situation | What it means | What to do |
|---|---|---|
| `AUTHENTICATION_REQUIRED` | Azure CLI session invalid/expired | `az login --allow-no-subscriptions`, then re-run step 4. No report is produced meanwhile. |
| `TRANSPORT_ERROR` / `BLOCKED — POWER BI TRANSPORT` | DNS/timeout/HTTP 5xx persisted after 3 attempts | Check network, then `python pipeline\dax_bridge.py run --period <P> --dax dax_queries.md --retry-failed work\<k>\dax_results_<k>.json --out work\<k>\dax_results_<k>.json` (only failed queries are re-run). |
| `RETRY_SUCCESS` | A transient failure was resolved by retry | Nothing; validation shows `PASS_WITH_WARNINGS`. |
| `DAX_ERROR` | Non-transient query error (HTTP 400) | Do not retry. Check whether a measure/column used by `dax_queries.md` was renamed; document it. Never edit the model from the pipeline. |
| `UNAVAILABLE_VIA_REST` (Q0) | `INFO.*` not supported by executeQueries | Expected; continue. |
| Validation `BLOCKED` | Missing query, broken identity, model older than the export, wrong period | Fix the cause at the source (refresh, re-export, re-run). Never analyse with missing data; never edit results. |
| Validation `REQUIRES_REVIEW` | Real findings (unexplained reconciliation differences, possible duplicates, export integrity) | The report may be generated; each finding is documented as REQUIERE REVISIÓN. Resolve in MoneyPro when possible. |
| Readiness `HUMAN_CONFIRMATION_REQUIRED` | Real ambiguity (e.g. two plausible files for the same End Period) | Confirm the right input; record with `pipeline.py confirm --check "<check>"`. |
| Regression `FAILED` | Engine/pipeline behaviour changed | STOP. Investigate the diff before any real month. |

## Configuration and paths

| Path / setting | Where | Class | Portable alternative |
|---|---|---|---|
| Power BI dataset / tenant IDs | `.env` | configurable (required) | `POWERBI_DATASET_ID`, `POWERBI_TENANT_ID` |
| Azure CLI executable | `powerbi_rest_client.py` default (standard Windows install path) | necessary default | `AZURE_CLI_PATH` |
| Fonts | `rlib.py` (Carlito/Calibri) | necessary default | `REPORT_FONT_DIR` |
| Private data root (`..\` from the repo) | tests default, command examples | configurable | `FINANCE_DATA_ROOT`, `MP_DIR` |
| `regression\`, `work\`, `report_data_*.json` | repo folder (git-ignored) | private runtime data | keep local; never commit |
| Google Drive personal paths | none in code (only in private outputs) | legacy | — |
