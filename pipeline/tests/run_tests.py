"""Pipeline test suite (no network, no Power BI, no Notion). Plain runner: python pipeline/tests/run_tests.py

Covers the spec test plan:
  D  negative transport test  (retry, exhaustion, BLOCKED, no PDF, no Notion)
  E  authentication test       (AUTHENTICATION_REQUIRED, no report)
  F  readiness                 (missing -> BLOCKED, ambiguous -> HUMAN_CONFIRMATION_REQUIRED,
                                known warning -> READY_WITH_WARNINGS, all correct -> READY)
  plus REST client error typing, --retry-failed, validation status normalisation, MoneyPro update
  equivalence with the official September workbook, and the Notion HUMAN RELEASE gate.
Writes regression/pipeline_tests.json (read by `pipeline.py certify`).
Optional env: MP_DIR = folder with the MoneyPro files (default: $FINANCE_DATA_ROOT/Transacciones Money Pro, else
../Transacciones Money Pro next to the engine).

PRIVATE FIXTURES: 14 tests use real (git-ignored) data: report_data_2026_09_GOLD.json, regression/*.json and the
MoneyPro Control Totals workbooks. When they are absent (e.g. a fresh clone), those tests are reported as SKIPPED —
never as PASS — and the suite cannot certify the pipeline (`pipeline.py certify` requires 0 skipped).
"""
from __future__ import annotations

import copy
import json
import os
import shutil
import subprocess
import sys
import tempfile
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
PIPE = os.path.dirname(HERE)
ENGINE = os.path.dirname(PIPE)
sys.path[:0] = [PIPE, ENGINE]

import powerbi_rest_client as prc  # noqa: E402
import dax_bridge as db  # noqa: E402
import validate as va  # noqa: E402
import readiness as rd  # noqa: E402
import moneypro_control as mpc  # noqa: E402
from common import Period, load_json, save_json  # noqa: E402

P = Period.parse("2026-09")
REG = os.path.join(ENGINE, "regression")
_DATA_ROOT = os.environ.get("FINANCE_DATA_ROOT") or os.path.join(ENGINE, "..")
MP_DIR = os.environ.get("MP_DIR") or os.path.join(_DATA_ROOT, "Transacciones Money Pro")
PRIVATE = {
    "GOLD": os.path.join(ENGINE, "report_data_2026_09_GOLD.json"),
    "BASELINE": os.path.join(REG, "baseline_2026_09.json"),
    "DONUTS": os.path.join(REG, "donuts_2026_09.json"),
    "LISTING": os.path.join(REG, "listing_2026_10_07.json"),
    "MP_XLSX": os.path.join(MP_DIR, "MoneyPro_Control_Totals_2026_Septiembre.xlsx"),
    "MP_PREV_XLSX": os.path.join(MP_DIR, "MoneyPro_Control_Totals_2026.xlsx"),
}
RESULTS: dict[str, list] = {}


def test(group, needs=()):
    def deco(fn):
        def run():
            missing = [k for k in needs if not os.path.exists(PRIVATE[k])]
            if missing:
                RESULTS.setdefault(group, []).append({"test": fn.__name__, "ok": None, "skipped": True,
                                                      "error": "private fixture missing: " + ", ".join(missing)})
                return
            try:
                fn()
                RESULTS.setdefault(group, []).append({"test": fn.__name__, "ok": True})
            except Exception as exc:  # noqa: BLE001
                RESULTS.setdefault(group, []).append({"test": fn.__name__, "ok": False,
                                                      "error": f"{type(exc).__name__}: {exc}",
                                                      "trace": traceback.format_exc()[-800:]})
        run.__name__ = fn.__name__
        TESTS.append(run)
        return fn
    return deco


TESTS = []


# ----------------------------------------------------------------------------------------- fakes
class Res:
    def __init__(self, rows):
        self.rows = rows


class FakeClient:
    """script: {query_id_substring or '*': [outcome, ...]}; outcome = 'ok' | exception instance."""

    def __init__(self, script=None, default="ok"):
        self.script = {k: list(v) for k, v in (script or {}).items()}
        self.default = default
        self.calls = []

    def execute_dax(self, dax):
        key = next((k for k in self.script if k != "*" and k in dax), "*")
        seq = self.script.get(key)
        out = seq.pop(0) if seq else self.default
        self.calls.append(key)
        if isinstance(out, Exception):
            raise out
        return Res([{"[Test]": 1}])

    def get_last_refresh(self):
        return None


def dns():
    return prc.PowerBITransportError("NameResolutionError getaddrinfo failed")


def plan():
    """Built from dax_queries.md (the single DAX source) — no stored plan needed."""
    return db.build_plan(os.path.join(ENGINE, "dax_queries.md"), P)


def silent(*_a, **_k):
    pass


# ----------------------------------------------------------------------------------------- REST
@test("rest")
def r1_normal_all_ok():
    res = db.run_rest(plan(), client=FakeClient(), sleep=silent, log=silent)
    res["summary"] = db.summarize(res)
    assert res["summary"]["overall"] == "PASS", res["summary"]
    assert all(v["status"] == "OK" for v in res["queries"].values())


@test("rest")
def r2_transient_then_success():
    sleeps = []
    q = next(x for x in plan()["queries"] if x["id"] == "Q3_EOM")
    out = db.execute_with_retry(FakeClient({"*": [dns(), "ok"]}), q, sleep=sleeps.append, log=silent)
    assert out["status"] == "RETRY_SUCCESS" and out["attempts"] == 2 and sleeps == [1], out


@test("rest")
def r3_retry_exhaustion_blocks():
    sleeps, logs = [], []
    q = next(x for x in plan()["queries"] if x["id"] == "Q8a")
    out = db.execute_with_retry(FakeClient({"*": [dns(), dns(), dns()]}), q, sleep=sleeps.append, log=logs.append)
    assert out["status"] == "TRANSPORT_ERROR" and out["attempts"] == 3 and sleeps == [1, 2], (out, sleeps)
    assert "[REST] Q8a attempt 3/3" in logs and "[REST] Q8a retry in 2s" in logs, logs


@test("rest")
def r4_dax_error_not_retried():
    q = next(x for x in plan()["queries"] if x["id"] == "Q1")
    c = FakeClient({"*": [prc.PowerBIQueryError("HTTP 400", status_code=400), "ok"]})
    out = db.execute_with_retry(c, q, sleep=silent, log=silent)
    assert out["status"] == "DAX_ERROR" and out["attempts"] == 1 and len(c.calls) == 1, out


@test("rest")
def r5_q0_unavailable_does_not_block():
    c = FakeClient({"INFO.MEASURES": [prc.PowerBIQueryError("HTTP 400", status_code=400)]})
    res = db.run_rest(plan(), client=c, sleep=silent, log=silent)
    res["summary"] = db.summarize(res)
    assert res["queries"]["Q0"]["status"] == "UNAVAILABLE_VIA_REST" and res["summary"]["overall"] == "PASS", res["summary"]


@test("rest")
def r6_az_network_error_is_retried():
    q = next(x for x in plan()["queries"] if x["id"] == "Q2")
    out = db.execute_with_retry(FakeClient({"*": [prc.AzureCLIError("getaddrinfo", kind="network"), "ok"]}), q,
                                sleep=silent, log=silent)
    assert out["status"] == "RETRY_SUCCESS", out


@test("rest")
def r7_retry_failed_reruns_only_failures():
    prev = db.run_rest(plan(), client=FakeClient({"WalletPrev": [dns(), dns(), dns()]}), sleep=silent, log=silent)
    prev["summary"] = db.summarize(prev)
    assert prev["summary"]["failed_core"] == ["Q2"], prev["summary"]
    c = FakeClient()
    res = db.run_rest(plan(), client=c, previous=prev, sleep=silent, log=silent)
    res["summary"] = db.summarize(res)
    assert len(c.calls) == 1 and res["summary"]["overall"] == "PASS", (c.calls, res["summary"])
    assert res["queries"]["Q1"].get("carried_from"), "successful queries must be carried over"


@test("rest")
def r8_client_maps_http_and_network_errors():
    import requests

    class R:
        def __init__(self, code):
            self.status_code, self.ok, self.text = code, 200 <= code < 300, "{}"

        def json(self):
            return {}

    cl = prc.PowerBIClient(dataset_id="00000000-0000-0000-0000-000000000000", azure_cli="az")
    cl.get_access_token = lambda: "x"   # never touches Azure CLI
    orig = requests.post
    try:
        for code, exc in ((503, prc.PowerBITransportError), (429, prc.PowerBITransportError),
                          (400, prc.PowerBIQueryError), (401, prc.PowerBIAuthenticationError)):
            requests.post = lambda *a, _c=code, **k: R(_c)
            try:
                cl.execute_dax('EVALUATE ROW("T",1)')
                raise AssertionError(f"HTTP {code} did not raise")
            except exc:
                pass

        def boom(*a, **k):
            raise requests.ConnectionError("getaddrinfo failed")
        requests.post = boom
        try:
            cl.execute_dax('EVALUATE ROW("T",1)')
            raise AssertionError("ConnectionError did not raise")
        except prc.PowerBITransportError:
            pass
    finally:
        requests.post = orig
    assert db.classify(prc.PowerBITransportError("x", 503)) == ("TRANSPORT_ERROR", True)
    assert db.classify(prc.AzureCLIError("expired", kind="auth")) == ("AUTHENTICATION_ERROR", False)


# ----------------------------------------------------------------------------------------- D / E
def _exhausted_results():
    res = db.run_rest(plan(), client=FakeClient({'[Asset Type]="Cash"': [dns()] * 6}), sleep=silent, log=silent)
    res["summary"] = db.summarize(res)
    return res


@test("rest", needs=("GOLD", "BASELINE"))
def d_negative_transport_blocks_everything():
    res = _exhausted_results()
    assert res["summary"]["overall"] == "BLOCKED — POWER BI TRANSPORT", res["summary"]
    v = va.check(P, res)
    assert v["gate"] == "BLOCKED" and v["blocked_reason"] == "BLOCKED — POWER BI TRANSPORT", v.get("blocked_reason")
    assert not any("Data Quality" in c["check"] for c in v["checks"]), "transport must never appear as data quality"
    with tempfile.TemporaryDirectory() as t:
        vp, rp = os.path.join(t, "v.json"), os.path.join(t, "r.json")
        save_json(vp, v)
        r = subprocess.run([sys.executable, os.path.join(PIPE, "regression.py"), "--baseline", os.path.join(REG, "baseline_2026_09.json"),
                            "--validations", vp, "--out", rp], capture_output=True, text=True)
        assert r.returncode == 2 and load_json(rp)["status"] == "REGRESSION NOT RUN", r.stdout + r.stderr
        work = os.path.join(t, "w")
        os.makedirs(work)
        save_json(os.path.join(work, "dax_results_2026_09.json"), res)
        save_json(os.path.join(work, "validations_2026_09.json"), v)
        save_json(os.path.join(work, "readiness_2026_09.json"), {"status": "READY", "overall": "READY", "items": [], "blocking_items": []})
        save_json(os.path.join(work, "moneypro_control_2026_09.json"), {"status": "PASS", "issues": []})
        r = subprocess.run([sys.executable, os.path.join(PIPE, "pipeline.py"), "status", "--period", "2026-09", "--work", work,
                            "--regression", os.path.join(t, "none.json")], capture_output=True, text=True)
        st = load_json(os.path.join(work, "pipeline_state_2026_09.json"))
        assert st["state"] == "BLOCKED" and not st["can_generate_report"] and not st["can_write_notion"], st
        save_json(os.path.join(work, "report_data_2026_09.json"), load_json(os.path.join(ENGINE, "report_data_2026_09_GOLD.json")))
        r = subprocess.run([sys.executable, os.path.join(ENGINE, "engine.py"), os.path.join(work, "report_data_2026_09.json"),
                            "missing.pdf", os.path.join(t, "out.pdf")], capture_output=True, text=True)
        assert r.returncode != 0 and "BLOCKED" in (r.stdout + r.stderr) and not os.path.exists(os.path.join(t, "out.pdf"))


@test("rest")
def e_authentication_required():
    c = FakeClient({"*": [prc.AzureCLIError("Please run 'az login'", kind="auth")]}, default="ok")
    res = db.run_rest(plan(), client=c, sleep=silent, log=silent)
    res["summary"] = db.summarize(res)
    assert res["summary"]["overall"] == "AUTHENTICATION_REQUIRED", res["summary"]
    assert len(c.calls) == 1, "after an auth failure the remaining queries are not attempted"
    assert "az login" in db.status_table(res)
    v = va.check(P, res)
    assert v["gate"] == "BLOCKED" and v["blocked_reason"] == "AUTHENTICATION_REQUIRED"
    pb = rd.powerbi_probe("rest", None, P, client=FakeClient({"*": [prc.AzureCLIError("expired", kind="auth")]}), sleep=silent, log=silent)
    assert pb["status"] == "BLOCKED" and "AUTHENTICATION_REQUIRED" in pb["detail"], pb


# ----------------------------------------------------------------------------------------- F readiness
def _listing():
    return load_json(os.path.join(REG, "listing_2026_10_07.json"))["entries"] + [
        {"name": "Report Engine/regression/baseline_2026_09.json", "type": "file", "size": 1, "mtimeMs": 1}]


def _staged(tmp, extra_same_end=False):
    tm = os.path.join(tmp, "Transacciones Money Pro")
    os.makedirs(tm)
    shutil.copy(os.path.join(MP_DIR, "MoneyPro_Control_Totals_2026_Septiembre.xlsx"), tm)
    shutil.copy(os.path.join(MP_DIR, "MoneyPro_Control_Totals_2026.xlsx"), tm)
    if extra_same_end:
        shutil.copy(os.path.join(MP_DIR, "MoneyPro_Control_Totals_2026_Septiembre.xlsx"),
                    os.path.join(tm, "MoneyPro_Control_Totals_2026_Septiembre_v2.xlsx"))
    return tmp


PBI_OK = {"status": "PASS", "transport": "rest", "detail": "fixture"}


@test("readiness", needs=("LISTING", "MP_XLSX", "MP_PREV_XLSX"))
def f1_all_inputs_correct_ready():
    with tempfile.TemporaryDirectory() as t:
        r = rd.check(P, _listing(), _staged(t), PBI_OK, "READY", {"status": "PASS", "issues": []})
        assert r["overall"] in ("READY", "READY_WITH_WARNINGS") and r["status"] == "READY", (r["overall"], r["blocking_items"])


@test("readiness", needs=("LISTING", "MP_XLSX", "MP_PREV_XLSX"))
def f2_known_warning_ready_with_warnings():
    with tempfile.TemporaryDirectory() as t:
        r = rd.check(P, _listing(), _staged(t), PBI_OK, "READY", {"status": "PASS_WITH_WARNINGS", "issues": []})
        assert r["overall"] == "READY_WITH_WARNINGS", r["overall"]
        assert any(i["status"] == "WARNING" and "no vigente" in i["item"] for i in r["items"]), "stale cumulative workbook = known warning"


@test("readiness", needs=("LISTING", "MP_XLSX", "MP_PREV_XLSX"))
def f3_missing_input_blocked_without_useless_confirmation():
    lst = [e for e in _listing() if "Net Worth Tracker 9-30-26" not in e["name"]]
    with tempfile.TemporaryDirectory() as t:
        r = rd.check(P, lst, _staged(t), PBI_OK, "READY")
        assert r["overall"] == "BLOCKED" and "Net Worth Tracker del mes" in r["blocking_items"], r["blocking_items"]
        assert not any(i["status"] == "HUMAN_CONFIRMATION_REQUIRED" for i in r["items"])


@test("readiness", needs=("LISTING", "MP_XLSX", "MP_PREV_XLSX"))
def f4_two_plausible_files_human_confirmation():
    lst = _listing() + [{"name": "Transacciones Money Pro/MoneyPro_Control_Totals_2026_Septiembre_v2.xlsx", "type": "file", "size": 1, "mtimeMs": 1}]
    with tempfile.TemporaryDirectory() as t:
        r = rd.check(P, lst, _staged(t, extra_same_end=True), PBI_OK, "READY")
        assert r["overall"] == "HUMAN_CONFIRMATION_REQUIRED", r["overall"]


@test("readiness")
def f5_rest_probe_transient_and_exhausted():
    ok = rd.powerbi_probe("rest", None, P, client=FakeClient({"*": [dns(), "ok"]}), sleep=silent, log=silent)
    assert ok["status"] == "PASS_WITH_WARNINGS", ok
    ko = rd.powerbi_probe("rest", None, P, client=FakeClient({"*": [dns(), dns(), dns()]}), sleep=silent, log=silent)
    assert ko["status"] == "BLOCKED" and "TRANSPORT" in ko["detail"], ko


@test("readiness", needs=("DONUTS",))
def f6_undated_donut_inherits_word_end_period():
    r = mpc.check_donuts(P, load_json(os.path.join(REG, "donuts_2026_09.json")))
    assert not any(i["severity"] in ("BLOCKING", "REQUIRES USER CONFIRMATION") for i in r), r


@test("readiness")
def f7_control_totals_workbook_released_after_check():
    """Synthetic workbook (no private data): End Period is read and the file handle is closed (Windows lock)."""
    import openpyxl
    name = "Transacciones Money Pro/MoneyPro_Control_Totals_Synthetic.xlsx"
    with tempfile.TemporaryDirectory() as t:
        path = os.path.join(t, *name.split("/"))
        os.makedirs(os.path.dirname(path))
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Totals"
        ws.append(["Group", "Item", "Value", "Start", "End"])
        ws.append(["Income", "Example", 1.0, "01/01/2018", P.ddmmyyyy])
        wb.save(path)
        r = rd.check(P, [{"name": name, "type": "file", "size": 1, "mtimeMs": 1}], t, PBI_OK, "READY")
        ct = [i for i in r["items"] if i["area"] == "Control Totals"]
        assert ct and ct[0]["status"] == "PASS", ct
        os.replace(path, path + ".moved")  # fails on Windows while the workbook is still open


# ----------------------------------------------------------------------------------------- validation
@test("validation")
def v1_emoji_status_normalised():
    assert va.norm_status("🟢 OK") == "OK" and va.norm_status("🔴 CHECK") == "CHECK" and va.norm_status("⚪ SIN MAPEO") == "SIN MAPEO"


@test("validation")
def v2_retry_success_rows_are_used():
    res = {"queries": {"Q1": {"status": "RETRY_SUCCESS", "rows": [{"a": 1}]}}}
    assert va.rows(res, "Q1") == [{"a": 1}]


# ----------------------------------------------------------------------------------------- MoneyPro
@test("moneypro", needs=("DONUTS", "MP_XLSX", "MP_PREV_XLSX"))
def m1_update_reproduces_official_september_workbook():
    import openpyxl
    with tempfile.TemporaryDirectory() as t:
        out = os.path.join(t, "rebuilt.xlsx")
        u = mpc.update(P, load_json(os.path.join(REG, "donuts_2026_09.json")),
                       os.path.join(MP_DIR, "MoneyPro_Control_Totals_2026.xlsx"), out)
        assert u["status"] == "PASS", u["status"]
        a = openpyxl.load_workbook(os.path.join(MP_DIR, "MoneyPro_Control_Totals_2026_Septiembre.xlsx"))["Totals"]
        b = openpyxl.load_workbook(out)["Totals"]
        assert a.max_row == b.max_row
        for r in range(1, a.max_row + 1):
            for c in range(1, 6):
                x, y = a.cell(r, c).value, b.cell(r, c).value
                same = abs(x - y) < 0.005 if isinstance(x, (int, float)) and isinstance(y, (int, float)) else x == y
                assert same, (r, c, x, y)
        assert len(u["new_categories"]) == 5 and not u["not_seen"]


@test("moneypro", needs=("DONUTS", "MP_XLSX"))
def m2_category_not_seen_is_kept_and_needs_human():
    d = copy.deepcopy(load_json(os.path.join(REG, "donuts_2026_09.json")))
    with tempfile.TemporaryDirectory() as t:
        base = os.path.join(t, "base.xlsx")
        shutil.copy(os.path.join(MP_DIR, "MoneyPro_Control_Totals_2026_Septiembre.xlsx"), base)
        items = d["donuts"]["Other Assets"]["items"]
        gone = next(k for k, v in items.items() if float(v) == 0)   # a 0-valued line disappears from the close
        items.pop(gone)
        u = mpc.update(P, d, base, os.path.join(t, "o.xlsx"))
        assert u["status"] == "HUMAN_CONFIRMATION_REQUIRED", u["status"]
        assert any(x["category"] == gone for x in u["not_seen"])
        import openpyxl
        ws = openpyxl.load_workbook(os.path.join(t, "o.xlsx"))["Totals"]
        assert any(ws.cell(r, 2).value == gone for r in range(2, ws.max_row + 1)), "row must be kept"


@test("moneypro", needs=("DONUTS", "MP_PREV_XLSX"))
def m3_wrong_end_period_or_monthly_view_blocks_and_writes_nothing():
    for mut in ("end", "start"):
        d = copy.deepcopy(load_json(os.path.join(REG, "donuts_2026_09.json")))
        if mut == "end":
            d["donuts"]["Income"]["end"] = "2026-08-31"
        else:
            d["donuts"]["Expenses"]["start"] = "2026-09-01"
        with tempfile.TemporaryDirectory() as t:
            o = os.path.join(t, "o.xlsx")
            u = mpc.update(P, d, os.path.join(MP_DIR, "MoneyPro_Control_Totals_2026.xlsx"), o)
            assert u["status"] == "BLOCKED" and not os.path.exists(o), (mut, u["status"])


@test("moneypro", needs=("DONUTS",))
def m4_input_is_never_overwritten():
    try:
        mpc.update(P, load_json(os.path.join(REG, "donuts_2026_09.json")), "x.xlsx", "x.xlsx")
        raise AssertionError("same in/out accepted")
    except SystemExit:
        pass


# ----------------------------------------------------------------------------------------- gates
@test("gates", needs=("GOLD",))
def g1_notion_requires_human_release():
    gold = os.path.join(ENGINE, "report_data_2026_09_GOLD.json")
    with tempfile.TemporaryDirectory() as t:
        w = os.path.join(t, "w")
        os.makedirs(w)
        res = db.run_rest(plan(), client=FakeClient(), sleep=silent, log=silent)
        res["summary"] = db.summarize(res)
        save_json(os.path.join(w, "dax_results_2026_09.json"), res)
        save_json(os.path.join(w, "validations_2026_09.json"), {"gate": "REQUIRES_REVIEW", "checks": [], "figures": {}})
        save_json(os.path.join(w, "readiness_2026_09.json"), {"status": "READY", "overall": "READY", "items": [], "blocking_items": []})
        save_json(os.path.join(w, "moneypro_control_2026_09.json"), {"status": "PASS", "issues": []})
        save_json(os.path.join(w, "report_data_2026_09.json"), load_json(gold))
        save_json(os.path.join(w, "qa_2026_09.json"), {"status": "QA_PASSED"})
        reg = os.path.join(t, "reg.json")
        save_json(reg, {"status": "REGRESSION PASSED", "pending_parts": []})
        pp = [sys.executable, os.path.join(PIPE, "pipeline.py")]
        base = ["--period", "2026-09", "--work", w, "--regression", reg]
        subprocess.run(pp + ["status"] + base, capture_output=True, text=True)
        st = load_json(os.path.join(w, "pipeline_state_2026_09.json"))
        assert st["state"] == "QA_PASSED" and not st["can_write_notion"], st
        nf = os.path.join(t, "n.json")
        save_json(nf, {"verified": True})
        r = subprocess.run(pp + ["complete"] + base + ["--notion", nf], capture_output=True, text=True)
        assert r.returncode != 0 and "HUMAN RELEASE" in (r.stdout + r.stderr), r.stdout + r.stderr
        subprocess.run(pp + ["release"] + base + ["--by", "test"], capture_output=True, text=True, check=True)
        st = load_json(os.path.join(w, "pipeline_state_2026_09.json"))
        assert st["can_write_notion"], st
        r = subprocess.run(pp + ["complete"] + base + ["--notion", nf], capture_output=True, text=True)
        st = load_json(os.path.join(w, "pipeline_state_2026_09.json"))
        assert st["state"] == "COMPLETED", (st["state"], r.stdout, r.stderr)


def main():
    for t in TESTS:
        t()
    groups = {}
    for g, v in RESULTS.items():
        ran = [x for x in v if not x.get("skipped")]
        skipped = len(v) - len(ran)
        failed = [x for x in ran if not x["ok"]]
        groups[g] = {"passed": not failed and skipped == 0, "failed": len(failed), "skipped": skipped,
                     "summary": f"{len(ran) - len(failed)}/{len(v)}" + (f" ({skipped} skipped)" if skipped else ""),
                     "tests": v}
    n_fail = sum(g["failed"] for g in groups.values())
    n_skip = sum(g["skipped"] for g in groups.values())
    allok = n_fail == 0 and n_skip == 0
    os.makedirs(REG, exist_ok=True)
    save_json(os.path.join(REG, "pipeline_tests.json"), {"passed": allok, "failed": n_fail, "skipped": n_skip, "groups": groups})
    for g, v in groups.items():
        state = "FAIL" if v["failed"] else ("SKIP" if v["skipped"] else "PASS")
        print(f"{g:11} {v['summary']}  {state}")
        for x in v["tests"]:
            if x.get("skipped"):
                print(f"   SKIP {x['test']}: {x['error']}")
            elif not x["ok"]:
                print(f"   FAIL {x['test']}: {x['error']}")
    if n_fail:
        print(f"TESTS FAILED ({n_fail})")
    elif n_skip:
        print(f"PASS WITH SKIPS — {n_skip} test(s) need private fixtures; not valid for certification")
    else:
        print("ALL TESTS PASS")
    sys.exit(1 if n_fail else 0)


if __name__ == "__main__":
    main()
