"""MoneyPro control gate.

Does NOT read screenshots and does NOT update the Excel: that is the job of the existing
`moneypro-control-totals` skill (single owner of extraction/update). This module only validates
its outputs as an independent control source:

  donuts_<YYYY_MM>.json  (values read from the MoneyPro screenshots by the skill), schema:
     {"source": "Money Pro - <Mes> <Año>.docx",
      "donuts": {"Income":      {"end": "YYYY-MM-DD" | null, "total": <number>, "items": {"<category>": <number>, ...}},
                 "Expenses":    {...}, "Other Assets": {...},
                 "Assets":      {"end": ..., "total": ..., "items": {"Other Assets": ..., "Payment Accounts": ...}}}}
  MoneyPro Control Totals Excel (sheet 'Totals') for the period.

STRUCTURAL RULE — cumulative closes: every MoneyPro close (Word with the 4 donuts) holds values
ACCUMULATED from the Beginning Period (inception, e.g. Jan 1, 2018) up to its End Period — Income,
Expenses, Other Assets and Assets alike. They are never movements of the month. The End Period (not the
file name nor the month in it) identifies the close; values are compared only with control totals of the
SAME End Period (Excel 'Totals') and never with monthly Power BI figures (Q1). Optional "start" per donut
(ISO date) is checked against the Excel Beginning Period: a donut that starts at the first day of the
month is a monthly view, not a close -> BLOCKING.

Checks (BLOCKING unless noted):
  - the 4 donuts are present (a missing donut is never treated as zero)
  - End Period visible on every donut and equal to the cut date (not visible -> REQUIRES USER CONFIRMATION)
  - each donut: sum(items) == center total
  - Assets donut: 'Other Assets' line == Other Assets donut total (subtotal, not a category)
  - Excel 'Totals': End Period == cut date on every data row
  - Excel totals by Type == donut totals (Income, Expense, Assets)
  - category-level comparison: new categories / categories not seen (WARNING, never deleted or zeroed)
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import Period, save_json, load_json, now_iso, BLOCKING, WARNING, CONFIRM  # noqa: E402

TOL = 0.01
DONUTS = ["Income", "Expenses", "Other Assets", "Assets"]
TYPE_OF = {"Income": "Income", "Expenses": "Expense", "Other Assets": "Other Assets"}


def read_totals(xlsx_path: str) -> dict:
    import openpyxl
    wb = openpyxl.load_workbook(xlsx_path, data_only=False)
    if "Totals" not in wb.sheetnames:
        return {"error": "Sheet 'Totals' not found"}
    ws = wb["Totals"]
    rows, formulas = [], {}
    for r in ws.iter_rows(min_row=2, values_only=True):
        t, cat, val, beg, end = (list(r) + [None] * 5)[:5]
        if t is None:
            continue
        if isinstance(val, str) and val.startswith("="):
            formulas[str(t)] = val
            continue
        rows.append({"type": str(t), "category": cat, "value": float(val or 0), "end": end, "begin": beg})
    return {"rows": rows, "formulas": formulas}


def check_donuts(period: Period, donuts_json: dict) -> list[dict]:
    """Donut-only checks (no Excel): presence, single End Period per Word, legend sums, Assets subtotal, cumulative start."""
    issues, info = [], []
    d = donuts_json.get("donuts", {})
    cut = period.eom.isoformat()

    for name in DONUTS:
        if name not in d:
            issues.append({"severity": BLOCKING, "check": f"donut {name}", "message": "Donut missing in screenshots (not assumed zero)"})
    visible = {dn.get("end") for dn in d.values() if dn.get("end")}
    if len(visible) > 1:
        issues.append({"severity": BLOCKING, "check": "End Period del Word",
                       "message": f"Las capturas del mismo Word muestran End Periods distintos {sorted(visible)}: no es un único cierre"})
    word_end = visible.pop() if len(visible) == 1 else None
    for name, dn in d.items():
        start = dn.get("start")
        if start and start == period.eom.replace(day=1).isoformat():
            issues.append({"severity": BLOCKING, "check": f"{name} start period",
                           "message": f"La dona empieza el {start}: vista mensual, no cierre acumulado. Capturar el rango completo hasta el End Period"})
        end = dn.get("end")
        if end is None and word_end:
            issues.append({"severity": WARNING, "check": f"{name} end period",
                           "message": f"Fecha no visible en esta captura; hereda el End Period del Word de cierre ({word_end}), "
                                      "visible en las demás donas (regla: un Word = un cierre acumulado)"})
            end = word_end
        if end is None:
            issues.append({"severity": CONFIRM, "check": f"{name} end period",
                           "message": "End Period not visible in the screenshot; confirm it corresponds to the cut date"})
        elif end != cut:
            issues.append({"severity": BLOCKING, "check": f"{name} end period", "message": f"End Period {end} != cut {cut}"})
        s = sum(float(v) for v in dn.get("items", {}).values())
        if abs(s - float(dn["total"])) > TOL:
            issues.append({"severity": BLOCKING, "check": f"{name} legend sum",
                           "message": f"Legend sum {s:,.2f} != center total {float(dn['total']):,.2f}"})
    if "Assets" in d and "Other Assets" in d:
        sub = d["Assets"]["items"].get("Other Assets")
        if sub is None or abs(float(sub) - float(d["Other Assets"]["total"])) > TOL:
            issues.append({"severity": BLOCKING, "check": "Assets/Other Assets subtotal",
                           "message": "Assets donut 'Other Assets' line does not match the Other Assets donut total"})

    return issues


def check(period: Period, donuts_json: dict, xlsx_path: str) -> dict:
    issues = check_donuts(period, donuts_json)
    d = donuts_json.get("donuts", {})
    xl = read_totals(xlsx_path)
    if "error" in xl:
        issues.append({"severity": BLOCKING, "check": "Excel", "message": xl["error"]})
        return {"status": "BLOCKED", "issues": issues}
    begins = {r["begin"].date().isoformat() if hasattr(r["begin"], "date") else str(r["begin"]) for r in xl["rows"] if r["begin"]}
    for name, dn in d.items():
        if dn.get("start") and begins and dn["start"] not in begins:
            issues.append({"severity": BLOCKING, "check": f"{name} start vs Beginning Period",
                           "message": f"Inicio de la dona {dn['start']} != Beginning Period del Excel {sorted(begins)}: no es el mismo acumulado"})
    ends = {r["end"] for r in xl["rows"]}
    if ends != {period.ddmmyyyy}:
        issues.append({"severity": BLOCKING, "check": "Excel End Period",
                       "message": f"End Period values {sorted(map(str, ends))} != {period.ddmmyyyy} (control totals not updated for the period)"})

    def xl_sum(t): return sum(r["value"] for r in xl["rows"] if r["type"] == t)
    totals = {"TOTAL INCOME": xl_sum("Income"), "TOTAL EXPENSE": xl_sum("Expense"),
              "TOTAL ASSETS": xl_sum("Other Assets") + xl_sum("Assets")}
    totals["NET PROFIT"] = totals["TOTAL INCOME"] - totals["TOTAL EXPENSE"]
    pairs = [("TOTAL INCOME", "Income"), ("TOTAL EXPENSE", "Expenses"), ("TOTAL ASSETS", "Assets")]
    for tk, dk in pairs:
        if dk in d and abs(totals[tk] - float(d[dk]["total"])) > TOL:
            issues.append({"severity": BLOCKING, "check": f"{tk} vs donut {dk}",
                           "message": f"Excel {totals[tk]:,.2f} != donut {float(d[dk]['total']):,.2f}"})
    for f in ["TOTAL ASSETS", "TOTAL INCOME", "TOTAL EXPENSE"]:
        if f not in xl["formulas"]:
            issues.append({"severity": WARNING, "check": "Excel formulas", "message": f"Formula row '{f}' not found"})

    # category level
    xl_cats = {(r["type"], r["category"]): r["value"] for r in xl["rows"]}
    seen = set()
    for dn_name, t in TYPE_OF.items():
        for cat, v in d.get(dn_name, {}).get("items", {}).items():
            seen.add((t, cat))
            if (t, cat) not in xl_cats:
                issues.append({"severity": WARNING, "check": "new category", "message": f"{t} / {cat} in screenshots but not in Excel"})
            elif abs(xl_cats[(t, cat)] - float(v)) > TOL:
                issues.append({"severity": BLOCKING, "check": "category value",
                               "message": f"{t} / {cat}: Excel {xl_cats[(t, cat)]:,.2f} != screenshot {float(v):,.2f}"})
    for cat, v in d.get("Assets", {}).get("items", {}).items():
        if cat != "Other Assets":
            seen.add(("Assets", cat))
            if ("Assets", cat) in xl_cats and abs(xl_cats[("Assets", cat)] - float(v)) > TOL:
                issues.append({"severity": BLOCKING, "check": "category value",
                               "message": f"Assets / {cat}: Excel {xl_cats[('Assets', cat)]:,.2f} != screenshot {float(v):,.2f}"})
    for k in xl_cats:
        if k not in seen:
            issues.append({"severity": WARNING, "check": "category not seen",
                           "message": f"{k[0]} / {k[1]} in Excel but not in this month's screenshots (not deleted, not zeroed)"})

    sev = {i["severity"] for i in issues}
    status = "BLOCKED" if BLOCKING in sev else ("REQUIRES USER CONFIRMATION" if CONFIRM in sev else ("PASS_WITH_WARNINGS" if WARNING in sev else "PASS"))
    return {"status": status, "checked": now_iso(), "excel": os.path.basename(xlsx_path), "basis": f"acumulado al End Period {period.ddmmyyyy}",
            "source": donuts_json.get("source"), "excel_totals": totals,
            "donut_totals": {k: float(v["total"]) for k, v in d.items()},
            "categories": [{"type": k[0], "category": k[1], "value": v} for k, v in xl_cats.items()],
            "issues": issues}


# ------------------------------------------------------------------------------------------------
# Integrated replacement of the `moneypro-control-totals` skill (Pasos 0–9). The visual reading of
# the donuts stays with Claude (donuts_<YYYY_MM>.json); everything else is code.
# ------------------------------------------------------------------------------------------------
TYPE_ORDER = ["Income", "Expense", "Other Assets", "Assets"]
VARIATION_ALERT = 0.20


def extract_media(docx_path: str, out_dir: str) -> list[str]:
    """Paso 0: extract the screenshots embedded in the Word (document order) for visual reading."""
    import re
    import zipfile
    os.makedirs(out_dir, exist_ok=True)
    with zipfile.ZipFile(docx_path) as z:
        rels = z.read("word/_rels/document.xml.rels").decode("utf-8")
        doc = z.read("word/document.xml").decode("utf-8")
        rid_target = dict(re.findall(r'Id="(rId\d+)"[^>]*Target="(media/[^"]+)"', rels))
        rid_target.update({a: b for b, a in re.findall(r'Target="(media/[^"]+)"[^>]*Id="(rId\d+)"', rels)})
        order = [rid_target[r] for r in re.findall(r'r:embed="(rId\d+)"', doc) if r in rid_target]
        written = []
        for k, target in enumerate(order, 1):
            name = f"{k:02d}_{os.path.basename(target)}"
            with open(os.path.join(out_dir, name), "wb") as fh:
                fh.write(z.read(f"word/{target}"))
            written.append(name)
    return written


def donut_rows(d: dict) -> list[tuple[str, str, float]]:
    """Map donuts by TITLE to sheet rows: Expenses->Expense; Assets donut: 'Other Assets' is a subtotal
    (skipped), every other line (Payment Accounts, future lines) is Type 'Assets'."""
    out = []
    for name, t in (("Income", "Income"), ("Expenses", "Expense"), ("Other Assets", "Other Assets")):
        out += [(t, c, float(v)) for c, v in d.get(name, {}).get("items", {}).items()]
    out += [("Assets", c, float(v)) for c, v in d.get("Assets", {}).get("items", {}).items() if c != "Other Assets"]
    return out


def update(period: Period, donuts_json: dict, excel_in: str, excel_out: str) -> dict:
    """Pasos 3–8: write a NEW workbook (never overwrites the input) with the close values at End Period."""
    import re
    from copy import copy
    import openpyxl
    if os.path.abspath(excel_in) == os.path.abspath(excel_out):
        raise SystemExit("update escribe un archivo nuevo: --write no puede ser el mismo que --update-from")
    issues = check_donuts(period, donuts_json)
    if any(i["severity"] in (BLOCKING, CONFIRM) for i in issues):
        return {"status": "BLOCKED", "issues": issues, "written": None}
    d = donuts_json["donuts"]
    wb = openpyxl.load_workbook(excel_in)
    ws = wb["Totals"]
    data, formulas = [], []
    for r in range(2, ws.max_row + 1):
        a, c = ws.cell(r, 1).value, ws.cell(r, 3).value
        if a is None:
            continue
        if isinstance(c, str) and c.startswith("="):
            formulas.append({"row": r, "label": a, "formula": c,
                             "style": [_style(ws.cell(r, k)) for k in range(1, 6)], "vals": [ws.cell(r, k).value for k in (2, 4, 5)]})
        else:
            data.append({"row": r, "type": a, "category": ws.cell(r, 2).value, "value": c, "begin": ws.cell(r, 4).value,
                         "end": ws.cell(r, 5).value, "style": [_style(ws.cell(r, k)) for k in range(1, 6)]})
    begins = {x["begin"] for x in data}
    prev_end = sorted({str(x["end"]) for x in data})
    new_vals = {(t, c): v for t, c, v in donut_rows(d)}
    changes, new_cats, not_seen, alerts = [], [], [], []
    for x in data:
        k = (x["type"], x["category"])
        if k in new_vals:
            old, new = float(x["value"] or 0), new_vals[k]
            if abs(old - new) > 0.004:
                changes.append({"type": k[0], "category": k[1], "previous": old, "new": new, "variation": new - old})
                if (old and abs(new - old) / abs(old) > VARIATION_ALERT) or (old * new < 0):
                    alerts.append({"type": k[0], "category": k[1], "previous": old, "new": new,
                                   "pct": (new - old) / abs(old) if old else None})
            x["value"] = new
        else:
            not_seen.append({"type": k[0], "category": k[1], "value": x["value"]})   # kept, never zeroed
    existing = {(x["type"], x["category"]) for x in data}
    begin_default = next(iter(begins)) if len(begins) == 1 else data[0]["begin"]
    for t, c, v in donut_rows(d):
        if (t, c) not in existing:
            tmpl = next((x for x in reversed(data) if x["type"] == t), data[-1])
            pos = max(i for i, x in enumerate(data) if x["type"] == tmpl["type"]) + 1
            data.insert(pos, {"row": None, "type": t, "category": c, "value": v, "begin": begin_default,
                              "end": None, "style": tmpl["style"]})
            existing.add((t, c))
            new_cats.append({"type": t, "category": c, "value": v})
    # Paso 5: End Period on EVERY data row; rewrite sheet body deterministically
    old_formula_rows = {f["row"]: None for f in formulas}
    last_data = 1 + len(data)
    for r in range(2, ws.max_row + 1):
        for k in range(1, 6):
            ws.cell(r, k).value = None
    for i, x in enumerate(data, start=2):
        vals = [x["type"], x["category"], x["value"], x["begin"], period.ddmmyyyy]
        for k, v in enumerate(vals, start=1):
            cell = ws.cell(i, k)
            cell.value = v
            _apply(cell, x["style"][k - 1])
    # Paso 6: formulas — extend SUMIFS ranges to the last data row, re-point relative refs to moved rows
    for n, f in enumerate(formulas):
        old_formula_rows[f["row"]] = last_data + 1 + n
    for n, f in enumerate(formulas):
        r = last_data + 1 + n
        fx = re.sub(r"\$([A-Z])\$1:\$\1\$\d+", lambda m: f"${m.group(1)}$1:${m.group(1)}${last_data}", f["formula"])
        fx = re.sub(r"(?<![$A-Z])([A-Z])(\d+)(?!\d)",
                    lambda m: f"{m.group(1)}{old_formula_rows.get(int(m.group(2)), int(m.group(2)))}", fx)
        for k, v in enumerate([f["label"], f["vals"][0], fx, f["vals"][1], f["vals"][2]], start=1):
            ws.cell(r, k).value = v
            _apply(ws.cell(r, k), f["style"][k - 1])
    wb.save(excel_out)
    # Paso 7: independent validation of the written file (Python evaluation of the same SUMIFS logic)
    res = check(period, donuts_json, excel_out)
    xl = read_totals(excel_out)
    ranges_ok = all(f"${c}$1:${c}${last_data}" in fx for fx in xl["formulas"].values() if "SUMIFS" in fx for c in ("A", "C"))
    status = "PASS" if res["status"] in ("PASS", "PASS_WITH_WARNINGS") and ranges_ok else "BLOCKED"
    if status == "PASS" and not_seen:
        status = "HUMAN_CONFIRMATION_REQUIRED"   # Paso 4.4: kept as-is; the user decides (never auto-zeroed/deleted)
    return {"status": status, "written": os.path.basename(excel_out), "source": os.path.basename(excel_in),
            "previous_end_period": prev_end, "end_period": period.ddmmyyyy, "basis": f"acumulado al End Period {period.ddmmyyyy}",
            "changes": changes, "new_categories": new_cats, "not_seen": not_seen, "variation_alerts": alerts,
            "formula_ranges_ok": ranges_ok, "formulas": xl["formulas"], "totals": res["excel_totals"],
            "donut_totals": res["donut_totals"], "validation": res["status"],
            "issues": issues + [i for i in res["issues"] if i not in issues]}


def _style(cell) -> dict:
    from copy import copy
    return {"font": copy(cell.font), "border": copy(cell.border), "fill": copy(cell.fill),
            "number_format": cell.number_format, "alignment": copy(cell.alignment), "protection": copy(cell.protection)}


def _apply(cell, st: dict) -> None:
    from copy import copy
    for k in ("font", "border", "fill", "alignment", "protection"):
        setattr(cell, k, copy(st[k]))
    cell.number_format = st["number_format"]


def render_update(u: dict) -> str:
    """Paso 9: final report."""
    L = [f"MONEYPRO CONTROL TOTALS — cierre {u.get('end_period')} ({u.get('basis')}) — {u['status']}",
         f"Archivo nuevo: {u.get('written')} (base: {u.get('source')}; End Period previo {u.get('previous_end_period')})",
         f"Fórmulas reajustadas: {'OK' if u.get('formula_ranges_ok') else 'REVISAR'}", "", "Cambios de valor:"]
    L += [f"  {c['type']:13} {c['category']:34} {c['previous']:>18,.2f} -> {c['new']:>18,.2f} ({c['variation']:+,.2f})" for c in u.get("changes", [])]
    L += ["", "Categorías nuevas:"] + ([f"  {c['type']} / {c['category']} = {c['value']:,.2f}" for c in u.get("new_categories", [])] or ["  ninguna"])
    L += ["", "Categorías no vistas en este cierre (se conservan, no se ponen en cero — confirmar con el usuario):"]
    L += [f"  {c['type']} / {c['category']} = {c['value']}" for c in u.get("not_seen", [])] or ["  ninguna"]
    L += ["", "Totales (Excel nuevo vs donas):"]
    t, dn = u.get("totals", {}), u.get("donut_totals", {})
    for k, dk in (("TOTAL INCOME", "Income"), ("TOTAL EXPENSE", "Expenses"), ("TOTAL ASSETS", "Assets")):
        L.append(f"  {k:14} {t.get(k, 0):>18,.2f}   dona {dn.get(dk, 0):>18,.2f}")
    L.append(f"  {'NET PROFIT':14} {t.get('NET PROFIT', 0):>18,.2f}")
    if u.get("variation_alerts"):
        L += ["", "Variaciones > 20 % o cambio de signo (validar contra el movimiento real):"]
        L += [f"  {a['type']} / {a['category']}: {a['previous']:,.2f} -> {a['new']:,.2f}" + (f" ({a['pct']:+.1%})" if a['pct'] is not None else "")
              for a in u["variation_alerts"]]
    return "\n".join(L)


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "extract-media":
        ap = argparse.ArgumentParser()
        ap.add_argument("cmd")
        ap.add_argument("--docx", required=True)
        ap.add_argument("--to", required=True)
        a = ap.parse_args()
        for n in extract_media(a.docx, a.to):
            print(n)
        return
    if len(sys.argv) > 1 and sys.argv[1] == "update":
        ap = argparse.ArgumentParser()
        ap.add_argument("cmd")
        ap.add_argument("--period", required=True)
        ap.add_argument("--donuts", required=True)
        ap.add_argument("--update-from", required=True, help="workbook of the previous close (not modified)")
        ap.add_argument("--write", required=True, help="new workbook to create")
        ap.add_argument("--out", required=True, help="JSON report")
        a = ap.parse_args()
        u = update(Period.parse(a.period), load_json(a.donuts), a.update_from, a.write)
        save_json(a.out, u)
        print(render_update(u) if u.get("written") else f"BLOCKED: {u['issues']}")
        return
    ap = argparse.ArgumentParser()
    ap.add_argument("--period", required=True)
    ap.add_argument("--donuts", required=True)
    ap.add_argument("--excel", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    res = check(Period.parse(a.period), load_json(a.donuts), a.excel)
    save_json(a.out, res)
    print(res["status"])
    for i in res["issues"]:
        print(f" - [{i['severity']}] {i['check']}: {i['message']}")


if __name__ == "__main__":
    main()
