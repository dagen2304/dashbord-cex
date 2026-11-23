"""Analyse capacitaire et exports CSV / Excel."""
import csv
import io
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import openpyxl
from fastapi import APIRouter, Depends, Query, Response
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import audit, require_operator
from app.models.inventory import InfrastructureDevice, MetricHistory
from app.models.user import User
from app.services.analytics_service import CapacityAnalyticsService

router = APIRouter()

# Profondeur d'historique utile : M (30 j) et M-1 (30 j précédents).
HISTORY_DAYS = 60


def build_capacity_report(db: Session, device_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """Une seule requête sur la fenêtre utile, regroupée en mémoire par (équipement, ressource)."""
    since = datetime.utcnow() - timedelta(days=HISTORY_DAYS)
    q = (
        db.query(
            MetricHistory.device_id, MetricHistory.resource_name, MetricHistory.unit,
            MetricHistory.timestamp, MetricHistory.capacity_used_tb, MetricHistory.capacity_total_tb,
        )
        .filter(MetricHistory.timestamp >= since, MetricHistory.device_id.isnot(None))
        .order_by(MetricHistory.timestamp.asc())
    )
    if device_id:
        q = q.filter(MetricHistory.device_id == device_id)

    series: Dict[tuple, List[dict]] = defaultdict(list)
    units: Dict[tuple, str] = {}
    for dev_id, res, unit, ts, used, total in q.yield_per(5000):
        key = (dev_id, res)
        series[key].append({"timestamp": ts, "used_tb": used, "total_tb": total})
        units[key] = unit

    devices = {d.id: d for d in db.query(InfrastructureDevice).filter(
        InfrastructureDevice.id.in_({k[0] for k in series})).all()} if series else {}

    report = []
    for (dev_id, res), points in series.items():
        dev = devices.get(dev_id)
        if not dev:
            continue
        analysis = CapacityAnalyticsService.calculate_trends(points, unit=units[(dev_id, res)])
        analysis.update(
            device_id=dev_id,
            device_name=dev.display_name,
            device_type=dev.device_type,
            category=dev.category,
            is_archived=dev.is_archived,
            resource_name=res,
            unit=units[(dev_id, res)],
        )
        report.append(analysis)
    sev = {"CRITICAL": 0, "WARNING": 1, "HEALTHY": 2}
    report.sort(key=lambda r: (sev.get(r["severity"], 3), r["device_name"], r["resource_name"]))
    return report


@router.get("/capacity/data")
def get_capacity_analytics_data(device_id: Optional[str] = Query(None), db: Session = Depends(get_db)):
    return build_capacity_report(db, device_id)


@router.get("/capacity/{device_id}/{resource_name}/history")
def get_capacity_history(device_id: str, resource_name: str, days: int = Query(30, ge=1, le=365),
                         db: Session = Depends(get_db)):
    since = datetime.utcnow() - timedelta(days=days)
    rows = (
        db.query(MetricHistory.timestamp, MetricHistory.capacity_used_tb, MetricHistory.capacity_total_tb, MetricHistory.usage_pct)
        .filter(MetricHistory.device_id == device_id, MetricHistory.resource_name == resource_name,
                MetricHistory.timestamp >= since)
        .order_by(MetricHistory.timestamp.asc())
        .all()
    )
    return [{"timestamp": ts.isoformat() + "Z", "used": used, "total": total, "usage_pct": pct} for ts, used, total, pct in rows]


# ── Exports ─────────────────────────────────────────────────────────────────
_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def safe_cell(value: Any) -> Any:
    """Neutralise l'injection de formule (CSV/Excel) : un texte commençant par = + - @
    est préfixé d'une apostrophe. Les nombres sont laissés tels quels."""
    if isinstance(value, str) and value.startswith(_FORMULA_PREFIXES):
        return "'" + value
    return value


_HEADERS = [
    "Équipement", "Type", "Ressource", "Unité", "Capacité", "Utilisé", "Libre", "Usage (%)",
    "Var. semaine", "Croiss. semaine (%)", "Var. mois", "Croiss. mois (%)", "Croiss. / jour",
    "Saturation estimée (jours)", "Criticité",
]


def _row(r: Dict[str, Any]) -> List[Any]:
    return [
        r["device_name"], r["device_type"], r["resource_name"], r["unit"],
        r["capacity_total_tb"], r["capacity_used_tb"], r["capacity_free_tb"], r["usage_pct"],
        r["w2w_diff_tb"], r["w2w_growth_pct"], r["mom_diff_tb"], r["mom_growth_pct"], r["growth_per_day_tb"],
        r["days_to_full"] if r["days_to_full"] is not None else "Non projetée", r["severity"],
    ]


def _filename(ext: str) -> str:
    return f"Rapport_Capacitaire_NOC_{datetime.now().strftime('%Y%m%d_%H%M')}.{ext}"


@router.get("/capacity/csv")
def export_capacity_csv(device_id: Optional[str] = Query(None), user: User = Depends(require_operator),
                        db: Session = Depends(get_db)):
    data = build_capacity_report(db, device_id)
    out = io.StringIO()
    writer = csv.writer(out, delimiter=";")
    writer.writerow(_HEADERS)
    for r in data:
        writer.writerow([safe_cell(v) if v is not None else "" for v in _row(r)])
    audit(db, user, "report.export_csv", device_id or "all")
    db.commit()
    return Response(
        content=out.getvalue().encode("utf-8-sig"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{_filename("csv")}"'},
    )


@router.get("/capacity/excel")
def export_capacity_excel(device_id: Optional[str] = Query(None), user: User = Depends(require_operator),
                          db: Session = Depends(get_db)):
    data = build_capacity_report(db, device_id)
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Capacité"

    ncols = len(_HEADERS)
    last_col = get_column_letter(ncols)
    ws.merge_cells(f"A1:{last_col}1")
    ws["A1"] = "NOC INFRA — Rapport d'analyse capacitaire"
    ws["A1"].font = Font(name="Calibri", size=14, bold=True, color="FFFFFF")
    ws["A1"].fill = PatternFill("solid", start_color="111827")
    ws["A1"].alignment = Alignment(horizontal="center", vertical="center")
    ws.row_dimensions[1].height = 32
    ws.merge_cells(f"A2:{last_col}2")
    ws["A2"] = (f"Généré le {datetime.now().strftime('%d/%m/%Y %H:%M')} — "
                "Critique : saturation < 90 j ou usage ≥ 90 % ; Attention : < 180 j ou ≥ 80 %")
    ws["A2"].font = Font(size=9, italic=True, color="6B7280")
    ws["A2"].alignment = Alignment(horizontal="center")

    border = Border(*(Side(style="thin", color="E5E7EB"),) * 4)
    for c, h in enumerate(_HEADERS, 1):
        cell = ws.cell(row=4, column=c, value=h)
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", start_color="1F2937")
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.border = border
    ws.freeze_panes = "A5"

    sev_style = {
        "CRITICAL": (PatternFill("solid", start_color="FEE2E2"), Font(bold=True, color="991B1B")),
        "WARNING": (PatternFill("solid", start_color="FEF3C7"), Font(bold=True, color="92400E")),
        "HEALTHY": (PatternFill("solid", start_color="D1FAE5"), Font(bold=True, color="065F46")),
    }
    for i, r in enumerate(data, 5):
        for c, v in enumerate(_row(r), 1):
            cell = ws.cell(row=i, column=c, value=safe_cell(v))
            cell.border = border
            if isinstance(v, float):
                cell.number_format = "0.00"
        fill, font = sev_style.get(r["severity"], sev_style["HEALTHY"])
        sev_cell = ws.cell(row=i, column=ncols)
        sev_cell.fill, sev_cell.font = fill, font

    for c in range(1, ncols + 1):
        width = max((len(str(ws.cell(row=r, column=c).value or "")) for r in range(4, ws.max_row + 1)), default=10)
        ws.column_dimensions[get_column_letter(c)].width = max(12, min(width + 3, 36))

    buf = io.BytesIO()
    wb.save(buf)
    audit(db, user, "report.export_excel", device_id or "all")
    db.commit()
    return Response(
        content=buf.getvalue(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{_filename("xlsx")}"'},
    )
