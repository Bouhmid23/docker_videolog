"""Génération d'un rapport de suivi athlète PDF propre (volume, PMC, conformité, records, sports)."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fpdf import FPDF

from services.sports_catalog import get_sport_meta

_REPLACEMENTS = {
    "\u2019": "'",
    "\u2018": "'",
    "\u201c": '"',
    "\u201d": '"',
    "\u2013": "-",
    "\u2014": "-",
    "\u2026": "...",
    "\u2192": "->",
    "\u2190": "<-",
    "\u2191": "+",
    "\u2193": "-",
    "\u0153": "oe",
    "\u0152": "OE",
    "\u00a0": " ",
    "\u202f": " ",
}


def _safe(text: Any) -> str:
    """Convertit un texte UTF-8 en Latin-1 sûr pour les polices PDF standard (Helvetica)."""
    s = "" if text is None else str(text)
    for src, dst in _REPLACEMENTS.items():
        s = s.replace(src, dst)
    return s.encode("latin-1", "replace").decode("latin-1")


def _fmt_hours(duration_s: float) -> str:
    total_min = int(round(duration_s / 60.0))
    h, m = divmod(total_min, 60)
    return f"{h}h {m:02d}m" if h else f"{m} min"


def _fmt_delta(start: float | None, end: float | None) -> str:
    if start is None or end is None:
        return "-"
    diff = end - start
    sign = "+" if diff > 0 else ""
    return f"{sign}{diff:.1f}"


class _ReportPDF(FPDF):
    def __init__(self, org_name: str, generated_at: str) -> None:
        super().__init__(orientation="P", unit="mm", format="A4")
        self._org_name = _safe(org_name)
        self._generated_at = _safe(generated_at)
        self.set_auto_page_break(auto=True, margin=16)

    def footer(self) -> None:
        self.set_y(-12)
        self.set_draw_color(226, 232, 240)
        self.line(12, self.get_y(), 198, self.get_y())
        self.set_y(-10)
        self.set_font("Helvetica", "", 8)
        self.set_text_color(100, 116, 139)
        self.cell(
            120,
            6,
            _safe(f"Videolog - {self._org_name} | Genere le {self._generated_at}"),
            align="L",
        )
        self.cell(0, 6, f"Page {self.page_no()}/{{nb}}", align="R")


def _draw_section_title(pdf: _ReportPDF, title: str) -> None:
    pdf.ln(3)
    y = pdf.get_y()
    pdf.set_fill_color(24, 171, 248)
    pdf.rect(12, y + 0.8, 2.2, 5.2, style="F")
    pdf.set_x(16.5)
    pdf.set_font("Helvetica", "B", 11)
    pdf.set_text_color(15, 23, 42)
    pdf.cell(0, 7, _safe(title), new_x="LMARGIN", new_y="NEXT")
    pdf.ln(1)


def _draw_kpi_card(
    pdf: _ReportPDF,
    x: float,
    y: float,
    w: float,
    h: float,
    label: str,
    value: str,
    sub: str,
    accent: tuple[int, int, int] = (24, 171, 248),
) -> None:
    pdf.set_fill_color(248, 250, 252)
    pdf.set_draw_color(226, 232, 240)
    pdf.rect(x, y, w, h, style="DF")
    pdf.set_fill_color(*accent)
    pdf.rect(x, y, w, 1.4, style="F")

    pdf.set_xy(x + 3, y + 3)
    pdf.set_font("Helvetica", "B", 7.5)
    pdf.set_text_color(100, 116, 139)
    pdf.cell(w - 6, 4, _safe(label.upper()))

    pdf.set_xy(x + 3, y + 8)
    pdf.set_font("Helvetica", "B", 13)
    pdf.set_text_color(15, 23, 42)
    pdf.cell(w - 6, 7, _safe(value))

    pdf.set_xy(x + 3, y + 16)
    pdf.set_font("Helvetica", "", 7.5)
    pdf.set_text_color(71, 85, 105)
    pdf.cell(w - 6, 4, _safe(sub))


def _draw_pmc_sparkline(
    pdf: _ReportPDF,
    x: float,
    y: float,
    w: float,
    h: float,
    series: list[dict[str, Any]],
) -> None:
    """Dessine un graphique vectoriel CTL / ATL / TSB sur la période."""
    pdf.set_fill_color(248, 250, 252)
    pdf.set_draw_color(226, 232, 240)
    pdf.rect(x, y, w, h, style="DF")

    if len(series) < 2:
        pdf.set_xy(x, y + h / 2 - 3)
        pdf.set_font("Helvetica", "I", 8.5)
        pdf.set_text_color(100, 116, 139)
        pdf.cell(w, 6, _safe("Donnees PMC insuffisantes sur la periode pour tracer la courbe."), align="C")
        return

    vals: list[float] = []
    for p in series:
        vals.extend([float(p.get("ctl") or 0), float(p.get("atl") or 0), float(p.get("tsb") or 0)])
    v_min = min(min(vals), -10.0)
    v_max = max(max(vals), 10.0)
    span = max(v_max - v_min, 1.0)

    pad_l, pad_r, pad_t, pad_b = 10.0, 4.0, 6.0, 7.0
    gx, gy = x + pad_l, y + pad_t
    gw, gh = w - pad_l - pad_r, h - pad_t - pad_b

    def _px(i: int) -> float:
        return gx + (i / (len(series) - 1)) * gw

    def _py(val: float) -> float:
        return gy + gh - ((val - v_min) / span) * gh

    # Ligne zéro
    if v_min < 0 < v_max:
        y0 = _py(0.0)
        pdf.set_draw_color(203, 213, 225)
        pdf.set_line_width(0.2)
        pdf.line(gx, y0, gx + gw, y0)
        pdf.set_font("Helvetica", "", 6.5)
        pdf.set_text_color(148, 163, 184)
        pdf.set_xy(x + 1, y0 - 2)
        pdf.cell(8, 4, "0", align="R")

    # Axes min/max
    pdf.set_font("Helvetica", "", 6.5)
    pdf.set_text_color(148, 163, 184)
    pdf.set_xy(x + 1, gy - 1.5)
    pdf.cell(8, 4, f"{v_max:.0f}", align="R")
    pdf.set_xy(x + 1, gy + gh - 2.5)
    pdf.cell(8, 4, f"{v_min:.0f}", align="R")

    curves = [
        ("ctl", (24, 171, 248), 0.65, "CTL (Forme)"),
        ("atl", (255, 146, 43), 0.5, "ATL (Fatigue)"),
        ("tsb", (20, 184, 166), 0.45, "TSB (Fraicheur)"),
    ]
    for key, color, lw, _ in curves:
        pdf.set_draw_color(*color)
        pdf.set_line_width(lw)
        for i in range(1, len(series)):
            x1, y1 = _px(i - 1), _py(float(series[i - 1].get(key) or 0))
            x2, y2 = _px(i), _py(float(series[i].get(key) or 0))
            pdf.line(x1, y1, x2, y2)

    pdf.set_line_width(0.2)
    # Légende & dates
    lx = gx
    ly = y + h - 5.2
    pdf.set_font("Helvetica", "B", 6.5)
    for _, color, _, label in curves:
        pdf.set_fill_color(*color)
        pdf.rect(lx, ly + 1.2, 3, 1.5, style="F")
        pdf.set_text_color(71, 85, 105)
        pdf.set_xy(lx + 3.8, ly)
        pdf.cell(26, 4, _safe(label))
        lx += 31

    pdf.set_font("Helvetica", "", 6.5)
    pdf.set_text_color(100, 116, 139)
    pdf.set_xy(gx + gw - 45, ly)
    pdf.cell(45, 4, _safe(f"{series[0].get('date', '')} -> {series[-1].get('date', '')}"), align="R")


def render_athlete_report_pdf(
    report: dict[str, Any],
    *,
    org_name: str,
    pmc_series: list[dict[str, Any]] | None = None,
    by_sport: list[dict[str, Any]] | None = None,
) -> bytes:
    """Rend le bilan athlète au format PDF A4 prêt au téléchargement."""
    now_str = datetime.now(UTC).strftime("%d/%m/%Y %H:%M UTC")
    pdf = _ReportPDF(org_name=org_name, generated_at=now_str)
    pdf.alias_nb_pages()
    pdf.set_margins(12, 12, 12)
    pdf.add_page()

    # Bandeau d'en-tête sombre pro
    pdf.set_fill_color(15, 23, 42)
    pdf.rect(0, 0, 210, 34, style="F")
    pdf.set_fill_color(24, 171, 248)
    pdf.rect(0, 34, 210, 1.6, style="F")

    pdf.set_xy(12, 7)
    pdf.set_font("Helvetica", "B", 9)
    pdf.set_text_color(24, 171, 248)
    pdf.cell(120, 5, _safe(f"VIDEOLOG  |  {org_name.upper()}"))

    period = report.get("period") or {}
    pdf.set_xy(132, 7)
    pdf.set_font("Helvetica", "", 8.5)
    pdf.set_text_color(203, 213, 225)
    pdf.cell(
        66,
        5,
        _safe(f"Periode : {period.get('from', '')} -> {period.get('to', '')}"),
        align="R",
    )

    pdf.set_xy(12, 13.5)
    pdf.set_font("Helvetica", "B", 18)
    pdf.set_text_color(255, 255, 255)
    pdf.cell(140, 9, _safe(f"Bilan de suivi - {report.get('athlete_name', 'Athlete')}"))

    sport_code = report.get("sport")
    sport_label = get_sport_meta(sport_code)["label"] if sport_code else "Multi-sports"
    pdf.set_xy(12, 23.5)
    pdf.set_font("Helvetica", "", 9.5)
    pdf.set_text_color(148, 163, 184)
    pdf.cell(
        186,
        5,
        _safe(f"Discipline principale : {sport_label}  |  Fenetre d'analyse : {period.get('days', 30)} jours"),
    )

    # Grille de 4 cartes KPI
    pdf.set_y(41)
    y_cards = pdf.get_y()
    card_w = 44.5
    gap = 2.66
    acts = report.get("activities") or {}
    comp = report.get("compliance") or {}
    pmc_start = (report.get("pmc") or {}).get("start") or {}
    pmc_end = (report.get("pmc") or {}).get("end") or {}
    well = report.get("wellness") or {}

    _draw_kpi_card(
        pdf,
        12,
        y_cards,
        card_w,
        23,
        "Volume & Seances",
        f"{acts.get('count', 0)} seance(s)",
        f"{_fmt_hours(float(acts.get('total_duration_s') or 0))}  |  {acts.get('total_distance_km', 0)} km",
        accent=(24, 171, 248),
    )
    _draw_kpi_card(
        pdf,
        12 + (card_w + gap),
        y_cards,
        card_w,
        23,
        "Charge totale (TSS)",
        f"{float(acts.get('total_tss') or 0):.0f} pts",
        f"Moy. {(float(acts.get('total_tss') or 0) / max(1, int(period.get('days') or 30)) * 7):.0f} TSS / sem.",
        accent=(255, 146, 43),
    )
    _draw_kpi_card(
        pdf,
        12 + 2 * (card_w + gap),
        y_cards,
        card_w,
        23,
        "Conformite Plan",
        f"{float(comp.get('pct') or 0):.1f}%",
        f"{comp.get('planned_done', 0)} / {comp.get('planned_total', 0)} planifiees",
        accent=(20, 184, 166),
    )
    ctl_end_str = f"{float(pmc_end['ctl']):.1f}" if "ctl" in pmc_end else "-"
    ctl_delta = _fmt_delta(pmc_start.get("ctl"), pmc_end.get("ctl"))
    _draw_kpi_card(
        pdf,
        12 + 3 * (card_w + gap),
        y_cards,
        card_w,
        23,
        "Forme (CTL)",
        ctl_end_str,
        f"Evolution : {ctl_delta} CTL",
        accent=(99, 102, 241),
    )

    pdf.set_y(y_cards + 26)

    # Section PMC & Progression
    _draw_section_title(pdf, "Progression & Charge d'entrainement (PMC)")
    y_pmc = pdf.get_y()
    _draw_pmc_sparkline(pdf, 12, y_pmc, 186, 40, pmc_series or [])
    pdf.set_y(y_pmc + 43)

    # Tableau synthétique début -> fin PMC
    pdf.set_font("Helvetica", "B", 8.5)
    pdf.set_fill_color(241, 245, 249)
    pdf.set_draw_color(226, 232, 240)
    pdf.set_text_color(51, 65, 85)
    col_w = [66, 40, 40, 40]
    headers = ["Indicateur PMC", "Debut de periode", "Fin de periode", "Evolution"]
    for w, h in zip(col_w, headers):
        pdf.cell(w, 7, _safe(h), border=1, fill=True, align="C" if w == 40 else "L")
    pdf.ln()

    pdf.set_font("Helvetica", "", 8.5)
    pdf.set_text_color(15, 23, 42)
    pmc_rows = [
        ("Forme chronique (CTL)", pmc_start.get("ctl"), pmc_end.get("ctl")),
        ("Fatigue aigue (ATL)", pmc_start.get("atl"), pmc_end.get("atl")),
        ("Fraicheur / Equilibre (TSB)", pmc_start.get("tsb"), pmc_end.get("tsb")),
    ]
    for label, s_val, e_val in pmc_rows:
        pdf.cell(col_w[0], 6.5, _safe(f" {label}"), border=1)
        pdf.cell(col_w[1], 6.5, f"{s_val:.1f}" if s_val is not None else "-", border=1, align="C")
        pdf.cell(col_w[2], 6.5, f"{e_val:.1f}" if e_val is not None else "-", border=1, align="C")
        pdf.cell(col_w[3], 6.5, _fmt_delta(s_val, e_val), border=1, align="C")
        pdf.ln()

    # Section Records de puissance
    _draw_section_title(pdf, "Records de puissance sur la periode (MMP)")
    recs = report.get("records_power_w") or {}
    rec_items = [
        ("Sprint 5s", recs.get("5s")),
        ("Anaerobie 1 min", recs.get("1m")),
        ("VO2max 5 min", recs.get("5m")),
        ("Seuil 20 min", recs.get("20m")),
    ]
    y_rec = pdf.get_y()
    for idx, (lbl, val) in enumerate(rec_items):
        rx = 12 + idx * (card_w + gap)
        pdf.set_fill_color(248, 250, 252)
        pdf.set_draw_color(226, 232, 240)
        pdf.rect(rx, y_rec, card_w, 16, style="DF")
        pdf.set_xy(rx + 2, y_rec + 2)
        pdf.set_font("Helvetica", "", 7.5)
        pdf.set_text_color(100, 116, 139)
        pdf.cell(card_w - 4, 4, _safe(lbl), align="C")
        pdf.set_xy(rx + 2, y_rec + 7)
        pdf.set_font("Helvetica", "B", 12)
        pdf.set_text_color(15, 23, 42)
        pdf.cell(card_w - 4, 7, f"{val} W" if val is not None else "-", align="C")
    pdf.set_y(y_rec + 19)

    # Section Répartition par discipline (si activités présentes)
    if by_sport:
        _draw_section_title(pdf, "Repartition par discipline")
        sp_w = [76, 24, 30, 28, 28]
        sp_headers = ["Discipline", "Seances", "Duree", "Distance", "TSS"]
        pdf.set_font("Helvetica", "B", 8.5)
        pdf.set_fill_color(241, 245, 249)
        pdf.set_text_color(51, 65, 85)
        for w, h in zip(sp_w, sp_headers):
            pdf.cell(w, 7, _safe(h), border=1, fill=True, align="L" if w == 76 else "C")
        pdf.ln()

        pdf.set_font("Helvetica", "", 8.5)
        pdf.set_text_color(15, 23, 42)
        for row in by_sport:
            label = get_sport_meta(row.get("sport"))["label"]
            pdf.cell(sp_w[0], 6.5, _safe(f" {label}"), border=1)
            pdf.cell(sp_w[1], 6.5, str(row.get("count", 0)), border=1, align="C")
            pdf.cell(sp_w[2], 6.5, _fmt_hours(float(row.get("duration_s") or 0)), border=1, align="C")
            pdf.cell(sp_w[3], 6.5, f"{float(row.get('distance_km') or 0):.1f} km", border=1, align="C")
            pdf.cell(sp_w[4], 6.5, f"{float(row.get('tss') or 0):.0f}", border=1, align="C")
            pdf.ln()

    # Section Forme & Recuperation (Wellness)
    _draw_section_title(pdf, "Recuperation & Etat de forme")
    entries_n = int(well.get("entries_count") or 0)
    pdf.set_font("Helvetica", "", 9)
    pdf.set_text_color(51, 65, 85)
    if entries_n == 0:
        pdf.cell(0, 6, _safe("Aucune saisie de forme (sommeil / fatigue) enregistree sur la periode."), new_x="LMARGIN", new_y="NEXT")
    else:
        avg_sleep = f"{well['avg_sleep_h']} h/nuit" if well.get("avg_sleep_h") is not None else "-"
        avg_fat = f"{well['avg_fatigue']} / 5" if well.get("avg_fatigue") is not None else "-"
        pdf.cell(
            0,
            6,
            _safe(f"Saisies : {entries_n} jour(s)   |   Sommeil moyen : {avg_sleep}   |   Fatigue moyenne : {avg_fat}"),
            new_x="LMARGIN",
            new_y="NEXT",
        )

    out = pdf.output()
    return bytes(out)
