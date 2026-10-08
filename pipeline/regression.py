"""REGRESSION TEST — September 2026 (gold standard).

Part A (data):  validations JSON produced by the new pipeline for 2026-09 vs regression/baseline_2026_09.json.
Part B (pdf):   engine.py regenerates the report from report_data_2026_09_GOLD.json; page count and per-page
                text must match the published gold-standard report PDF.
Any difference => REGRESSION FAILED (STOP: investigate before enabling the real monthly flow).
"""
from __future__ import annotations

import argparse
import difflib
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import save_json, load_json, now_iso  # noqa: E402


def part_a(val: dict, base: dict) -> list[dict]:
    out, tol, rtol = [], base["tolerance"], base["ratio_tolerance"]
    f = val["figures"]
    exp_gate = base.get("expected_validation_gate")
    if exp_gate:
        out.append({"item": "validation.gate", "expected": exp_gate, "got": val["gate"], "ok": val["gate"] in exp_gate})

    def cmp(label, got, exp, t=tol):
        ok = got is not None and abs(got - exp) <= t
        out.append({"item": label, "expected": exp, "got": got, "ok": ok})

    for k, v in base["month"].items():
        cmp(f"month.{k}", f["month"].get(k), v, rtol if k in ("Largest", "Top3") else tol)
    for k, v in base["ytd"].items():
        cmp(f"ytd.{k}", f["ytd"].get(k), v)
    d, r = base["derived"], f["runway"]
    cmp("assets_EOM", f.get("assets_EOM"), d["assets_EOM"])
    cmp("cash_type_EOM", f["cash_type"]["EOM"], d["cash_type_EOM"])
    cmp("cash_type_PEOM", f["cash_type"]["PEOM"], d["cash_type_PEOM"])
    for k in ("ExpAll", "MonthsAll", "ExpAllPrev", "MonthsAllPrev", "ExpYTD", "AME_meas_all", "AME_meas_Y"):
        cmp(f"q4.{k}", r["q4"].get(k), d[k])
    cmp("AME_official", r["AME_official"], d["AME_official"])
    cmp("runway", r["runway"], d["runway"], rtol)
    cmp("runway_prev", r["runway_prev"], d["runway_prev"], rtol)
    cmp("LCR_meas_Y", r["measure_LCR_Y"], d["LCR_meas_Y"], 0.001)
    cmp("LCR_meas_all", r["measure_LCR_all"], d["LCR_meas_all"], 0.001)

    rec = f["reconciliation"]
    out.append({"item": "reconciliation.overall", "expected": base["reconciliation"]["overall_prefix"] + "…",
                "got": rec["overall"],
                "ok": re.sub(r"^[^A-ZÁÉÍÓÚ]+", "", str(rec["overall"] or "").upper()).startswith(base["reconciliation"]["overall_prefix"])})
    by_cat = {x["category"]: x for x in rec["lines"]}
    for _, cat, diff in base["reconciliation"]["lines"]:
        cmp(f"recon.{cat}.diff", (by_cat.get(cat) or {}).get("diff"), diff)
    for _, cat in base["reconciliation"]["sin_mapeo"]:
        got = (by_cat.get(cat) or {}).get("status")
        out.append({"item": f"recon.{cat}.status", "expected": "SIN MAPEO", "got": got, "ok": got == "SIN MAPEO"})
    ident = f["data_quality"]["identical_records"]
    for sub, amt, n in base["data_quality"]["identical_records"]:
        hit = any(x["sub"] == sub and x["amt"] is not None and abs(x["amt"] - amt) <= 0.01 and x["n"] == n for x in ident)
        out.append({"item": f"identical.{sub}.{amt}", "expected": n, "got": "found" if hit else "missing", "ok": hit})
    return out


def _norm(t: str) -> str:
    return re.sub(r"\s+", " ", t or "").strip()


def part_b(engine_dir: str, gold: str, dashboard_pdf: str, published: str, out_pdf: str) -> list[dict]:
    from pypdf import PdfReader
    sys.path.insert(0, engine_dir)
    import engine  # noqa: E402
    engine.build(gold, dashboard_pdf, out_pdf, regression=True)
    a, b = PdfReader(out_pdf), PdfReader(published)
    res = [{"item": "pdf.pages", "expected": len(b.pages), "got": len(a.pages), "ok": len(a.pages) == len(b.pages)}]
    for i in range(min(len(a.pages), len(b.pages))):
        ta, tb = _norm(a.pages[i].extract_text()), _norm(b.pages[i].extract_text())
        if ta != tb:
            ratio = difflib.SequenceMatcher(None, ta, tb).ratio()
            res.append({"item": f"pdf.page{i + 1}.text", "expected": tb[:160], "got": ta[:160], "ok": False, "similarity": round(ratio, 4)})
    res.append({"item": "pdf.text_pages_identical", "expected": len(b.pages),
                "got": len(b.pages) - sum(1 for x in res if x["item"].endswith(".text")),
                "ok": not any(x["item"].endswith(".text") for x in res)})
    return res


def regression_qa(val: dict, gold: str, dashboard: str, pdf: str, out: str) -> dict:
    """Same QA rules as pipeline.py qa, applied to the regenerated September report + validated figures."""
    from pypdf import PdfReader
    data = load_json(gold)
    n, pages = len(PdfReader(dashboard).pages), len(PdfReader(pdf).pages)
    text = " ".join((p.extract_text() or "") for p in PdfReader(pdf).pages)
    f = val["figures"]
    checks = [{"check": "Páginas = 2N + secciones", "ok": pages == 2 * n + len(data["sections"]), "detail": f"{pages}"},
              {"check": "Comentarios = N", "ok": len(data["comments"]) == n, "detail": f"{len(data['comments'])}/{n}"}]
    for label, v in (("Income", f["month"].get("Income")), ("Expense", f["month"].get("Expense")), ("Net Worth acumulado", f["ytd"].get("NWAcum"))):
        s_ = f"{round(v):,}" if v is not None else None
        checks.append({"check": f"{label} validado presente en el PDF", "ok": bool(s_ and s_ in text), "detail": s_})
    res = {"status": "QA_PASSED" if all(c["ok"] for c in checks) else "QA FAILED", "checks": checks, "run": now_iso()}
    save_json(out, res)
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--baseline", required=True)
    ap.add_argument("--validations")
    ap.add_argument("--engine-dir")
    ap.add_argument("--gold")
    ap.add_argument("--dashboard")
    ap.add_argument("--published")
    ap.add_argument("--out-pdf")
    ap.add_argument("--pdf-from", help="reuse Part B (PDF) results already PASSED in another environment (JSON)")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    base = load_json(a.baseline)
    result = {"period": base["period"], "run": now_iso(), "data": None, "pdf": None}
    if a.validations:
        val = load_json(a.validations)
        if val.get("gate") == "BLOCKED":
            result["status"] = "REGRESSION NOT RUN"
            result["reason"] = f"validation BLOCKED ({val.get('blocked_reason', 'ver checks')}): la regresión no se ejecuta sobre datos incompletos"
            save_json(a.out, result)
            print(result["status"], "—", result["reason"])
            raise SystemExit(2)
        result["data"] = part_a(val, base)
    if a.gold:
        result["pdf"] = part_b(a.engine_dir, a.gold, a.dashboard, a.published, a.out_pdf)
    elif a.pdf_from:
        prev = load_json(a.pdf_from)
        result["pdf"] = [dict(x, source=f"{os.path.basename(a.pdf_from)} ({prev.get('run')})") for x in (prev.get("pdf") or [])]
        if not result["pdf"]:
            raise SystemExit(f"{a.pdf_from} no contiene resultados de la Parte B (PDF)")
    parts = [p for p in (result["data"], result["pdf"]) if p is not None]
    failed = [x for p in parts for x in p if not x["ok"]]
    pending = [n for n, p in (("data", result["data"]), ("pdf", result["pdf"])) if p is None]
    result["status"] = "REGRESSION FAILED" if failed else ("REGRESSION PARTIAL" if pending else "REGRESSION PASSED")
    if result["status"] == "REGRESSION PASSED" and a.gold:
        result["qa"] = regression_qa(val, a.gold, a.dashboard, a.out_pdf, os.path.join(os.path.dirname(os.path.abspath(a.out)),
                                                                                       "qa_regression_2026_09.json"))
        if result["qa"]["status"] != "QA_PASSED":
            result["status"] = "REGRESSION FAILED"
    result["pending_parts"] = pending
    save_json(a.out, result)
    print(result["status"], "| pending:", pending or "none")
    for x in failed:
        print(f" DIFF {x['item']}: expected {x['expected']} got {x['got']}")


if __name__ == "__main__":
    main()
