"""VALIDATION — identities, independent runway, reconciliation gate, data quality.

Input : dax results JSON (dax_bridge.py run|ingest) + moneypro_control JSON (optional, for context).
Output: validations JSON with figures, checks and an overall gate:
          PASS               everything verified
          PASS_WITH_WARNINGS known methodological / non-monthly warnings only (Q0 via REST, LCR measure context,
                             query obtained after retry, ...)
          REQUIRES_REVIEW    findings a human must review (potential duplicates, unexplained reconciliation
                             differences, inconsistent control totals); documented in the report, not auto-fixed
          BLOCKED            critical: query not available after retries, authentication, wrong period,
                             broken financial identity, model not refreshed with the latest export
Transport failures are NEVER reported as data-quality findings.
No financial methodology is created here: every rule is the one already documented in
dax_queries.md / the Skill (Cash Basis, Historical Cost, official runway = Cash-type / ExpAll÷MonthsAll).
"""
from __future__ import annotations

import argparse
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import Period, save_json, load_json, now_iso, num, norm_key, BLOCKING, WARNING, INFO  # noqa: E402

TOL = 1.0  # pesos
REVIEW = "REQUIRES_REVIEW"
SUCCESS = {"OK", "RETRY_SUCCESS"}
TRANSPORT = {"TRANSPORT_ERROR", "ERROR", "NOT_RUN"}   # "ERROR" = legacy status of older result files


def norm_status(st) -> str:
    """'🟢 OK' / '🔴 CHECK' / '⚪ SIN MAPEO' (model labels with emoji) -> 'OK' / 'CHECK' / 'SIN MAPEO'."""
    return re.sub(r"[^A-ZÁÉÍÓÚÑ ()0-9]", "", str(st or "").upper()).strip()

# Reconciliation lines whose difference is explained by a documented methodological difference
# (Money Pro groups / definitions that Power BI does not map 1:1). Anything else != OK is REQUIRES REVIEW.
METHODOLOGY = {
    ("Assets", "Payment Accounts"): "Money Pro 'Payment Accounts' es una agrupación de cuentas de pago; el modelo no la mapea "
                                    "1:1 (Wallet Cash Basis + cuentas Cash-type). Diferencia de definición, no de datos.",
}


def col(row: dict, name: str):
    """Find a column by exact name or by '[name]' suffix (REST: 'Table[Col]', MCP: '[Col]')."""
    if name in row:
        return row[name]
    for k, v in row.items():
        if k.endswith(f"[{name}]"):
            return v
    return None


def rows(res: dict, qid: str) -> list[dict]:
    q = res["queries"].get(qid, {})
    return [{norm_key(k): v for k, v in r.items()} for r in q.get("rows", [])] if q.get("status") in SUCCESS else []


def export_month_totals(csv_path: str, period: Period) -> dict:
    """Income / Expense of the month straight from the Money Pro export (control only, no methodology):
    semicolon CSV, Date M/D/YYYY, Amount with thousands commas, column 'Transaction Type'."""
    import csv
    import re
    out = {"Income": 0.0, "Expense": 0.0, "rows": 0, "malformed": 0, "fragments": [], "repeated_after_fragment": []}
    with open(csv_path, encoding="utf-8-sig", newline="") as fh:
        recs = list(csv.DictReader(fh, delimiter=";"))
    seen = []
    for r in recs:
        d = (r.get("Date") or "").strip()
        if not re.fullmatch(r"\d{1,2}/\d{1,2}/\d{4}", d):
            out["malformed"] += 1
            out["fragments"].append(";".join(str(v) for v in r.values() if v)[:120])
            continue
        key = tuple((k, r.get(k)) for k in ("Date", "Time", "Amount", "Account", "Category", "Description", "Transaction Type", "Agent"))
        if out["fragments"] and key in seen:
            out["repeated_after_fragment"].append(f"{d} {r.get('Time')} {r.get('Category')} {r.get('Amount')}")
        seen.append(key)
    for r in recs:
            d = (r.get("Date") or "").strip()
            if not re.fullmatch(r"\d{1,2}/\d{1,2}/\d{4}", d):
                continue
            mth, _, yr = map(int, d.split("/"))
            if (yr, mth) != (period.year, period.month):
                continue
            t = r.get("Transaction Type")
            if t in ("Income", "Expense"):
                out[t] += float((r.get("Amount") or "0").replace(",", ""))
                out["rows"] += 1
    return out


def check(period: Period, res: dict, mp: dict | None = None, export_csv: str | None = None) -> dict:
    checks, fig = [], {}

    def add(name, ok, sev, detail, **vals):
        result = "OK" if ok else {BLOCKING: "FAIL", REVIEW: "REVIEW"}.get(sev, "WARN")
        checks.append({"check": name, "result": result, "severity": INFO if ok else sev, "detail": detail, **vals})

    # 0. Core queries executed — transport/auth/DAX problems block; they are never data findings
    blocked_reason = None
    if any(q["status"] == "AUTHENTICATION_ERROR" for q in res["queries"].values()):
        blocked_reason = "AUTHENTICATION_REQUIRED"
        add("Autenticación Power BI", False, BLOCKING,
            "AUTHENTICATION_REQUIRED — la sesión de Azure CLI no es válida: ejecutar `az login --allow-no-subscriptions`")
    for qid, q in res["queries"].items():
        st = q["status"]
        if qid.startswith("Q0"):
            if st not in SUCCESS:
                add("Q0 INFO.MEASURES", False, WARNING, "Q0 UNAVAILABLE_VIA_REST — inventario de medidas no verificado; continúa Q1–Q8")
            continue
        if st == "RETRY_SUCCESS":
            add(f"{qid} obtenida tras reintento", False, WARNING,
                f"Falla transitoria de transporte resuelta en el intento {q.get('attempts')}; resultado válido")
        elif st == "AUTHENTICATION_ERROR":
            blocked_reason = "AUTHENTICATION_REQUIRED"
            add(f"{qid} ejecutada", False, BLOCKING, "AUTHENTICATION_REQUIRED — ejecutar `az login --allow-no-subscriptions`")
        elif st == "DAX_ERROR":
            blocked_reason = blocked_reason or "BLOCKED — DAX ERROR"
            add(f"{qid} ejecutada", False, BLOCKING, f"DAX_ERROR (no transitorio): {' '.join(str(q.get('error', '')).split())[:220]}")
        elif st in TRANSPORT:
            blocked_reason = blocked_reason or "BLOCKED — POWER BI TRANSPORT"
            add(f"{qid} ejecutada", False, BLOCKING,
                f"TRANSPORT_ERROR tras {q.get('attempts', 1)} intento(s) — falla de transporte, no de datos: "
                f"{' '.join(str(q.get('error', '')).split())[-220:]}")
    if blocked_reason:
        return {"period": f"{period.year}-{period.month:02d}", "validated": now_iso(), "gate": "BLOCKED",
                "blocked_reason": blocked_reason, "checks": checks, "figures": fig,
                "note": "No se analizan cifras con consultas faltantes: ninguna conclusión financiera se basa en datos no obtenidos."}

    # 1. Q1 monthly KPIs
    q1 = {(int(num(col(r, "Year"))), int(num(col(r, "Month")))): r for r in rows(res, "Q1")}
    cur, prv = q1.get((period.year, period.month)), q1.get((period.prev.year, period.prev.month))
    if not cur:
        add("Q1 mes actual", False, BLOCKING, "Q1 no devolvió el mes del corte (¿modelo sin refrescar?)")
        return {"period": f"{period.year}-{period.month:02d}", "gate": "BLOCKED", "checks": checks, "figures": fig}
    m = {k: num(v) for k, v in cur.items()}
    p = {k: num(v) for k, v in (prv or {}).items()}
    fig["month"], fig["prev_month"] = m, p
    q2 = {k: num(v) for k, v in (rows(res, "Q2") or [{}])[0].items()}
    fig["ytd"] = q2

    # Freshness (transport-independent, works for REST/Service and the local connector):
    # the model must contain the latest Money Pro export -> Income and Expense of the month must match.
    if export_csv:
        ex = export_month_totals(export_csv, period)
        fig["export_month"] = {**ex, "file": os.path.basename(export_csv)}
        ok = abs(ex["Income"] - (m.get("Income") or 0)) <= TOL and abs(ex["Expense"] - (m.get("Expense") or 0)) <= TOL
        add("Modelo actualizado con el último export Money Pro (Income/Expense del mes)", ok, BLOCKING,
            f"Export {ex['Income']:,.2f} / {ex['Expense']:,.2f} vs modelo {m.get('Income') or 0:,.2f} / {m.get('Expense') or 0:,.2f}"
            + ("" if ok else " — refrescar (y publicar, si se usa REST) el modelo antes de continuar"))
        if ex["malformed"]:
            rep = ex["repeated_after_fragment"]
            add("Integridad del export Money Pro", False, REVIEW,
                f"{ex['malformed']} línea(s) truncada(s) ({'; '.join(ex['fragments'])})"
                + (f"; después de la línea truncada se repiten registros idénticos ya presentes: {', '.join(rep)}. "
                   "Posible artefacto del archivo exportado (no confirmado): REQUIERE REVISIÓN — re-exportar Money Pro y comparar." if rep else ""))
    else:
        add("Frescura del modelo vs export", False, WARNING, "No se proporcionó --export: frescura validada sólo por proxy (mtime PBIX)")

    # Identity 1: NAP − NAS + ΔWallet = Income − Expense = NW month
    dW = (q2.get("Wallet") or 0) - (q2.get("WalletPrev") or 0)
    lhs = (m.get("NAP") or 0) - (m.get("NAS") or 0) + dW
    res_ie = (m.get("Income") or 0) - (m.get("Expense") or 0)
    add("Identidad 1: NAP − NAS + ΔWallet = Income − Expense", abs(lhs - res_ie) <= TOL and abs(res_ie - (m.get("NW") or 0)) <= TOL,
        BLOCKING, f"{lhs:,.2f} vs {res_ie:,.2f} (NW mes {m.get('NW') or 0:,.2f})", lhs=lhs, rhs=res_ie)

    # Identity 4: Active + Financial + Other + Benefits = Income
    comp = sum(m.get(k) or 0 for k in ("Active", "Fin", "Other", "Benef"))
    add("Identidad 4: Active + Financial + Other + Benefits = Income", abs(comp - (m.get("Income") or 0)) <= TOL, BLOCKING,
        f"{comp:,.2f} vs {m.get('Income') or 0:,.2f}")

    # Identity 2: Wallet + Σ Asset Amount (balance at cut) = NW acumulado
    for suf, wal, nwk in (("EOM", "Wallet", "NWAcum"), ("PEOM", "WalletPrev", "NWAcumPrev")):
        q5 = rows(res, f"Q5_{suf}")
        bal = sum(num(col(r, "Bal")) or 0 for r in q5)
        fig[f"assets_{suf}"] = bal
        fig[f"assets_detail_{suf}"] = [{"account": col(r, "Asset Accounts"), "type": col(r, "Asset Type"),
                                        "bal": num(col(r, "Bal")), "amt_model": num(col(r, "AmtModel"))} for r in q5]
        tot = (q2.get(wal) or 0) + bal
        add(f"Identidad 2 ({suf}): Wallet + Σ Asset Amount = Net Worth acumulado", abs(tot - (q2.get(nwk) or 0)) <= TOL,
            BLOCKING, f"{tot:,.2f} vs {q2.get(nwk) or 0:,.2f}")
        if suf == "EOM":
            drift = [d for d in fig["assets_detail_EOM"] if d["amt_model"] is not None and abs(d["amt_model"] - (d["bal"] or 0)) > TOL]
            add("Asset Amount (último saldo) = saldo al corte", not drift, WARNING,
                "Transacciones posteriores al corte en: " + ", ".join(str(d["account"]) for d in drift) if drift
                else "Sin transacciones posteriores al corte")

    # Identity 3 + independent runway (Q3 + Q4)
    cash_eom = sum(num(col(r, "Bal")) or 0 for r in rows(res, "Q3_EOM"))
    cash_peom = sum(num(col(r, "Bal")) or 0 for r in rows(res, "Q3_PEOM"))
    fig["cash_type"] = {"EOM": cash_eom, "PEOM": cash_peom,
                        "detail": [{"account": col(r, "Asset Accounts"), "bal": num(col(r, "Bal"))} for r in rows(res, "Q3_EOM")]}
    if m.get("CashEOM") is not None:
        add("Identidad 3: Σ Cash-type = [Cash Balance EOM]", abs(cash_eom - m["CashEOM"]) <= TOL, WARNING,
            f"{cash_eom:,.2f} vs {m['CashEOM']:,.2f}")
    q4 = {k: num(v) for k, v in (rows(res, "Q4") or [{}])[0].items()}
    ame = q4["ExpAll"] / q4["MonthsAll"] if q4.get("MonthsAll") else None
    ame_p = q4["ExpAllPrev"] / q4["MonthsAllPrev"] if q4.get("MonthsAllPrev") else None
    fig["runway"] = {"AME_official": ame, "AME_official_prev": ame_p,
                     "runway": cash_eom / ame if ame else None, "runway_prev": cash_peom / ame_p if ame_p else None,
                     "AME_YTD": (q4.get("ExpYTD") or 0) / period.month, "measure_LCR_Y": q4.get("LCR_meas_Y"),
                     "measure_LCR_all": q4.get("LCR_meas_all"), "measure_AME_Y": q4.get("AME_meas_Y"),
                     "measure_AME_all": q4.get("AME_meas_all"), "q4": q4,
                     "note": "Runway oficial = Cash-type / (ExpAll ÷ MonthsAll). La medida del modelo es referencia, no fuente."}
    add("Runway independiente calculable", ame is not None and cash_eom > 0, BLOCKING,
        f"Cash-type {cash_eom:,.2f} / AME {ame or 0:,.2f} = {fig['runway']['runway'] or 0:.3f} meses")
    if q4.get("LCR_meas_Y") is not None and fig["runway"]["runway"]:
        gap = abs(q4["LCR_meas_Y"] - fig["runway"]["runway"])
        add("Medida Liquid Cash Runway vs runway validado", gap < 0.05, WARNING,
            f"Medida en contexto anual {q4['LCR_meas_Y']:.3f} vs validado {fig['runway']['runway']:.3f}: la medida divide el "
            f"gasto del año entre 12 meses (incluye meses futuros sin gasto real); el validado usa Cash-type / gasto "
            f"histórico ÷ meses con gasto real. Diferencia de contexto, no de datos. Se reporta el validado.")

    # 2. Reconciliation gate (Q8c, Q8d)
    overall = col((rows(res, "Q8c") or [{}])[0], "Overall")
    recon = []
    for r in rows(res, "Q8d"):
        t, c, st = col(r, "Type"), col(r, "Category"), norm_status(col(r, "St"))
        d = num(col(r, "Diff"))
        note = METHODOLOGY.get((t, c), "")
        if d is not None and abs(d) < 0.005:
            cls = "CONFIRMADO"
        elif d is not None and abs(d) < TOL:
            cls, note = "EXPLICADO POR METODOLOGÍA", "Diferencia < $1: redondeo de presentación de Money Pro"
        elif (t, c) in METHODOLOGY:
            cls = "EXPLICADO POR METODOLOGÍA"
        else:
            cls = "REQUIERE REVISIÓN"
        recon.append({"type": t, "category": c, "pbi": num(col(r, "PBI")), "mp": num(col(r, "MP")), "diff": d,
                      "status": st, "status_raw": col(r, "St"), "class": cls, "note": note})
    fig["reconciliation"] = {"overall": overall, "lines": recon}
    review = [x for x in recon if x["class"] == "REQUIERE REVISIÓN"]
    add("Reconciliación Power BI vs Money Pro", not review, REVIEW,
        f"Overall '{overall}'. Requieren revisión: " + "; ".join(f"{x['type']}/{x['category']} {x['diff'] or 0:+,.2f}" for x in review)
        if review else f"Overall '{overall}'")

    # Control source used by the model vs the period control file
    if mp and mp.get("categories"):
        mp_map = {(c["type"], c["category"]): c["value"] for c in mp["categories"]}
        off = [x for x in recon if (x["type"], x["category"]) in mp_map and x["mp"] is not None
               and abs(mp_map[(x["type"], x["category"])] - x["mp"]) > TOL]
        add("Totales de control del modelo = Excel del período", not off, REVIEW,
            ("El modelo usa totales de control distintos al Excel del período en: "
             + ", ".join(f"{x['type']}/{x['category']}" for x in off) + " — refrescar la fuente de control en Power BI")
            if off else "El modelo lee los mismos totales de control del período")
        if mp.get("status") not in ("PASS", "PASS_WITH_WARNINGS"):
            add("MoneyPro Control", False, BLOCKING if mp.get("status") == "BLOCKED" else REVIEW,
                f"Estado {mp.get('status')}: " + "; ".join(i["message"] for i in mp.get("issues", []) if i["severity"] != WARNING))

    # 3. Data quality (Q8a, Q8b) — findings, never auto-corrected
    nosub = [{"date": col(r, "Date"), "type": col(r, "Type"), "cat": col(r, "Cat"), "agent": col(r, "Agent"), "amt": num(col(r, "Amt"))}
             for r in rows(res, "Q8a")]
    ident = [{"date": col(r, "Date"), "type": col(r, "Type"), "sub": col(r, "Subcategory"), "agent": col(r, "Agent"),
              "amt": num(col(r, "Amount")), "n": num(col(r, "N"))} for r in rows(res, "Q8b")]
    fig["data_quality"] = {"no_subcategory": nosub, "identical_records": ident}
    add("Data Quality: sin subcategoría (año)", not nosub, REVIEW, f"{len(nosub)} registros — REQUIERE REVISIÓN en Money Pro")
    add("Data Quality: registros idénticos (mes)", not ident, REVIEW,
        f"{len(ident)} grupos — no son duplicados confirmados; REQUIERE REVISIÓN")

    sev = {c["severity"] for c in checks}
    gate = ("BLOCKED" if BLOCKING in sev else REVIEW if REVIEW in sev
            else "PASS_WITH_WARNINGS" if WARNING in sev else "PASS")
    return {"period": f"{period.year}-{period.month:02d}", "validated": now_iso(), "gate": gate,
            "checks": checks, "figures": fig}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--period", required=True)
    ap.add_argument("--results", required=True)
    ap.add_argument("--moneypro")
    ap.add_argument("--export", help="Money Pro Transactions export CSV (freshness control)")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    v = check(Period.parse(a.period), load_json(a.results), load_json(a.moneypro) if a.moneypro else None, a.export)
    save_json(a.out, v)
    print("VALIDATION:", v["gate"])
    for c in v["checks"]:
        print(f" [{c['result']:6}] {c['check']}: {c['detail']}")


if __name__ == "__main__":
    main()
