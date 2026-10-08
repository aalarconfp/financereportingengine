"""Repository safety check — run before every commit.

Scans the files Git would version (tracked + staged; or, before `git init`, every file not ignored by
.gitignore according to `git check-ignore` when available) and fails on:
  - forbidden extensions (financial data, Office files, PDFs, images, Power BI, archives)
  - files under regression/ or work/, `.env` files, report_data_*.json, any non-example JSON
  - GUID-like identifiers (dataset/tenant/workspace/Notion IDs), JWT/bearer tokens, private keys
  - personal absolute paths (Google Drive / user folders), e-mail addresses
  - large currency-formatted amounts (>= 1,000,000)
Exit code 1 when anything is found. Never bypass: fix the file or exclude it.

Usage:  python scripts/repo_safety_check.py            (inside the git repository)
        python scripts/repo_safety_check.py --all      (scan every non-ignored file in the folder)
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

FORBIDDEN_EXT = {".csv", ".tsv", ".xlsx", ".xlsm", ".xls", ".docx", ".doc", ".pdf", ".pbix", ".pbit", ".png", ".jpg",
                 ".jpeg", ".gif", ".zip", ".skill", ".pem", ".key", ".pfx", ".p12", ".log"}
ALLOWED_JSON = re.compile(r"^(examples/[^/]+\.example\.json|\.claude/settings\.json)$")
PATTERNS = {
    "guid": re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"),
    "jwt": re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"),
    "bearer": re.compile(r"Bearer\s+[A-Za-z0-9._-]{20,}"),
    "private_key": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    "personal_path": re.compile(r"(?i)(?:[A-Z]:\\(?:My Drive|Users\\[^\\\s]+)|/Users/[^/\s]+|My Drive\\Personal Finance)"),
    "email": re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
    "amount": re.compile(r"\$?\b\d{1,3}(?:,\d{3}){2,}(?:\.\d+)?\b"),
}
ALLOW_LITERALS = {"00000000-0000-0000-0000-000000000000", "noreply@anthropic.com"}
SELF = "scripts/repo_safety_check.py"


def candidate_files(scan_all: bool) -> list[str]:
    def git(*args):
        return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True)
    inside = git("rev-parse", "--is-inside-work-tree").returncode == 0
    if inside and not scan_all:
        files = set(git("ls-files").stdout.splitlines()) | set(git("diff", "--cached", "--name-only").stdout.splitlines())
        return sorted(f for f in files if f)
    files = []
    for dp, dns, fns in os.walk(ROOT):
        dns[:] = [d for d in dns if d not in (".git",)]
        for fn in fns:
            files.append(os.path.relpath(os.path.join(dp, fn), ROOT).replace(os.sep, "/"))
    if inside or git("--version").returncode == 0:
        # NUL-separated bytes: text mode turns "\n" into "\r\n" on Windows and git would not match the paths
        r = subprocess.run(["git", "check-ignore", "--no-index", "--stdin", "-z"], cwd=ROOT,
                           input="\0".join(files).encode("utf-8"), capture_output=True)
        ignored = set(r.stdout.decode("utf-8").split("\0"))
        files = [f for f in files if f not in ignored]
    return sorted(files)


def check(files: list[str]) -> list[str]:
    problems = []
    for f in files:
        low = f.lower()
        ext = os.path.splitext(low)[1]
        base = os.path.basename(low)
        if ext in FORBIDDEN_EXT:
            problems.append(f"{f}: forbidden file type '{ext}'")
            continue
        if low.startswith(("regression/", "work/")) or "/regression/" in low or "/work/" in low:
            problems.append(f"{f}: private runtime folder")
            continue
        if base == ".env" or (base.startswith(".env.") and base != ".env.example"):
            problems.append(f"{f}: environment file")
            continue
        if ext == ".json" and not ALLOWED_JSON.match(f):
            problems.append(f"{f}: JSON not allowed (results may contain financial data)")
            continue
        if f == SELF:
            continue
        path = os.path.join(ROOT, f)
        if not os.path.isfile(path):
            continue
        try:
            text = open(path, encoding="utf-8").read()
        except UnicodeDecodeError:
            problems.append(f"{f}: binary file")
            continue
        for name, rx in PATTERNS.items():
            for m in rx.finditer(text):
                if m.group(0) in ALLOW_LITERALS:
                    continue
                line = text.count("\n", 0, m.start()) + 1
                problems.append(f"{f}:{line}: {name} → {m.group(0)[:40]}")
    return problems


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true", help="scan all non-ignored files, not only tracked/staged")
    a = ap.parse_args()
    sys.stdout.reconfigure(errors="backslashreplace")  # Windows cp1252 consoles cannot print "→"
    files = candidate_files(a.all)
    problems = check(files)
    print(f"Scanned {len(files)} file(s).")
    if problems:
        print("REPOSITORY SAFETY CHECK: FAIL")
        for p in problems:
            print("  -", p)
        sys.exit(1)
    print("REPOSITORY SAFETY CHECK: PASS")


if __name__ == "__main__":
    main()
