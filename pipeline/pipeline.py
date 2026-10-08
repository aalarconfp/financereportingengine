"""MONTHLY FINANCIAL REPORT ENGINE — orchestrator / state machine.

Single entry point that decides whether each stage may run. It never computes financial figures itself:
  readiness.py        -> STEP 0 Data Readiness Gate
  moneypro_control.py -> MoneyPro control subprocess (validates outputs of the moneypro-control-totals skill)
  dax_bridge.py       -> Q0–Q8 from dax_queries.md (REST or connector ingest)
  validate.py         -> identities, independent runway, reconciliation gate, data quality
  regression.py       -> September 2026 gold-standard regression (must be PASSED once before real months)
  engine.py           -> PDF from report_data only
  Notion/PFOS         -> only after QA_PASSED

Commands (all write work/<YYYY_MM>/pipeline_state_<YYYY_MM>.json):
  status   --period P --work DIR [--regression FILE]   -> computes state from the artefacts present
  assemble --period P --work DIR --analysis FILE       -> report_data_<P>.json = analysis (meta/comments/sections) + data block
  qa       --period P --work DIR --pdf FILE --dashboard FILE
  complete --period P --work DIR --notion FILE         -> COMPLETED after Notion write was re-read and verified
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import Period, save_json, load_json, now_iso  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_REG = os.path.join(HERE, "..", "regression", "regression_2026_09.json")


def _load(path):
    return load_json(path) if path and os.path.exists(path) else None


def paths(p: Period, work: str) -> dict:
    k = p.key
    return {k2: os.path.join(work, v) for k2, v in {
        "readiness": f"readiness_{k}.json", "moneypro": f"moneypro_control_{k}.json", "results": f"dax_results_{k}.json",
        "validation": f"validations_{k}.json", "report_data": f"report_data_{k}.json", "state": f"pipeline_state_{k}.json",
        "qa": f"qa_{k}.json", "notion": f"notion_{k}.json"}.items()}


def compute(p: Period, work: str, reg_path: str | None) -> dict:
    f = paths(p, work)
    rd, mp, rs, va = (_load(f[x]) for x in ("readiness", "moneypro", "results", "validation"))
    reg = _load(reg_path or DEFAULT_REG)
    qa, prev = _load(f["qa"]), _load(f["state"]) or {}
    st = {"period": f"{p.year}-{p.month:02d}", "updated": now_iso(),
          "readiness": rd["status"] if rd else "NOT RUN", "moneypro_control": mp["status"] if mp else "NOT RUN",
          "dax": (rs.get("summary", {}).get("overall") or ("OK" if rs.get("summary", {}).get("all_core_ok") else "ERROR")) if rs else "NOT RUN",
          "q0": rs.get("summary", {}).get("q0") if rs else "NOT RUN", "transport": rs.get("transport") if rs else None,
          "validation": va["gate"] if va else "NOT RUN", "regression": reg["status"] if reg else "NOT RUN",
          "qa": qa["status"] if qa else "NOT RUN", "notion": prev.get("notion", "NOT RUN"), "blocking": [], "warnings": []}

    if rd and rd["status"] in ("BLOCKED", "HUMAN_CONFIRMATION_REQUIRED"):
        st["blocking"] += [f"Readiness ({rd.get('overall', rd['status'])}): {i}" for i in rd["blocking_items"]
                           if not prev.get("user_confirmed", {}).get(i)]
    if mp and mp["status"] == "BLOCKED":
        st["blocking"] += [f"MoneyPro Control: {i['message']}" for i in mp["issues"] if i["severity"] == "BLOCKING"]
    if mp and mp["status"] == "REQUIRES USER CONFIRMATION":
        st["blocking"] += [f"MoneyPro Control (confirmar): {i['check']} — {i['message']}" for i in mp["issues"]
                           if i["severity"] == "REQUIRES USER CONFIRMATION" and not prev.get("user_confirmed", {}).get(i["check"])]
    if rs and not rs.get("summary", {}).get("all_core_ok"):
        st["blocking"] += [f"DAX {k}: {v} ({rs['summary'].get('overall', '')})" for k, v in rs["summary"]["statuses"].items()
                           if v not in ("OK", "RETRY_SUCCESS") and not k.startswith("Q0")]
    if va and va["gate"] == "BLOCKED":
        st["blocking"] += [f"Validation: {c['check']} — {c['detail']}" for c in va["checks"] if c["severity"] == "BLOCKING"]
    if reg and reg["status"] == "REGRESSION FAILED":
        st["blocking"].append("Regression September 2026 FAILED — STOP e investigar")
    if va:
        st["warnings"] = [f"[{c['severity']}] {c['check']} — {c['detail']}" for c in va["checks"]
                          if c["severity"] in ("WARNING", "REQUIRES_REVIEW")]
    if rd:
        st["warnings"] += [f"Readiness [{i['status']}]: {i['item']} — {i['detail']}" for i in rd["items"]
                           if i["status"] not in ("PASS", "BLOCKED", "HUMAN_CONFIRMATION_REQUIRED")]
    st["human_release"] = prev.get("human_release")
    st["user_confirmed"] = prev.get("user_confirmed", {})

    # State machine (section 28)
    if st["blocking"]:
        state = "FAILED" if st["regression"] == "REGRESSION FAILED" or (qa and qa["status"] == "CRITICAL QA FAILED") else "BLOCKED"
    elif not rd:
        state = "BLOCKED"
        st["blocking"].append("Readiness gate not run")
    elif not (mp and rs and va):
        state = "RUNNING"
    elif st["regression"] != "REGRESSION PASSED":
        state = "VALIDATION"
        st["blocking"].append(f"Regression September 2026 = {st['regression']}: report generation disabled until PASSED")
    elif not os.path.exists(f["report_data"]):
        state = "READY_FOR_REPORT"
    elif not qa:
        state = "READY_FOR_REPORT" if not prev.get("pdf") else "REPORT_GENERATED"
    elif qa["status"] != "QA_PASSED":
        state = "FAILED" if qa["status"] == "CRITICAL QA FAILED" else "REPORT_GENERATED"
    elif st["notion"] == "VERIFIED":
        state = "COMPLETED"
    else:
        state = "QA_PASSED"
        if not st["human_release"]:
            st["blocking"].append("HUMAN RELEASE CONFIRMATION pendiente: Notion no se toca hasta "
                                  "`pipeline.py release --by <nombre>` tras revisar el PDF")
    st["state"] = state
    st["attention"] = bool(st["warnings"]) and state not in ("BLOCKED", "FAILED")
    st["can_generate_report"] = state in ("READY_FOR_REPORT", "REPORT_GENERATED")
    st["can_write_notion"] = state == "QA_PASSED" and bool(st["human_release"])
    st["pdf"] = prev.get("pdf")
    return st


def render(st: dict, p: Period) -> str:
    if st["state"] in ("BLOCKED", "FAILED"):
        out = ["MONTHLY REPORT — DATA READINESS", f"Period: {p.month_en} {p.year}", f"Status: {st['state']}", "", "Missing / blocking:"]
        out += [f"{i}. {b}" for i, b in enumerate(st["blocking"], 1)]
        out += ["", "Proceso detenido. No report was generated and no Notion records were modified."]
        return "\n".join(out)
    lines = [f"MONTHLY FINANCIAL REPORT ENGINE — {p.month_en} {p.year}", f"State: {st['state']}" + (" (ATTENTION)" if st["attention"] else "")]
    for k in ("readiness", "moneypro_control", "dax", "q0", "transport", "validation", "regression", "qa", "notion"):
        lines.append(f"  {k:17}: {st[k]}")
    if st["blocking"]:
        lines += ["Pending before next stage:"] + [f"  - {b}" for b in st["blocking"]]
    if st["warnings"]:
        lines += ["Attention (non-blocking review):"] + [f"  - {w}" for w in st["warnings"]]
    lines.append(f"Report generation enabled: {st['can_generate_report']} | Notion write enabled: {st['can_write_notion']}")
    return "\n".join(lines)


def assemble(p: Period, work: str, analysis_path: str, st: dict) -> None:
    if st["state"] not in ("READY_FOR_REPORT", "REPORT_GENERATED"):
        raise SystemExit(f"BLOCKED: state {st['state']} — report_data not assembled.")
    f = paths(p, work)
    an = load_json(analysis_path)
    for k in ("meta", "comments", "sections"):
        if k not in an:
            raise SystemExit(f"Analysis file lacks '{k}'.")
    va, rs, mp, rd = (load_json(f[x]) for x in ("validation", "results", "moneypro", "readiness"))
    an["pipeline"] = {
        "period": st["period"], "assembled": now_iso(), "state": st["state"],
        "dax_results": rs["queries"], "dax_transport": rs.get("transport"), "validations": va["checks"],
        "figures": va["figures"], "reconciliation": va["figures"].get("reconciliation"),
        "moneypro_control": {k: mp.get(k) for k in ("status", "excel", "source", "excel_totals", "donut_totals", "issues")},
        "readiness": rd["items"], "warnings": st["warnings"], "user_confirmed": st.get("user_confirmed", {}),
        "unresolved": [c for c in va["checks"] if c["result"] == "REVIEW"],
        "dashboard_values": an.get("dashboard_values"), "nwt_values": an.get("nwt_values"),
    }
    save_json(f["report_data"], an)


def qa(p: Period, work: str, pdf: str, dashboard: str) -> dict:
    from pypdf import PdfReader
    f = paths(p, work)
    data = load_json(f["report_data"])
    n = len(PdfReader(dashboard).pages)
    pages = len(PdfReader(pdf).pages)
    exp = 2 * n + len(data["sections"])
    text = " ".join((pg.extract_text() or "") for pg in PdfReader(pdf).pages)
    fig = data["pipeline"]["figures"]
    checks = [{"check": "Páginas = 2N + secciones", "ok": pages == exp, "critical": True, "detail": f"{pages} vs {exp}"},
              {"check": "Comentarios cubren todas las páginas del dashboard", "ok": len(data["comments"]) == n, "critical": True,
               "detail": f"{len(data['comments'])} vs {n}"}]
    for label, v in (("Income", fig["month"].get("Income")), ("Expense", fig["month"].get("Expense")),
                     ("Net Worth acumulado", fig["ytd"].get("NWAcum"))):
        s = f"{round(v):,}" if v is not None else None
        checks.append({"check": f"{label} validado aparece en el PDF", "ok": bool(s and s in text), "critical": False, "detail": s})
    pend = [c for c in data["comments"] for row in c.get("val", []) if len(row) > 3 and row[3] == "PEND"]
    checks.append({"check": "Sin validaciones PEND sin explicar", "ok": not pend, "critical": False, "detail": f"{len(pend)} PEND"})
    checks.append({"check": "Sin tokens/secretos en report_data", "ok": not any(w in open(f["report_data"], encoding="utf-8").read().lower()
                                                                               for w in ("bearer ", "access_token", "eyj0exai")), "critical": True, "detail": ""})
    crit = [c for c in checks if not c["ok"] and c["critical"]]
    res = {"status": "CRITICAL QA FAILED" if crit else "QA_PASSED",
           "attention": [c for c in checks if not c["ok"] and not c["critical"]], "checks": checks, "pdf": os.path.basename(pdf), "run": now_iso()}
    save_json(f["qa"], res)
    return res


def certify(reg_path: str | None) -> dict:
    """MONTHLY PIPELINE READY only when every prerequisite of section 19 is satisfied."""
    root = os.path.join(HERE, "..", "regression")
    reg = _load(reg_path or DEFAULT_REG) or {}
    tests = _load(os.path.join(root, "pipeline_tests.json")) or {}
    rd = _load(os.path.join(root, "readiness_2026_09.json")) or {}
    mp = _load(os.path.join(root, "moneypro_control_2026_09.json")) or {}
    mpu = _load(os.path.join(root, "moneypro_update_test_2026_09.json")) or {}
    qa = _load(os.path.join(root, "qa_regression_2026_09.json")) or {}
    req = [
        ("September regression", reg.get("status") == "REGRESSION PASSED" and not reg.get("pending_parts"), reg.get("status", "NOT RUN")),
        ("REST retry validated", tests.get("groups", {}).get("rest", {}).get("passed") is True, tests.get("groups", {}).get("rest", {}).get("summary", "NOT RUN")),
        ("Data Readiness validated", tests.get("groups", {}).get("readiness", {}).get("passed") is True
         and rd.get("overall") in ("READY", "READY_WITH_WARNINGS"), f"tests {tests.get('groups', {}).get('readiness', {}).get('summary', 'NOT RUN')}; Sept {rd.get('overall', 'NOT RUN')}"),
        ("MoneyPro controls validated", mp.get("status") in ("PASS", "PASS_WITH_WARNINGS") and mpu.get("status") == "PASS",
         f"control {mp.get('status', 'NOT RUN')}; update-test {mpu.get('status', 'NOT RUN')}"),
        ("QA", qa.get("status") == "QA_PASSED", qa.get("status", "NOT RUN")),
    ]
    ready = all(ok for _, ok, _ in req)
    lines = [f"{n:30} {'PASS' if ok else 'PENDING'}  ({d})" for n, ok, d in req]
    lines += ["", "MONTHLY PIPELINE READY" if ready else "MONTHLY PIPELINE NOT READY — el flujo mensual real sigue deshabilitado"]
    return {"ready": ready, "lines": lines}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["status", "assemble", "qa", "release", "complete", "confirm", "certify"])
    ap.add_argument("--by", help="release: who authorizes publishing to Notion (human)")
    ap.add_argument("--period", required=True)
    ap.add_argument("--work", required=True)
    ap.add_argument("--regression")
    ap.add_argument("--analysis")
    ap.add_argument("--pdf")
    ap.add_argument("--dashboard")
    ap.add_argument("--notion")
    ap.add_argument("--check", help="confirm: MoneyPro check name confirmed by the user (e.g. 'Assets end period')")
    a = ap.parse_args()
    p = Period.parse(a.period)
    f = paths(p, a.work)
    if a.cmd == "confirm":
        prev = _load(f["state"]) or {}
        prev.setdefault("user_confirmed", {})[a.check] = now_iso()
        save_json(f["state"], prev)
    st = compute(p, a.work, a.regression)
    if a.cmd == "assemble":
        assemble(p, a.work, a.analysis, st)
        st = compute(p, a.work, a.regression)
    elif a.cmd == "qa":
        if st["state"] not in ("READY_FOR_REPORT", "REPORT_GENERATED"):
            raise SystemExit(f"BLOCKED: state {st['state']}")
        st["pdf"] = os.path.basename(a.pdf)
        save_json(f["state"], st)
        qa(p, a.work, a.pdf, a.dashboard)
        st = compute(p, a.work, a.regression)
    elif a.cmd == "release":
        if st["state"] != "QA_PASSED":
            raise SystemExit(f"BLOCKED: HUMAN RELEASE solo es posible en QA_PASSED (state {st['state']}).")
        if not a.by:
            raise SystemExit("release requiere --by <nombre de quien autoriza>")
        st["human_release"] = {"by": a.by, "at": now_iso()}
        save_json(f["state"], st)
        st = compute(p, a.work, a.regression)
    elif a.cmd == "certify":
        cert = certify(a.regression)
        print("\n".join(cert["lines"]))
        raise SystemExit(0 if cert["ready"] else 3)
    elif a.cmd == "complete":
        if st["state"] != "QA_PASSED":
            raise SystemExit(f"BLOCKED: Notion stage requires QA_PASSED (state {st['state']}).")
        if not st.get("human_release"):
            raise SystemExit("BLOCKED: falta HUMAN RELEASE CONFIRMATION (pipeline.py release --by <nombre>).")
        n = load_json(a.notion)
        if not n.get("verified"):
            raise SystemExit("Notion write not verified (re-read pending) — state stays QA_PASSED.")
        save_json(f["notion"], n)
        st["notion"] = "VERIFIED"
        save_json(f["state"], st)
        st = compute(p, a.work, a.regression)
    save_json(f["state"], st)
    print(render(st, p))


if __name__ == "__main__":
    main()
