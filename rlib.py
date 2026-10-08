from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib.colors import HexColor, white
from reportlab.platypus import Paragraph, Table, TableStyle, Frame, KeepInFrame, Image, Spacer
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.enums import TA_LEFT, TA_RIGHT, TA_CENTER

import os as _os
# Carlito (Linux) or Calibri (Windows, metric-compatible). Presentation only: same text and layout metrics.
_FONT_SETS = [
    (_os.environ.get("REPORT_FONT_DIR", "/usr/share/fonts/truetype/crosextra/"),
     ("Carlito-Regular.ttf", "Carlito-Bold.ttf", "Carlito-Italic.ttf", "Carlito-BoldItalic.ttf")),
    (_os.path.join(_os.environ.get("WINDIR", r"C:\Windows"), "Fonts"),
     ("calibri.ttf", "calibrib.ttf", "calibrii.ttf", "calibriz.ttf")),
]
for _dir, _files in _FONT_SETS:
    if all(_os.path.exists(_os.path.join(_dir, f)) for f in _files):
        break
else:
    raise SystemExit("Fuente Carlito/Calibri no encontrada (definir REPORT_FONT_DIR)")
F = _dir if _dir.endswith(("/", "\\")) else _dir + _os.sep
for _name, _file in zip(("C", "CB", "CI", "CBI"), _files):
    pdfmetrics.registerFont(TTFont(_name, _os.path.join(_dir, _file)))
pdfmetrics.registerFontFamily("C", normal="C", bold="CB", italic="CI", boldItalic="CBI")

W, H = 996, 576
NAVY = HexColor("#12239E"); BLUE = HexColor("#118DFF"); ORANGE = HexColor("#E66C37")
INK = HexColor("#2B2F36"); MUTED = HexColor("#6B7079"); RULE = HexColor("#D9DCE1")
BAND = HexColor("#F3F4F6"); DARK = HexColor("#252423"); GREEN = HexColor("#1E7F4F"); AMBER = HexColor("#B26A00")
SOFTBLUE = HexColor("#EAF3FF"); SOFTORANGE = HexColor("#FDF0EA"); SOFTGREEN = HexColor("#E9F5EE")

body = ParagraphStyle("b", fontName="C", fontSize=10.8, leading=13.8, textColor=INK)
small = ParagraphStyle("s", parent=body, fontSize=8.8, leading=11, textColor=MUTED)
h = ParagraphStyle("h", fontName="CB", fontSize=11.6, leading=14.5, textColor=NAVY, spaceBefore=7, spaceAfter=2)
cell = ParagraphStyle("c", fontName="C", fontSize=9.4, leading=11.4, textColor=INK)
cellb = ParagraphStyle("cb", parent=cell, fontName="CB")
cellr = ParagraphStyle("cr", parent=cell, alignment=TA_RIGHT)
cellrb = ParagraphStyle("crb", parent=cellr, fontName="CB")
headc = ParagraphStyle("hc", parent=cell, fontName="CB", textColor=white)
headcr = ParagraphStyle("hcr", parent=headc, alignment=TA_RIGHT)


def m(x, dec=0):
    if x is None:
        return "—"
    s = f"{abs(x):,.{dec}f}"
    return ("−$" if x < 0 else "$") + s


def pct(x, dec=1):
    return f"{x*100:,.{dec}f}%".replace("-", "−")


def P(t, st=body):
    return Paragraph(t, st)


def header(c, title, subtitle=None, kicker="REPORTE FINANCIERO"):
    c.setFillColor(NAVY); c.rect(0, H - 6, W, 6, stroke=0, fill=1)
    c.setFont("CB", 8.5); c.setFillColor(MUTED)
    c.drawString(36, H - 26, kicker)
    c.drawRightString(W - 36, H - 26, "Cash Basis · Costo histórico · COP")
    c.setFont("CB", 19); c.setFillColor(INK)
    c.drawString(36, H - 52, title)
    if subtitle:
        c.setFont("C", 10.5); c.setFillColor(MUTED)
        c.drawString(36, H - 68, subtitle)
    c.setStrokeColor(RULE); c.setLineWidth(0.8)
    c.line(36, H - 77, W - 36, H - 77)


def footer(c, page, src="", label="Reporte Financiero"):
    c.setStrokeColor(RULE); c.setLineWidth(0.6); c.line(36, 30, W - 36, 30)
    c.setFont("C", 7.8); c.setFillColor(MUTED)
    c.drawString(36, 18, src)
    c.drawRightString(W - 36, 18, f"{label} · {page}")


def frame(c, x, y, w, hgt, flow, pad=0):
    fr = Frame(x, y, w, hgt, leftPadding=pad, rightPadding=pad, topPadding=0, bottomPadding=0, showBoundary=0)
    k = KeepInFrame(w - 2 * pad, hgt, flow, mode="shrink")
    fr.addFromList([k], c)


def table(rows, widths, head=True, zebra=True, bold_last=False, align_right_from=1, hl_rows=(), fs=None, right=None):
    data = []
    for i, r in enumerate(rows):
        out = []
        for j, v in enumerate(r):
            isr = (j in right) if right is not None else (j >= align_right_from)
            if isinstance(v, str) or v is None:
                v = "" if v is None else v
                if head and i == 0:
                    st = headcr if isr else headc
                elif (bold_last and i == len(rows) - 1) or i in hl_rows:
                    st = cellrb if isr else cellb
                else:
                    st = cellr if isr else cell
                if fs:
                    st = ParagraphStyle("x", parent=st, fontSize=fs, leading=fs * 1.22)
                out.append(Paragraph(v, st))
            else:
                out.append(v)
        data.append(out)
    t = Table(data, colWidths=widths, repeatRows=1 if head else 0)
    s = [("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("TOPPADDING", (0, 0), (-1, -1), 2.6),
         ("BOTTOMPADDING", (0, 0), (-1, -1), 2.6), ("LEFTPADDING", (0, 0), (-1, -1), 4), ("RIGHTPADDING", (0, 0), (-1, -1), 4),
         ("LINEBELOW", (0, 0), (-1, -1), 0.4, RULE)]
    if head:
        s += [("BACKGROUND", (0, 0), (-1, 0), DARK)]
    if zebra:
        for i in range(1 if head else 0, len(rows)):
            if (i % 2 == 0):
                s.append(("BACKGROUND", (0, i), (-1, i), HexColor("#F7F8FA")))
    if bold_last:
        s.append(("LINEABOVE", (0, len(rows) - 1), (-1, len(rows) - 1), 0.9, INK))
    for r in hl_rows:
        s.append(("BACKGROUND", (0, r), (-1, r), SOFTBLUE))
    t.setStyle(TableStyle(s))
    return t


def callout(text, color=NAVY, bg=SOFTBLUE, width=None, fs=None):
    st = ParagraphStyle("co", parent=body, fontSize=fs or body.fontSize, leading=(fs or body.fontSize) * 1.3)
    t = Table([[Paragraph(text, st)]], colWidths=[width] if width else None)
    t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), bg), ("LINEBEFORE", (0, 0), (0, -1), 3, color),
                           ("LEFTPADDING", (0, 0), (-1, -1), 8), ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                           ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5)]))
    return t


def kpi(c, x, y, w, hgt, label, value, sub, accent=NAVY):
    c.setFillColor(BAND); c.roundRect(x, y, w, hgt, 4, stroke=0, fill=1)
    c.setFillColor(accent); c.rect(x, y, 3, hgt, stroke=0, fill=1)
    c.setFont("CB", 8); c.setFillColor(MUTED); c.drawString(x + 10, y + hgt - 15, label.upper())
    c.setFont("CB", 18); c.setFillColor(INK); c.drawString(x + 10, y + hgt - 37, value)
    c.setFont("C", 8.4); c.setFillColor(MUTED)
    for i, line in enumerate(sub.split("\n")):
        c.drawString(x + 10, y + 9 + (len(sub.split("\n")) - 1 - i) * 10, line)

bodyL = ParagraphStyle("bL", parent=body, fontSize=12.2, leading=15.8)
hL = ParagraphStyle("hL", parent=h, fontSize=12.8, leading=16, spaceBefore=9, spaceAfter=2.5)
smallL = ParagraphStyle("sL", parent=small, fontSize=9.4, leading=11.6)
