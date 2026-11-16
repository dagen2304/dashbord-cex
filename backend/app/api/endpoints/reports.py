import io
import csv
import random
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional
from fastapi import APIRouter, Response, Query, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.models.inventory import InfrastructureDevice, MetricHistory
from app.services.analytics_service import CapacityAnalyticsService
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

router = APIRouter()

def _generate_all_reports_data(db: Session, device_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    Génère les données analytiques complètes compilées pour les équipements actifs et archivés.
    Utilise la table PostgreSQL metrics_history. Les données sont groupées par équipement et ressource.
    """
    devices = db.query(InfrastructureDevice).all()
    
    analyzed_devices = []
    if not devices:
        return analyzed_devices

    for dev in devices:
        if device_id and dev.id != device_id:
            continue
            
        # Obtenir toutes les ressources distinctes pour cet équipement
        resources = db.query(MetricHistory.resource_name, MetricHistory.unit).filter(MetricHistory.device_id == dev.id).distinct().all()
        
        for res_name, res_unit in resources:
            history_records = (
                db.query(MetricHistory)
                .filter(MetricHistory.device_id == dev.id, MetricHistory.resource_name == res_name)
                .order_by(MetricHistory.timestamp.asc())
                .all()
            )
            
            history = []
            for r in history_records:
                history.append({
                    "timestamp": r.timestamp,
                    "used_tb": r.capacity_used_tb,
                    "total_tb": r.capacity_total_tb
                })
                
            analysis = CapacityAnalyticsService.calculate_trends(history)
            analysis["device_id"] = dev.id
            analysis["device_name"] = dev.display_name
            analysis["device_type"] = dev.category.lower() if dev.category else "unknown"
            analysis["resource_name"] = res_name
            analysis["unit"] = res_unit
            analyzed_devices.append(analysis)
            
    return analyzed_devices

@router.get("/capacity/data")
async def get_capacity_analytics_data(
    device_id: Optional[str] = Query(None, description="Filtrer par UUID d'équipement (depuis le DeviceSelector)"),
    db: Session = Depends(get_db)
):
    """
    Retourne les données d'analyse capacitaire.
    """
    return _generate_all_reports_data(db, device_id)

@router.get("/capacity/csv")
async def export_capacity_csv(
    device_id: Optional[str] = Query(None),
    db: Session = Depends(get_db)
):
    """
    Exporte le rapport de tendance capacitaire au format CSV standardisé.
    """
    data = _generate_all_reports_data(db, device_id)
    
    output = io.StringIO()
    writer = csv.writer(output, delimiter=';')
    
    writer.writerow([
        "Équipement", "Type", "Ressource", "Unité", "Capacité Totale", "Utilisé", "Libre", 
        "Taux d'utilisation (%)", "Variation Hebdomadaire", "Croissance W2W (%)",
        "Variation Mensuelle", "Croissance MoM (%)", "Croissance Quotidienne", 
        "Est. Saturation (Jours)", "Niveau Criticité"
    ])
    
    for row in data:
        writer.writerow([
            row["device_name"], row["device_type"], row["resource_name"], row["unit"],
            row["capacity_total_tb"], row["capacity_used_tb"], row["capacity_free_tb"],
            row["usage_pct"], row["w2w_diff_tb"], row["w2w_growth_pct"],
            row["mom_diff_tb"], row["mom_growth_pct"], row["growth_per_day_tb"],
            "Saturation" if row["days_to_full"] == 9999 else row["days_to_full"], row["severity"]
        ])
        
    output.seek(0)
    csv_bytes = output.getvalue().encode('utf-8-sig')
    
    return Response(
        content=csv_bytes,
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=Rapport_NOC_Capacitaire_{datetime.now().strftime('%Y%m%d')}.csv"}
    )

@router.get("/capacity/excel")
async def export_capacity_excel(
    device_id: Optional[str] = Query(None),
    db: Session = Depends(get_db)
):
    """
    Génère et exporte un fichier Excel stylisé Premium.
    """
    data = _generate_all_reports_data(db, device_id)
    
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Tendances NOC"
    ws.views.sheetView[0].showGridLines = True
    
    ws.merge_cells("A1:O1")
    title_cell = ws["A1"]
    title_cell.value = "PLATEFORME NOC - RAPPORT ANALYTIQUE CAPACITAIRE"
    title_cell.font = Font(name="Segoe UI", size=14, bold=True, color="FFFFFF")
    title_cell.fill = PatternFill(start_color="111827", end_color="111827", fill_type="solid")
    title_cell.alignment = Alignment(horizontal="center", vertical="center")
    ws.row_dimensions[1].height = 40
    
    ws.merge_cells("A2:O2")
    subtitle_cell = ws["A2"]
    subtitle_cell.value = f"Généré le {datetime.now().strftime('%d/%m/%Y à %H:%M:%S')} | Seuil d'alerte : Jaune < 180j, Rouge < 90j"
    subtitle_cell.font = Font(name="Segoe UI", size=9, italic=True, color="6B7280")
    subtitle_cell.alignment = Alignment(horizontal="center", vertical="center")
    ws.row_dimensions[2].height = 20
    
    headers = [
        "Nom de l'Équipement", "Type", "Ressource", "Unité", "Capacité", "Utilisé", "Libre",
        "Usage (%)", "Var. W2W", "Croiss. W2W (%)", "Var. MoM", "Croiss. MoM (%)",
        "Croiss. Jour", "Time-to-Full (Jours)", "Statut Saturation"
    ]
    
    header_fill = PatternFill(start_color="1F2937", end_color="1F2937", fill_type="solid")
    header_font = Font(name="Segoe UI", size=10, bold=True, color="FFFFFF")
    thin_border = Border(
        left=Side(style='thin', color='E5E7EB'), right=Side(style='thin', color='E5E7EB'),
        top=Side(style='thin', color='E5E7EB'), bottom=Side(style='thin', color='E5E7EB')
    )
    
    ws.row_dimensions[4].height = 25
    for col_idx, header in enumerate(headers, 1):
        cell = ws.cell(row=4, column=col_idx)
        cell.value = header
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.border = thin_border
        
    fill_healthy = PatternFill(start_color="D1FAE5", end_color="D1FAE5", fill_type="solid")
    font_healthy = Font(name="Segoe UI", size=10, bold=True, color="065F46")
    fill_warning = PatternFill(start_color="FEF3C7", end_color="FEF3C7", fill_type="solid")
    font_warning = Font(name="Segoe UI", size=10, bold=True, color="92400E")
    fill_critical = PatternFill(start_color="FEE2E2", end_color="FEE2E2", fill_type="solid")
    font_critical = Font(name="Segoe UI", size=10, bold=True, color="991B1B")
    
    start_row = 5
    for row_idx, dev in enumerate(data, start_row):
        ws.row_dimensions[row_idx].height = 20
        
        ws.cell(row=row_idx, column=1, value=dev["device_name"]).alignment = Alignment(horizontal="left", vertical="center")
        ws.cell(row=row_idx, column=2, value=dev["device_type"]).alignment = Alignment(horizontal="left", vertical="center")
        ws.cell(row=row_idx, column=3, value=dev["resource_name"]).alignment = Alignment(horizontal="left", vertical="center")
        ws.cell(row=row_idx, column=4, value=dev["unit"]).alignment = Alignment(horizontal="center", vertical="center")
        ws.cell(row=row_idx, column=5, value=dev["capacity_total_tb"]).number_format = '#,##0.0'
        ws.cell(row=row_idx, column=6, value=dev["capacity_used_tb"]).number_format = '#,##0.0'
        ws.cell(row=row_idx, column=7, value=dev["capacity_free_tb"]).number_format = '#,##0.0'
        ws.cell(row=row_idx, column=8, value=dev["usage_pct"] / 100).number_format = '0.0%'
        ws.cell(row=row_idx, column=9, value=dev["w2w_diff_tb"]).number_format = '+#,##0.0;-#,##0.0;0.0'
        ws.cell(row=row_idx, column=10, value=dev["w2w_growth_pct"] / 100).number_format = '+0.0%;-0.0%;0.0%'
        ws.cell(row=row_idx, column=11, value=dev["mom_diff_tb"]).number_format = '+#,##0.0;-#,##0.0;0.0'
        ws.cell(row=row_idx, column=12, value=dev["mom_growth_pct"] / 100).number_format = '+0.0%;-0.0%;0.0%'
        ws.cell(row=row_idx, column=13, value=dev["growth_per_day_tb"]).number_format = '0.000'
        
        ttf_cell = ws.cell(row=row_idx, column=14)
        if dev["days_to_full"] == 9999:
            ttf_cell.value = "Tendance stable"
        else:
            ttf_cell.value = dev["days_to_full"]
            ttf_cell.number_format = '#,##0'
        ttf_cell.alignment = Alignment(horizontal="right", vertical="center")
        
        status_cell = ws.cell(row=row_idx, column=15)
        status_cell.value = dev["severity"]
        status_cell.alignment = Alignment(horizontal="center", vertical="center")
        if dev["severity"] == "CRITICAL":
            status_cell.fill = fill_critical; status_cell.font = font_critical
        elif dev["severity"] == "WARNING":
            status_cell.fill = fill_warning; status_cell.font = font_warning
        else:
            status_cell.fill = fill_healthy; status_cell.font = font_healthy
            
        for col_idx in range(1, 16):
            c = ws.cell(row=row_idx, column=col_idx)
            c.border = thin_border
            if col_idx != 15:
                c.font = Font(name="Segoe UI", size=10)
                if col_idx > 4 and col_idx < 14:
                    c.alignment = Alignment(horizontal="right", vertical="center")
                    
    for col in ws.columns:
        max_len = 0
        col_letter = get_column_letter(col[0].column)
        for cell in col[2:]: 
            if cell.value: max_len = max(max_len, len(str(cell.value)))
        ws.column_dimensions[col_letter].width = max(12, min(max_len + 4, 30))
        
    excel_bytes = io.BytesIO()
    wb.save(excel_bytes)
    excel_bytes.seek(0)
    
    return Response(
        content=excel_bytes.getvalue(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename=Rapport_NOC_Capacitaire_{datetime.now().strftime('%Y%m%d')}.xlsx"}
    )
