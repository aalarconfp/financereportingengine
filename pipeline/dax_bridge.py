"""DAX bridge: dax_queries.md (single source of Q0–Q8) -> resolved queries -> results JSON.

dax_queries.md stays the ONLY place where DAX lives. This module only:
  1. parses the fenced code blocks under each '## Qn' heading,
  2. splits blocks with several EVALUATE statements into sub-queries (REST executes one per call),
  3. expands {D} (Q3, Q5) into an EOM and a PEOM variant,
  4. substitutes {Y} {M} {PY} {PM} {EOM} {PEOM} {SOY},
  5. executes through a read-only transport and stores structured results.

Transports
  rest   -> powerbi_rest_client.PowerBIClient (Power BI Service, Azure CLI token in memory only).
            Runs where Azure CLI is logged in (the user's PC / a local Claude Code session).
  ingest -> results produced by Claude through the read-only Power BI modeling connector
            (Cowork sessions without a shell on the PC). Same query text from the plan; only the
            transport differs.

CLI
  python dax_bridge.py plan   --period 2026-10 --dax ../dax_queries.md --out plan_2026_10.json
  python dax_bridge.py run    --period 2026-10 --dax ../dax_queries.md --out results_2026_10.json   # REST (principal)
  python dax_bridge.py run    --period 2026-10 --retry-failed results_2026_10.json --out results_2026_10.json
                              # re-run only queries that failed (e.g. transient DNS); successful ones are carried over
  python dax_bridge.py ingest --plan plan_2026_10.json --raw raw_dir --out results_2026_10.json      # connector CSVs
"""
from __future__ import annotations

import argparse
import csv
import time
import glob
import io
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))  # powerbi_rest_client.py
from common import Period, save_json, load_json, now_iso, norm_key, WARNING  # noqa: E402

HEAD = re.compile(r"^## (Q\d)\b[^\n]*$", re.M)
FENCE = re.compile(r"```[a-zA-Z]*\n(.*?)```", re.S)
INFO_FUNCS = re.compile(r"\bINFO\.[A-Z]+\s*\(", re.I)


def parse_queries(md_text: str) -> dict[str, dict]:
    """Return {Qn: {'title':..., 'blocks':[dax,...]}} in document order."""
    out: dict[str, dict] = {}
    heads = list(HEAD.finditer(md_text))
    for i, h in enumerate(heads):
        end = heads[i + 1].start() if i + 1 < len(heads) else len(md_text)
        body = md_text[h.end():end]
        blocks = [b.strip() for b in FENCE.findall(body) if "EVALUATE" in b.upper()]
        out[h.group(1)] = {"title": h.group(0)[3:].strip(), "blocks": blocks}
    return out


def split_evaluates(block: str) -> list[str]:
    """Split a block containing several top-level EVALUATE statements (DEFINE stays with its EVALUATE)."""
    lines = block.splitlines()
    chunks, cur = [], []
    for ln in lines:
        if ln.lstrip().upper().startswith("EVALUATE") and any(l.strip().upper().startswith("EVALUATE") for l in cur):
            chunks.append("\n".join(cur))
            # keep preceding comment lines with the next statement
            cur = []
        cur.append(ln)
    if cur:
        chunks.append("\n".join(cur))
    cleaned = []
    for c in chunks:
        body = "\n".join(l for l in c.splitlines() if not l.strip().startswith("--")).strip()
        if body:
            cleaned.append(body)
    return cleaned


def build_plan(md_path: str, period: Period) -> dict:
    md = open(md_path, encoding="utf-8").read()
    ph = period.placeholders()
    queries = []
    for qid, q in parse_queries(md).items():
        for bi, block in enumerate(q["blocks"]):
            parts = split_evaluates(block)
            for pi, dax in enumerate(parts):
                variants = [("", dax)]
                if "{D}" in dax:
                    variants = [("_EOM", dax.replace("{D}", "{EOM}")), ("_PEOM", dax.replace("{D}", "{PEOM}"))]
                for suffix, d in variants:
                    for k, v in ph.items():
                        d = d.replace(k, v)
                    if re.search(r"\{[A-Z]+\}", d):
                        raise ValueError(f"Unresolved placeholder in {qid}: {re.findall(r'{[A-Z]+}', d)}")
                    sub = (chr(ord('a') + pi) if len(parts) > 1 else "")
                    queries.append({"id": f"{qid}{sub}{suffix}", "q": qid, "title": q["title"],
                                    "uses_info_function": bool(INFO_FUNCS.search(d)), "dax": d})
    return {"period": f"{period.year}-{period.month:02d}", "source": os.path.basename(md_path),
            "created": now_iso(), "queries": queries}


def _rows(raw_rows: list[dict]) -> list[dict]:
    return [{norm_key(k): v for k, v in r.items()} for r in raw_rows]


# Query statuses (section 2 of the spec)
OK, RETRY_SUCCESS = "OK", "RETRY_SUCCESS"
TRANSPORT_ERROR, AUTHENTICATION_ERROR, DAX_ERROR = "TRANSPORT_ERROR", "AUTHENTICATION_ERROR", "DAX_ERROR"
UNAVAILABLE_VIA_REST, NOT_RUN = "UNAVAILABLE_VIA_REST", "NOT_RUN"
SUCCESS = {OK, RETRY_SUCCESS}
RETRY_DELAYS = (1, 2, 4)      # seconds between attempts -> max 3 attempts (1s, 2s backoff; 4s reserved)
MAX_ATTEMPTS = 3


def classify(exc) -> tuple[str, bool]:
    """(status, retryable) for an exception raised by powerbi_rest_client. Never guesses data problems."""
    import powerbi_rest_client as prc
    if isinstance(exc, prc.PowerBITransportError):
        return TRANSPORT_ERROR, True
    if isinstance(exc, prc.AzureCLIError):
        return (TRANSPORT_ERROR, True) if getattr(exc, "kind", "auth") == "network" else (AUTHENTICATION_ERROR, False)
    if isinstance(exc, prc.PowerBIAuthenticationError):
        return AUTHENTICATION_ERROR, False
    if isinstance(exc, prc.PowerBIQueryError):
        return DAX_ERROR, False
    return TRANSPORT_ERROR, False   # unknown PowerBIError: reported, not retried


def execute_with_retry(client, q: dict, *, sleep=time.sleep, log=print) -> dict:
    """Run ONE planned query (same DAX text, no extra DAX) with bounded retry on transient transport errors."""
    from powerbi_rest_client import PowerBIError
    qid, history = q["id"], []
    for attempt in range(1, MAX_ATTEMPTS + 1):
        log(f"[REST] {qid} attempt {attempt}/{MAX_ATTEMPTS}")
        try:
            r = client.execute_dax(q["dax"])
            status = OK if attempt == 1 else RETRY_SUCCESS
            log(f"[REST] {qid} {status}")
            return {"status": status, "rows": _rows(r.rows), "attempts": attempt, "history": history}
        except PowerBIError as exc:
            st, retryable = classify(exc)
            msg = str(exc)[:1500]
            history.append({"attempt": attempt, "class": st, "error": msg[:300]})
            if q["uses_info_function"] and st == DAX_ERROR:
                log(f"[REST] {qid} UNAVAILABLE_VIA_REST (INFO.* no soportado por executeQueries)")
                return {"status": UNAVAILABLE_VIA_REST, "rows": [], "attempts": attempt, "error": msg, "history": history}
            log(f"[REST] {qid} {st.lower().replace('_', ' ')}")
            if retryable and attempt < MAX_ATTEMPTS:
                d = RETRY_DELAYS[attempt - 1]
                log(f"[REST] {qid} retry in {d}s")
                sleep(d)
                continue
            return {"status": st, "rows": [], "attempts": attempt, "error": msg, "history": history}
    raise AssertionError("unreachable")


def run_rest(plan: dict, client=None, previous: dict | None = None, *, sleep=time.sleep, log=print) -> dict:
    """Execute the plan via REST. With `previous`, only queries not already OK/RETRY_SUCCESS are re-run."""
    if client is None:
        from powerbi_rest_client import PowerBIClient  # read-only client
        client = PowerBIClient()
    res = {"period": plan["period"], "transport": "rest", "executed": now_iso(), "queries": {}, "warnings": [],
           "retry_policy": {"max_attempts": MAX_ATTEMPTS, "delays_s": list(RETRY_DELAYS[:MAX_ATTEMPTS - 1])}}
    auth_failed = False
    for q in plan["queries"]:
        prev = (previous or {}).get("queries", {}).get(q["id"])
        if prev and prev.get("status") in SUCCESS:
            res["queries"][q["id"]] = {**prev, "carried_from": previous.get("executed")}
            continue
        if auth_failed:
            res["queries"][q["id"]] = {"status": NOT_RUN, "rows": [], "error": "Omitida: autenticación requerida"}
            continue
        out = execute_with_retry(client, q, sleep=sleep, log=log)
        res["queries"][q["id"]] = out
        if out["status"] == UNAVAILABLE_VIA_REST:
            res["warnings"].append({"severity": WARNING, "item": q["id"],
                                    "message": "Q0 unavailable via REST (INFO.* not supported by executeQueries)"})
        if out["status"] == AUTHENTICATION_ERROR:
            auth_failed = True
    return res


def status_table(res: dict) -> str:
    lines = ["POWER BI QUERY STATUS", "---------------------"]
    for k, v in res["queries"].items():
        extra = f" (attempts {v['attempts']})" if v.get("attempts", 1) > 1 else ""
        extra += " [carried]" if v.get("carried_from") else ""
        lines.append(f"{k:8} {v['status']}{extra}")
    lines += ["", f"Overall: {res['summary']['overall']}"]
    if res["summary"]["overall"] == "AUTHENTICATION_REQUIRED":
        lines.append("La sesión de Azure CLI no es válida. Ejecutar:  az login --allow-no-subscriptions")
    elif res["summary"]["overall"].startswith("BLOCKED"):
        lines.append("Consultas críticas sin resultado: " + ", ".join(res["summary"]["failed_core"]))
    return "\n".join(lines)


def _read_csv_text(text: str) -> list[dict]:
    text = text.strip()
    if not text:
        return []
    rdr = csv.DictReader(io.StringIO(text))
    return [dict(r) for r in rdr]


def ingest(plan: dict, raw_dir: str) -> dict:
    """raw_dir contains <query_id>.csv (connector output) or <query_id>.error.txt."""
    res = {"period": plan["period"], "transport": "connector", "executed": now_iso(), "queries": {}, "warnings": []}
    for q in plan["queries"]:
        csv_p = os.path.join(raw_dir, f"{q['id']}.csv")
        err_p = os.path.join(raw_dir, f"{q['id']}.error.txt")
        if os.path.exists(csv_p):
            res["queries"][q["id"]] = {"status": "OK", "rows": _rows(_read_csv_text(open(csv_p, encoding="utf-8").read()))}
        elif os.path.exists(err_p):
            st = "UNAVAILABLE" if q["uses_info_function"] else "ERROR"
            res["queries"][q["id"]] = {"status": st, "rows": [], "error": open(err_p, encoding="utf-8").read()[:1500]}
        else:
            res["queries"][q["id"]] = {"status": "NOT_RUN", "rows": []}
    return res


def summarize(res: dict) -> dict:
    """Core = every query except Q0. Legacy status 'ERROR' (older files) counts as a failure."""
    st = {k: v["status"] for k, v in res["queries"].items()}
    core = {k: s for k, s in st.items() if not k.startswith("Q0")}
    failed = [k for k, s in core.items() if s not in SUCCESS]
    if any(s == AUTHENTICATION_ERROR for s in st.values()):   # includes Q0: an auth failure there stops the run
        overall = "AUTHENTICATION_REQUIRED"
    elif any(s == DAX_ERROR for s in core.values()):
        overall = "BLOCKED — DAX ERROR"
    elif failed:
        overall = "BLOCKED — POWER BI TRANSPORT"
    elif any(s == RETRY_SUCCESS for s in core.values()):
        overall = "PASS_WITH_WARNINGS"
    else:
        overall = "PASS"
    return {"all_core_ok": not failed, "statuses": st, "failed_core": failed, "overall": overall,
            "retried": [k for k, s in st.items() if s == RETRY_SUCCESS],
            "q0": next((s for k, s in st.items() if k.startswith("Q0")), NOT_RUN)}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["plan", "run", "ingest"])
    ap.add_argument("--period")
    ap.add_argument("--dax", default=os.path.join(os.path.dirname(__file__), "..", "dax_queries.md"))
    ap.add_argument("--plan")
    ap.add_argument("--raw")
    ap.add_argument("--retry-failed", help="previous results JSON: re-run only the queries that did not succeed")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    if a.cmd == "plan":
        save_json(a.out, build_plan(a.dax, Period.parse(a.period)))
    elif a.cmd == "run":
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
        prev = load_json(a.retry_failed) if a.retry_failed else None
        res = run_rest(build_plan(a.dax, Period.parse(a.period)), previous=prev)
        res["summary"] = summarize(res)
        save_json(a.out, res)
        print(status_table(res))
    else:
        res = ingest(load_json(a.plan), a.raw)
        res["summary"] = summarize(res)
        save_json(a.out, res)
    print(f"OK -> {a.out}")


if __name__ == "__main__":
    main()
