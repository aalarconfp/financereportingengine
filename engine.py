"""Personal Finance Monthly Report engine.

Usage:
    python3 engine.py report_data.json "Dashboard Personal Financials Cash Basis M-D-YY.pdf" Reporte_Financiero_Mes_Año.pdf

report_data.json holds ONLY content (text + validated figures). Layout, styles and page order are fixed here.
Structure produced:  [visual p1, comment p1, ..., visual pN, comment pN] + section pages (A..L).
The engine never touches the Power BI model; it only formats data already validated with read-only DAX.
"""
import io, json, os, sys
from reportlab.pdfgen import canvas
from reportlab.platypus import Spacer, Image
from reportlab.lib.styles import ParagraphStyle
from pypdf import PdfReader, PdfWriter
from rlib import *  # styles, header/footer/frame/table/callout/kpi
import charts

TONES = {"blue": (NAVY, SOFTBLUE), "green": (GREEN, SOFTGREEN), "amber": (AMBER, SOFTORANGE),
         "gray": (BLUE, BAND), "orange": (ORANGE, SOFTORANGE)}
STYLES = {"body": body, "L": bodyL, "small": small, "smallL": smallL}
HSTYLES = {"body": h, "L": hL}
CTX = {}


def _hex(col):
    return "#" + col.hexval()[2:]


def blocks_to_flow(blocks, width, workdir):
    out = []
    for b in blocks:
        t = b["t"]
        if t == "h":
            out.append(P(b["text"], HSTYLES[b.get("s", "body")]))
        elif t == "p":
            out.append(P(b["text"], STYLES[b.get("s", "body")]))
        elif t == "sp":
            out.append(Spacer(1, b.get("h", 6)))
        elif t == "bullets":
            st = STYLES[b.get("s", "body")]
            out += [P("• " + i, st) for i in b["items"]]
        elif t == "table":
            out.append(table(b["rows"], b["w"], right=set(b.get("right", [])), hl_rows=tuple(b.get("hl", [])),
                             bold_last=b.get("bold_last", False), fs=b.get("fs")))
        elif t == "callout":
            col, bg = TONES[b.get("tone", "blue")]
            out.append(callout(b["text"], color=col, bg=bg, width=b.get("w", width), fs=b.get("fs")))
        elif t == "chart":
            CTX["chart_n"] = CTX.get("chart_n", 0) + 1
            path = os.path.join(workdir, f"chart_{CTX['chart_n']}.png")
            size = tuple(b.get("size", (6.4, 3.0)))
            charts.KINDS[b["kind"]](b["data"], path, size=size)
            w = b.get("w", width)
            out.append(Image(path, width=w, height=w * size[1] / size[0]))
        else:
            raise ValueError(f"unknown block type {t}")
    return out


def comment_page(c, p, pageno, meta):
    header(c, f"Comentario financiero — Dashboard página {p['n']}", p["name"], kicker=meta["kicker"])
    frame(c, 36, H - 122, W - 72, 40, [callout("<b>Lectura clave.</b> " + p["key"], width=W - 72, fs=12)])
    top = H - 130
    ok = {"OK": "<font color='#1E7F4F'><b>Cuadra</b></font>", "NOTE": "<font color='#B26A00'><b>Nota</b></font>",
          "DES": "<font color='#6B7079'><b>Diseño</b></font>", "PEND": "<font color='#B3261E'><b>Pending validation</b></font>"}
    rows = [["Concepto", "Valor", "Ratio", "Estado"]] + [r[:3] + [ok.get(r[3], r[3])] for r in p["val"]]
    left = [P("1. Qué muestra la visual", hL), P(p["s1"], bodyL), Spacer(1, 4), P("2. Validación contra Power BI", hL),
            table(rows, [150, 162, 70, 58], right={1, 2, 3}, fs=10.2), Spacer(1, 4),
            P(f"Consulta DAX de solo lectura (EVALUATE) sobre el modelo 'Dashboard Personal Finance', {meta['query_date']}.", smallL)]
    frame(c, 36, 40, 440, top - 40, left)
    right = [P("3. Qué significa financieramente", hL), P(p["s3"], bodyL),
             P(f"4. Comparación con {meta['prev_month_es'].lower()} / YTD", hL), P(p["s4"], bodyL),
             P("5. Riesgo / oportunidad", hL), P(p["s5"], bodyL),
             P("6. Limitación metodológica", hL), P(p["s6"], bodyL)]
    frame(c, 500, 40, W - 536, top - 40, right)
    footer(c, pageno, meta["footer_src"], meta["label"])


def section_page(c, s, pageno, meta, workdir):
    header(c, s["title"], s.get("subtitle"), kicker=meta["kicker"])
    kind = s.get("kind", "cols")
    if kind == "kpis":
        x0, y0, cw, ch, gap = 36, H - 88, 178, 70, 8
        for i, k in enumerate(s["kpis"]):
            r, col = divmod(i, 5)
            acc = {"orange": ORANGE, "blue": BLUE}.get(k.get("accent"), NAVY)
            kpi(c, x0 + col * (cw + gap), y0 - (r + 1) * ch - r * gap, cw, ch, k["label"], k["value"], k["sub"], acc)
        nrows = (len(s["kpis"]) + 4) // 5
        nx = 36 + (len(s["kpis"]) % 5) * (cw + gap) if len(s["kpis"]) % 5 else None
        if nx is not None and s.get("note"):
            c.setFont("CI", 8.2); c.setFillColor(MUTED)
            base = y0 - nrows * ch - (nrows - 1) * gap
            for i, line in enumerate(s["note"]):
                c.drawString(nx, base + 50 - i * 12, line)
        ytop = y0 - nrows * ch - (nrows - 1) * gap - 10
        frame(c, 36, 40, W - 72, ytop - 40, blocks_to_flow(s.get("below", []), W - 72, workdir))
    elif kind == "quad":
        bw = (W - 72 - 14) / 2
        pos = [(36, H / 2 - 22, H / 2 - 62), (36 + bw + 14, H / 2 - 22, H / 2 - 62), (36, 40, H / 2 - 72), (36 + bw + 14, 40, H / 2 - 72)]
        for (x, y, hh), bx in zip(pos, s["boxes"]):
            col, bg = TONES[bx.get("tone", "blue")]
            txt = f"<b><font color='{_hex(col)}'>{bx['title']}</font></b><br/>" + "<br/>".join("• " + i for i in bx["items"])
            frame(c, x, y, bw, hh, [callout(txt, color=col, bg=bg, width=bw, fs=12.4)])
    else:
        for col in s["cols"]:
            frame(c, col["x"], 40, col["w"], H - 128, blocks_to_flow(col["blocks"], col["w"], workdir))
    footer(c, pageno, meta["footer_src"], meta["label"])


def _gate(data_path, regression):
    """Precondition only (no financial logic): the pipeline must have reached READY_FOR_REPORT."""
    if regression:
        return
    key = os.path.basename(data_path).replace("report_data_", "").replace(".json", "")
    state_p = os.path.join(os.path.dirname(os.path.abspath(data_path)), f"pipeline_state_{key}.json")
    if not os.path.exists(state_p):
        raise SystemExit(f"BLOCKED: {os.path.basename(state_p)} no existe. Ejecute pipeline.py (Data Readiness + Validation) antes del PDF.")
    st = json.load(open(state_p, encoding="utf-8"))
    if st.get("state") not in ("READY_FOR_REPORT", "REPORT_GENERATED") or st.get("readiness") != "READY" \
            or st.get("validation") not in ("PASS", "PASS_WITH_WARNINGS", "REQUIRES_REVIEW") \
            or st.get("regression") != "REGRESSION PASSED":
        raise SystemExit(f"BLOCKED: estado del pipeline = {st.get('state')} (readiness {st.get('readiness')}, "
                         f"validation {st.get('validation')}, regression {st.get('regression')}). No se genera el PDF.")


def build(data_path, dashboard_pdf, out_pdf, workdir=None, regression=False):
    _gate(data_path, regression)
    data = json.load(open(data_path, encoding="utf-8"))
    meta = data["meta"]
    meta.setdefault("kicker", f"REPORTE FINANCIERO · {meta['month_es'].upper()} {meta['year']}")
    meta.setdefault("label", f"Reporte Financiero — {meta['month_es']} {meta['year']}")
    workdir = workdir or os.path.dirname(os.path.abspath(out_pdf))
    reader = PdfReader(dashboard_pdf)
    n = len(reader.pages)
    comments = sorted(data["comments"], key=lambda p: p["n"])
    if [p["n"] for p in comments] != list(range(1, n + 1)):
        raise SystemExit(f"Dashboard has {n} pages but comments cover {[p['n'] for p in comments]}")
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(W, H))
    for i, p in enumerate(comments):
        comment_page(c, p, 2 * (i + 1), meta); c.showPage()
    for j, s in enumerate(data["sections"]):
        section_page(c, s, 2 * n + 1 + j, meta, workdir); c.showPage()
    c.save()
    gen = PdfReader(io.BytesIO(buf.getvalue()))
    w = PdfWriter()
    for i in range(n):
        w.add_page(reader.pages[i]); w.add_page(gen.pages[i])
    for i in range(n, len(gen.pages)):
        w.add_page(gen.pages[i])
    w.add_metadata({"/Title": meta["label"], "/Author": "Personal Finance FP&A"})
    with open(out_pdf, "wb") as fh:
        w.write(fh)
    print(f"OK {out_pdf}: {len(w.pages)} páginas ({n} visuales + {n} comentarios + {len(data['sections'])} secciones)")


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if a != "--regression"]
    build(*args[:3], regression="--regression" in sys.argv)
