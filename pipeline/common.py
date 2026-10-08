"""Shared helpers for the Monthly Financial Report pipeline (no financial logic, no secrets)."""
from __future__ import annotations

import calendar
import datetime as dt
import json
import os
from dataclasses import dataclass, asdict, field
from typing import Any

MESES = ["Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio", "Julio", "Agosto",
         "Septiembre", "Octubre", "Noviembre", "Diciembre"]
MONTHS_EN = ["January", "February", "March", "April", "May", "June", "July", "August",
             "September", "October", "November", "December"]

# Item statuses and severities (section 4 of the spec)
READY, MISSING, NOT_APPLICABLE, OPTIONAL, CONFIRM = (
    "READY", "MISSING", "NOT APPLICABLE", "OPTIONAL", "REQUIRES USER CONFIRMATION")
BLOCKING, WARNING, INFO = "BLOCKING", "WARNING", "INFO"

# Global pipeline states (section 28)
STATES = ["BLOCKED", "READY", "RUNNING", "VALIDATION", "READY_FOR_REPORT", "REPORT_GENERATED",
          "QA_PASSED", "COMPLETED", "ATTENTION", "FAILED"]

# Keys that must never be persisted (section 33)
_SECRET_KEYS = {"token", "access_token", "accesstoken", "authorization", "password", "secret", "bearer"}


@dataclass
class Period:
    year: int
    month: int

    @classmethod
    def parse(cls, s: str) -> "Period":
        y, m = s.split("-")
        return cls(int(y), int(m))

    @property
    def key(self) -> str:
        return f"{self.year:04d}_{self.month:02d}"

    @property
    def eom(self) -> dt.date:
        return dt.date(self.year, self.month, calendar.monthrange(self.year, self.month)[1])

    @property
    def prev(self) -> "Period":
        return Period(self.year - 1, 12) if self.month == 1 else Period(self.year, self.month - 1)

    @property
    def next(self) -> "Period":
        return Period(self.year + 1, 1) if self.month == 12 else Period(self.year, self.month + 1)

    @property
    def mdyy(self) -> str:  # 9-30-26
        e = self.eom
        return f"{e.month}-{e.day}-{e.year % 100:02d}"

    @property
    def ddmmyyyy(self) -> str:  # 30/09/2026 (MoneyPro Control Totals End Period)
        return self.eom.strftime("%d/%m/%Y")

    @property
    def mes(self) -> str:
        return MESES[self.month - 1]

    @property
    def month_en(self) -> str:
        return MONTHS_EN[self.month - 1]

    def placeholders(self) -> dict[str, str]:
        p = self.prev
        return {"{Y}": str(self.year), "{M}": str(self.month), "{PY}": str(p.year), "{PM}": str(p.month),
                "{EOM}": f"DATE({self.eom.year},{self.eom.month},{self.eom.day})",
                "{PEOM}": f"DATE({p.eom.year},{p.eom.month},{p.eom.day})",
                "{SOY}": f"DATE({self.year},1,1)"}


def scrub(obj: Any) -> Any:
    """Remove any secret-looking keys before writing JSON to disk."""
    if isinstance(obj, dict):
        return {k: scrub(v) for k, v in obj.items() if k.lower().replace("-", "_") not in _SECRET_KEYS}
    if isinstance(obj, list):
        return [scrub(v) for v in obj]
    return obj


def save_json(path: str, data: Any) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(scrub(data), fh, ensure_ascii=False, indent=1, default=str)


def load_json(path: str) -> Any:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def norm_key(k: str) -> str:
    """'[Income]' -> 'Income'; 'Calendario[Year]' stays; REST and MCP keys become comparable."""
    k = k.strip()
    if k.startswith("[") and k.endswith("]") and k.count("[") == 1:
        return k[1:-1]
    return k


def num(v: Any) -> float | None:
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(str(v).replace(",", ""))
    except ValueError:
        return None
