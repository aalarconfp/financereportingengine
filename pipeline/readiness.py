"""STEP 0 — MONTHLY DATA READINESS (Data / Human-in-the-Loop Readiness gate).

Runs BEFORE any financial analysis. Determines automatically everything that can be determined and asks a
human only when there is a genuine ambiguity.

Item / area statuses
  PASS                         verified
  PASS_WITH_WARNINGS           verified, with a known non-blocking warning
  WARNING                      optional input missing / proxy check not conclusive
  HUMAN_CONFIRMATION_REQUIRED  real ambiguity (e.g. two equally plausible files for the same End Period)
  REQUIRES_REVIEW              a real discrepancy someone must look at (does not block the gate by itself)
  BLOCKED                      critical input missing / unusable (never asks a useless confirmation)
Overall
  READY · READY_WITH_WARNINGS · HUMAN_CONFIRMATION_REQUIRED · BLOCKED

Inputs
  --listing  JSON of a recursive listing of 'Modelo Financiero Personal' (Claude: device_list_dir)  | --scan <root> (PC)
  --staged   local root with copies of candidate files (to open Excel / count PDF pages)
  --powerbi  rest (default, PRIMARY) | connector (fallback, Claude ran the test query via Desktop) | MISSING
  --dax-results  dax_results_<k>.json from `dax_bridge.py run` (REST) — reused as evidence, no new call
  --moneypro moneypro_control_<k>.json (area MoneyPro / Control Totals content checks)
  --validation / --regression  optional, only to display their state in the summary
  --notion   READY | MISSING (only required for the final stage)

Power BI REST uses the EXISTING Azure CLI session; it never asks for `az login` unless the session is
invalid (status AUTHENTICATION_REQUIRED). No credentials are stored. A missing Azure subscription is not
a problem (institutional tenant; `az login --allow-no-subscriptions`).
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from common import Period, save_json, load_json, now_iso  # noqa: E402

PASS, PASS_W, WARN = "PASS", "PASS_WITH_WARNINGS", "WARNING"
HUMAN, REVIEW, BLOCKED = "HUMAN_CONFIRMATION_REQUIRED", "REQUIRES_REVIEW", "BLOCKED"
RANK = {PASS: 0, PASS_W: 1, WARN: 2, REVIEW: 3, HUMAN: 4, BLOCKED: 5}

EXPORT_RE = re.compile(r"Money Pro Transactions - (\d{4})_(\d{2})_(\d{2})_(\d{2})_(\d{2})_(\d{2})\.(csv|xlsx)$")
AREAS = ["Dashboard", "NWT", "MoneyPro", "Control Totals", "MoneyPro Export", "Power BI REST", "DAX",
         "Report Engine", "Regression baseline", "Documentation", "Previous report", "Notion"]


def scan(root: str) -> list[dict]:
    out = []
    for dp, _, fns in os.walk(root):
        for fn in fns:
            p = os.path.join(dp, fn)
            st = os.stat(p)
            out.append({"name": os.path.relpath(p, root).replace(os.sep, "/"), "type": "file",
                        "size": st.st_size, "mtimeMs": st.st_mtime * 1000})
    return out


def worst(statuses):
    return max(statuses, key=lambda s: RANK[s]) if statuses else PASS


# ---------------------------------------------------------------------------------------------- Power BI
def powerbi_probe(mode: str, dax_results: str | None, period: Period, client=None, sleep=None, log=print) -> dict:
    """Return {'status','transport','detail','refresh'} without executing any DAX beyond ROW("Test",1)."""
    if dax_results and os.path.exists(dax_results):
        r = load_json(dax_results)
        summ = r.get("summary", {})
        if r.get("transport") == "rest" and r.get("period") == f"{period.year}-{period.month:02d}":
            ov = summ.get("overall") or ("PASS" if summ.get("all_core_ok") else "BLOCKED — POWER BI TRANSPORT")
            if ov == "AUTHENTICATION_REQUIRED":
                return {"status": BLOCKED, "transport": "rest", "detail": "AUTHENTICATION_REQUIRED — ejecutar `az login --allow-no-subscriptions`"}
            if summ.get("all_core_ok"):
                st = PASS_W if summ.get("retried") else PASS
                return {"status": st, "transport": "rest",
                        "detail": f"Resultados REST del período ({r.get('executed')})"
                                  + (f"; obtenidas tras reintento: {', '.join(summ['retried'])}" if summ.get("retried") else "")}
            # results exist but incomplete: REST works; the failed queries are a DAX-stage problem (reported there)
            return {"status": PASS_W, "transport": "rest",
                    "detail": f"REST accesible; consultas pendientes por transporte: "
                              f"{', '.join(summ.get('failed_core') or [k for k, v in summ.get('statuses', {}).items() if v not in ('OK', 'RETRY_SUCCESS') and not k.startswith('Q0')])} "
                              "(reintentar con dax_bridge.py run --retry-failed)"}
    if mode == "connector":
        return {"status": PASS, "transport": "connector", "detail": "Consulta de prueba OK por el conector local (fallback)"}
    if mode != "rest":
        return {"status": BLOCKED, "transport": None, "detail": "Sin transporte Power BI verificado"}
    try:
        import time
        from dax_bridge import execute_with_retry
        if client is None:
            from powerbi_rest_client import PowerBIClient
            client = PowerBIClient()
        out = execute_with_retry(client, {"id": "READINESS_TEST", "dax": 'EVALUATE ROW("Test", 1)', "uses_info_function": False},
                                 sleep=sleep or time.sleep, log=log)
    except Exception as exc:  # import problems etc.
        return {"status": BLOCKED, "transport": None,
                "detail": f"REST no ejecutable en este entorno ({type(exc).__name__}: {str(exc)[:160]})"}
    if out["status"] == "AUTHENTICATION_ERROR":
        return {"status": BLOCKED, "transport": "rest", "detail": "AUTHENTICATION_REQUIRED — ejecutar `az login --allow-no-subscriptions`"}
    if out["status"] not in ("OK", "RETRY_SUCCESS"):
        return {"status": BLOCKED, "transport": "rest",
                "detail": f"BLOCKED — POWER BI TRANSPORT ({out['status']} tras {out['attempts']} intentos). "
                          "Fallback opcional: Power BI Desktop + conector."}
    res = {"status": PASS if out["status"] == "OK" else PASS_W, "transport": "rest",
           "detail": "Consulta de prueba REST OK" + (" tras reintento" if out["status"] == "RETRY_SUCCESS" else "")}
    try:
        res["refresh"] = client.get_last_refresh()
    except Exception:
        res["refresh"] = None
    return res


# ---------------------------------------------------------------------------------------------- gate
def check(period: Period, listing: list[dict], staged: str | None, pbi: dict, notion: str,
          moneypro: dict | None = None, validation: dict | None = None, regression: dict | None = None) -> dict:
    files = {e["name"].replace("\\", "/"): e for e in listing if e.get("type") == "file"}
    items = []

    def add(area, item, status, detail, required=True):
        items.append({"area": area, "item": item, "status": status, "required": required, "detail": detail})

    have = files.get
    hr, tm, eng = "Historic Reports/", "Transacciones Money Pro/", "Report Engine/"

    # Dashboard
    dash = f"{hr}Dashboard Personal Financials Cash Basis {period.mdyy}.pdf"
    if have(dash):
        pages = None
        if staged and os.path.exists(os.path.join(staged, dash)):
            try:
                from pypdf import PdfReader
                pages = len(PdfReader(os.path.join(staged, dash)).pages)
            except Exception:
                pages = None
        add("Dashboard", "Dashboard PDF del mes", PASS, dash + (f" ({pages} páginas)" if pages else ""))
    else:
        add("Dashboard", "Dashboard PDF del mes", BLOCKED, f"Falta {dash}: exportar el dashboard al corte.")

    # NWT
    nwt, nwt_prev = f"{hr}Net Worth Tracker {period.mdyy}.pdf", f"{hr}Net Worth Tracker {period.prev.mdyy}.pdf"
    add("NWT", "Net Worth Tracker del mes", PASS if have(nwt) else BLOCKED, nwt if have(nwt) else f"Falta {nwt}.")
    add("NWT", "Net Worth Tracker mes anterior", PASS if have(nwt_prev) else WARN,
        nwt_prev if have(nwt_prev) else "No disponible: se usa la columna del mes anterior del NWT actual.", required=False)

    # MoneyPro Word (cumulative close; identified by End Period inside, validated by moneypro_control)
    docx = f"{tm}Money Pro - {period.mes} {period.year}.docx"
    if have(docx):
        add("MoneyPro", "Word de cierre MoneyPro (4 donas)", PASS, f"{docx} — snapshot ACUMULADO al End Period")
    else:
        add("MoneyPro", "Word de cierre MoneyPro (4 donas)", BLOCKED, f"Falta '{docx}' con las 4 donas del cierre.")
    if moneypro:
        st = moneypro.get("status")
        mp_status = {"PASS": PASS, "PASS_WITH_WARNINGS": PASS_W, "REQUIRES USER CONFIRMATION": HUMAN}.get(st, BLOCKED)
        msgs = "; ".join(i["message"] for i in moneypro.get("issues", []) if i["severity"] != "INFO")[:600]
        add("MoneyPro", "Control MoneyPro (donas vs Excel, End Period, acumulados)", mp_status,
            f"{st}" + (f": {msgs}" if msgs else ""))

    # Control Totals Excel — selected by End Period, never by file name
    cands = sorted(n for n in files if n.startswith(tm) and os.path.basename(n).startswith("MoneyPro_Control_Totals") and n.endswith(".xlsx"))
    found = []
    for n in cands:
        end = None
        if staged and os.path.exists(os.path.join(staged, n)):
            try:
                import openpyxl
                wb = openpyxl.load_workbook(os.path.join(staged, n), read_only=True)
                try:  # read_only keeps the file handle open until close() (locks the file on Windows)
                    ws = wb["Totals"]
                    ends = {r[4] for r in ws.iter_rows(min_row=2, values_only=True) if r[0] and r[4] and not str(r[2] or "").startswith("=")}
                finally:
                    wb.close()
                end = ends.pop() if len(ends) == 1 else ("MIXED" if ends else None)
            except Exception:
                end = "UNREADABLE"
        found.append({"file": n, "end_period": end})
    match = [x for x in found if x["end_period"] == period.ddmmyyyy]
    unknown = [x for x in found if x["end_period"] is None]
    if len(match) == 1:
        add("Control Totals", "Excel MoneyPro Control Totals", PASS, f"{match[0]['file']} (End Period {period.ddmmyyyy})")
    elif len(match) > 1:
        add("Control Totals", "Excel MoneyPro Control Totals", HUMAN,
            "Dos o más archivos igualmente plausibles para el mismo End Period: " + ", ".join(x["file"] for x in match)
            + ". Indicar cuál es el oficial.")
    elif unknown:
        add("Control Totals", "Excel MoneyPro Control Totals", BLOCKED,
            "No se pudo leer el End Period (copiar los candidatos para inspección): " + ", ".join(x["file"] for x in unknown))
    else:
        add("Control Totals", "Excel MoneyPro Control Totals", BLOCKED,
            f"Ningún Excel con End Period {period.ddmmyyyy} (" + "; ".join(f"{x['file']}: {x['end_period']}" for x in found)
            + "). Actualizar con las donas del cierre.")
    for x in found:
        if match and x not in match and x["end_period"] not in (None, period.ddmmyyyy):
            add("Control Totals", f"Libro no vigente: {os.path.basename(x['file'])}", WARN,
                f"End Period {x['end_period']} (se usa el del cierre; no se toca)", required=False)

    # Export + temporal consistency
    exports = []
    for n, f in files.items():
        mm = EXPORT_RE.search(n)
        if mm and f.get("size", 1) > 0:
            exports.append((dt.datetime(*map(int, mm.groups()[:6])), n, f))
    exports.sort()
    export_info = None
    if not exports:
        add("MoneyPro Export", "Export Money Pro Transactions", BLOCKED, "No hay ningún export no vacío.")
    else:
        ts, n, f = exports[-1]
        export_info = {"file": n, "timestamp": ts.isoformat(), "mtimeMs": f.get("mtimeMs")}
        if ts.date() <= period.eom:
            add("MoneyPro Export", "Export Money Pro Transactions", BLOCKED,
                f"El último export ({os.path.basename(n)}) es del {ts.date()}, no posterior al cierre {period.eom}.")
        else:
            add("MoneyPro Export", "Export Money Pro Transactions", PASS, f"{os.path.basename(n)} (posterior al cierre)")
        dx = have(docx)
        if dx and f.get("mtimeMs") and f["mtimeMs"] > dx["mtimeMs"]:
            add("MoneyPro Export", "Capturas vs export", WARN,
                "El Word es anterior al último export: diferencias de reconciliación pueden ser de oportunidad.", required=False)

    # Power BI REST (+ refresh date vs export)
    add("Power BI REST", f"Power BI ({(pbi.get('transport') or 'sin transporte').upper()})", pbi["status"], pbi["detail"])
    ref = pbi.get("refresh")
    if export_info:
        if ref and ref.get("endTime"):
            try:
                end = dt.datetime.fromisoformat(ref["endTime"].replace("Z", "+00:00"))
                ex = dt.datetime.fromtimestamp(export_info["mtimeMs"] / 1000, dt.timezone.utc)
                ok = end >= ex and ref.get("status") in ("Completed", None)
                add("Power BI REST", "Refresh del dataset vs export", PASS if ok else WARN,
                    f"Último refresh {ref.get('endTime')} ({ref.get('status')}); export {ex.isoformat(timespec='seconds')}. "
                    "Control definitivo: validate.py --export (Income/Expense del mes).")
            except Exception:
                add("Power BI REST", "Refresh del dataset vs export", WARN, f"Fecha de refresh no interpretable: {ref}", required=False)
        else:
            pbix = have("Dashboard Personal Finance.pbix")
            proxy = pbix and export_info.get("mtimeMs") and pbix["mtimeMs"] >= export_info["mtimeMs"]
            add("Power BI REST", "Refresh del dataset vs export", PASS_W if proxy else WARN,
                ("PBIX guardado después del export (proxy). " if proxy else "Sin historial de refresh disponible. ")
                + "Control definitivo: validate.py --export (Income/Expense del mes en el export = modelo).", required=False)

    # DAX library, engine, baseline, docs, previous report
    add("DAX", "dax_queries.md (única fuente Q0–Q8)", PASS if have(eng + "dax_queries.md") else BLOCKED, eng + "dax_queries.md")
    miss = [f for f in ("engine.py", "rlib.py", "charts.py", "report_data_2026_09_GOLD.json", "powerbi_rest_client.py")
            if not have(eng + f)]
    add("Report Engine", "engine.py / rlib.py / charts.py / GOLD / REST client", BLOCKED if miss else PASS,
        ("Faltan: " + ", ".join(miss)) if miss else "Completo")
    add("Regression baseline", "regression/baseline_2026_09.json", PASS if have(eng + "regression/baseline_2026_09.json") else BLOCKED,
        eng + "regression/baseline_2026_09.json")
    docs = sorted(n for n in files if "Modelo_Financiero_Personal_Documentacion_" in n and n.endswith(".pdf"))
    pref = [n for n in docs if "Actualizada" in n] or docs
    add("Documentation", "Documentación metodológica", PASS if pref else BLOCKED, pref[-1] if pref else "No encontrada")
    prev = sorted(n for n in files if os.path.basename(n).startswith(f"Reporte_Financiero_{period.prev.mes}_{period.prev.year}")
                  and n.endswith(".pdf") and "preliminar" not in n)
    add("Previous report", "Reporte del mes anterior", PASS if len(prev) == 1 else (PASS_W if prev else WARN),
        (prev[0] + (f" (+{len(prev) - 1} variantes; se usa la primera, sólo es referencia)" if len(prev) > 1 else ""))
        if prev else "No encontrado (sólo referencia de estructura).", required=False)
    add("Notion", "Notion / PFOS", PASS if notion == "READY" else WARN,
        "Conector disponible (sólo se usa tras QA_PASSED + HUMAN RELEASE)" if notion == "READY"
        else "Sin conector: el cierre en Notion queda pendiente.", required=False)

    area_status = {a: worst([i["status"] for i in items if i["area"] == a]) for a in AREAS if any(i["area"] == a for i in items)}
    crit = [i for i in items if i["required"]]
    if any(i["status"] == BLOCKED for i in crit):
        overall = "BLOCKED"
    elif any(i["status"] == HUMAN for i in crit):
        overall = HUMAN
    elif any(i["status"] != PASS for i in items):
        overall = "READY_WITH_WARNINGS"
    else:
        overall = "READY"
    return {"period": f"{period.year}-{period.month:02d}", "checked": now_iso(), "overall": overall,
            "status": {"READY": "READY", "READY_WITH_WARNINGS": "READY", HUMAN: HUMAN}.get(overall, "BLOCKED"),
            "powerbi_transport": pbi.get("transport"), "areas": area_status, "items": items,
            "validation": (validation or {}).get("gate", "PENDING"),
            "regression": (regression or {}).get("status", "PENDING"),
            "blocking_items": [i["item"] for i in crit if i["status"] in (BLOCKED, HUMAN)]}


def render(res: dict, period: Period) -> str:
    lines = ["DATA READINESS", "----------------", f"Period: {period.month_en} {period.year}"]
    for a, s in res["areas"].items():
        lines.append(f"{a + ':':22} {s}")
    lines += [f"{'Validation:':22} {res['validation']}", f"{'Regression:':22} {res['regression']}", "",
              f"Overall: {res['overall']}"]
    if res["overall"] in ("BLOCKED", HUMAN):
        lines += ["", "Missing / to confirm:"]
        k = 0
        for i in res["items"]:
            if i["item"] in res["blocking_items"]:
                k += 1
                lines.append(f"{k}. [{i['status']}] {i['item']} — {i['detail']}")
        lines += ["", "Proceso detenido. No report was generated and no Notion records were modified."]
    else:
        notes = [i for i in res["items"] if i["status"] != PASS]
        if notes:
            lines += ["", "Warnings / review:"] + [f"- [{i['status']}] {i['item']} — {i['detail']}" for i in notes]
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--period", required=True)
    ap.add_argument("--listing")
    ap.add_argument("--scan")
    ap.add_argument("--staged")
    ap.add_argument("--powerbi", default="rest", choices=["rest", "connector", "MISSING"])
    ap.add_argument("--dax-results")
    ap.add_argument("--moneypro")
    ap.add_argument("--validation")
    ap.add_argument("--regression")
    ap.add_argument("--notion", default="MISSING", choices=["READY", "MISSING"])
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    p = Period.parse(a.period)
    if a.scan:
        listing, staged = scan(a.scan), a.staged or a.scan
    else:
        raw = load_json(a.listing)
        listing, staged = (raw["entries"] if isinstance(raw, dict) else raw), a.staged
    opt = lambda f: load_json(f) if f and os.path.exists(f) else None  # noqa: E731
    pbi = powerbi_probe(a.powerbi, a.dax_results, p)
    res = check(p, listing, staged, pbi, a.notion, opt(a.moneypro), opt(a.validation), opt(a.regression))
    save_json(a.out, res)
    print(render(res, p))


if __name__ == "__main__":
    main()
