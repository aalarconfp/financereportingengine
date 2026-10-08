"""Generic charts for the monthly report (static PNG, Power BI palette)."""
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager as fm

for f in ["/usr/share/fonts/truetype/crosextra/Carlito-Regular.ttf",
          "/usr/share/fonts/truetype/crosextra/Carlito-Bold.ttf"]:
    try:
        fm.fontManager.addfont(f)
    except Exception:
        pass
plt.rcParams.update({"font.family": "Carlito", "font.size": 11,
                     "axes.edgecolor": "#B8BCC4", "axes.labelcolor": "#4A4F57",
                     "xtick.color": "#4A4F57", "ytick.color": "#4A4F57"})

BLUE = "#118DFF"; NAVY = "#12239E"; ORANGE = "#E66C37"; LIGHT = "#7FB8F0"; GRID = "#E6E8EC"; INK = "#2B2F36"


def _style(ax, axis="y"):
    for s in ["top", "right", "left"] if axis == "y" else ["top", "right"]:
        ax.spines[s].set_visible(False)
    ax.grid(axis=axis, color=GRID, lw=0.8)
    ax.set_axisbelow(True)
    ax.tick_params(length=0)


def liquidity(data, out, size=(6.4, 3.3)):
    """data = {"ame": float, "levels": [{"label": str, "value": float}, ...]} (stacked cumulative levels)."""
    ame = data["ame"]; cols = [NAVY, BLUE, LIGHT, "#B9D7F5"]
    fig, ax = plt.subplots(figsize=size, dpi=220)
    cum = 0
    for i, lv in enumerate(data["levels"]):
        v = lv["value"]
        ax.bar(i, v / 1e6, bottom=cum / 1e6, color=cols[i % 4], width=0.55)
        cum += v
        ax.text(i, cum / 1e6 + max(cum / 1e6 * 0.02, 0.3), f"Acum. ${cum/1e6:,.2f}M\n{cum/ame:,.2f} meses",
                ha="center", va="bottom", fontsize=9.5, color=INK)
        if v / 1e6 > 0.9:
            ax.text(i, (cum - v / 2) / 1e6, f"${v/1e6:,.2f}M", ha="center", va="center", fontsize=10, color="white", fontweight="bold")
    ax.set_xticks(range(len(data["levels"]))); ax.set_xticklabels([l["label"] for l in data["levels"]], fontsize=9.5)
    ax.set_ylabel("COP millones"); ax.set_ylim(0, cum / 1e6 * 1.25)
    _style(ax)
    fig.tight_layout(); fig.savefig(out); plt.close(fig)


def bars(data, out, size=(6.4, 2.9)):
    """data = {"labels": [...], "values": [...], "highlight_last": bool, "avg": {"from": idx, "label": str} | None}"""
    lab, val = data["labels"], data["values"]
    fig, ax = plt.subplots(figsize=size, dpi=220)
    cols = [BLUE] * len(val)
    if data.get("highlight_last", True):
        cols[-1] = NAVY
    ax.bar(range(len(val)), [v / 1e6 for v in val], color=cols, width=0.6)
    top = max(val) / 1e6 if val else 1
    for i, v in enumerate(val):
        ax.text(i, v / 1e6 + top * 0.02, f"{v/1e6:,.1f}", ha="center", fontsize=9, color=INK)
    if data.get("avg"):
        sub = val[data["avg"].get("from", 0):]
        avg = sum(sub) / len(sub)
        ax.axhline(avg / 1e6, color=ORANGE, lw=1.4, ls="--")
        ax.text(len(val) - 0.55, avg / 1e6 + top * 0.03, f"{data['avg'].get('label', 'Promedio')}\n${avg/1e6:,.1f}M", ha="right", fontsize=9, color=ORANGE)
    ax.set_xticks(range(len(val))); ax.set_xticklabels(lab, fontsize=9)
    ax.set_ylabel(data.get("ylabel", "COP millones")); ax.set_ylim(0, top * 1.15)
    _style(ax)
    fig.tight_layout(); fig.savefig(out); plt.close(fig)


def hbar(data, out, size=(6.4, 3.0)):
    """data = {"items": [{"label": str, "value": float}], "total": float}"""
    items, total = data["items"], data["total"]
    fig, ax = plt.subplots(figsize=size, dpi=220)
    ys = list(range(len(items)))[::-1]
    mx = max(i["value"] for i in items) / 1e6 or 1
    for y, it in zip(ys, items):
        v = it["value"]
        ax.barh(y, v / 1e6, color=NAVY if v else "#C9CDD3", height=0.6)
        ax.text(v / 1e6 + mx * 0.01, y, f"${v:,.0f}  ({v/total:.1%})", va="center", fontsize=9.5, color=INK)
    ax.set_yticks(ys); ax.set_yticklabels([i["label"] for i in items], fontsize=10)
    ax.set_xlim(0, mx * 1.5); ax.set_xlabel("COP millones")
    _style(ax, axis="x")
    fig.tight_layout(); fig.savefig(out); plt.close(fig)


KINDS = {"liquidity": liquidity, "bars": bars, "hbar": hbar}
