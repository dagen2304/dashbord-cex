import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from typing import Dict, Any, List
import logging

logger = logging.getLogger(__name__)

class CapacityAnalyticsService:
    """
    Service d'analyse de tendance et de prédiction capacitaire (Espace disque, CPU, RAM).
    Utilise Pandas pour le calcul statistique et la régression linéaire prédictive.
    """

    @staticmethod
    def calculate_trends(history_points: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Calcule les variations W2W, MoM et projette la saturation (Time-to-Full).
        Chaque point de history_points doit être de la forme :
        {"timestamp": datetime, "used_tb": float, "total_tb": float}
        """
        if not history_points:
            logger.warning("Pas assez de points d'historique pour l'analyse capacitaire")
            return CapacityAnalyticsService._get_empty_analysis()

        # 1. Charger dans un DataFrame Pandas
        df = pd.DataFrame(history_points)
        df['timestamp'] = pd.to_datetime(df['timestamp'])
        df['used_tb'] = df['used_tb'].astype(float)
        df['total_tb'] = df['total_tb'].astype(float)
        
        # Calculer le pourcentage d'utilisation
        df['usage_pct'] = (df['used_tb'] / df['total_tb']) * 100
        
        # Trier par timestamp
        df = df.sort_values(by='timestamp').reset_index(drop=True)
        
        latest_point = df.iloc[-1]
        capacity_total = latest_point['total_tb']
        capacity_used = latest_point['used_tb']
        capacity_free = capacity_total - capacity_used
        usage_pct = latest_point['usage_pct']

        if len(history_points) < 5:
            # S'il y a au moins un point, on renvoie la capacité actuelle, avec 0 pour les prédictions
            empty_analysis = CapacityAnalyticsService._get_empty_analysis()
            empty_analysis.update({
                "capacity_total_tb": round(capacity_total, 1),
                "capacity_used_tb": round(capacity_used, 1),
                "capacity_free_tb": round(capacity_free, 1),
                "usage_pct": round(usage_pct, 1),
            })
            return empty_analysis

        # 2. Séparer les plages temporelles pour W2W et MoM
        now = df['timestamp'].max()
        
        # Plages W (derniers 7 jours) et W-1 (jours 8 à 14)
        week_curr = df[df['timestamp'] >= (now - timedelta(days=7))]
        week_prev = df[(df['timestamp'] >= (now - timedelta(days=14))) & (df['timestamp'] < (now - timedelta(days=7)))]
        
        # Plages M (derniers 30 jours) et M-1 (jours 31 à 60)
        month_curr = df[df['timestamp'] >= (now - timedelta(days=30))]
        month_prev = df[(df['timestamp'] >= (now - timedelta(days=60))) & (df['timestamp'] < (now - timedelta(days=30)))]

        # 3. Calculs W2W (Week-over-Week)
        w2w_diff_tb = 0.0
        w2w_growth_pct = 0.0
        if not week_curr.empty and not week_prev.empty:
            avg_w_curr = week_curr['used_tb'].mean()
            avg_w_prev = week_prev['used_tb'].mean()
            w2w_diff_tb = avg_w_curr - avg_w_prev
            if avg_w_prev > 0:
                w2w_growth_pct = (w2w_diff_tb / avg_w_prev) * 100

        # 4. Calculs MoM (Month-over-Month)
        mom_diff_tb = 0.0
        mom_growth_pct = 0.0
        if not month_curr.empty and not month_prev.empty:
            avg_m_curr = month_curr['used_tb'].mean()
            avg_m_prev = month_prev['used_tb'].mean()
            mom_diff_tb = avg_m_curr - avg_m_prev
            if avg_m_prev > 0:
                mom_growth_pct = (mom_diff_tb / avg_m_prev) * 100

        # 5. Algorithme de Projection Linéaire (Time-to-Full)
        # On projette en utilisant les données des 30 derniers jours
        days_to_full = 9999  # Code pour dire "infini" ou "pas de saturation prévisible"
        growth_per_day = 0.0
        
        if len(month_curr) >= 3:
            # Convertir les dates en secondes écoulées pour la régression linéaire
            x = (month_curr['timestamp'] - month_curr['timestamp'].min()).dt.total_seconds().values
            y = month_curr['used_tb'].values
            
            try:
                # Ajustement polynomial du premier degré (y = ax + b)
                # a est la pente en TB par seconde
                slope_s, intercept = np.polyfit(x, y, 1)
                
                # Pente en TB par jour (1 jour = 86400 secondes)
                growth_per_day = slope_s * 86400
                
                # S'assurer que le résultat n'est pas NaN
                import math
                if math.isnan(growth_per_day):
                    growth_per_day = 0.0
                
                if growth_per_day > 0.001:
                    # Jours restants avant saturation
                    days_to_full = int(capacity_free / growth_per_day)
                    if days_to_full < 0:
                        days_to_full = 9999
                else:
                    days_to_full = 9999  # Tendance stable ou descendante
            except Exception as poly_err:
                logger.warning(f"Impossible de calculer la tendance linéaire: {poly_err}")
                growth_per_day = 0.0
                days_to_full = 9999
                
        # Classer le niveau de criticité capacitaire
        severity = "HEALTHY"
        if days_to_full < 90:
            severity = "CRITICAL"
        elif days_to_full < 180:
            severity = "WARNING"

        return {
            "capacity_total_tb": round(capacity_total, 1),
            "capacity_used_tb": round(capacity_used, 1),
            "capacity_free_tb": round(capacity_free, 1),
            "usage_pct": round(usage_pct, 1),
            "w2w_diff_tb": round(w2w_diff_tb, 2),
            "w2w_growth_pct": round(w2w_growth_pct, 2),
            "mom_diff_tb": round(mom_diff_tb, 2),
            "mom_growth_pct": round(mom_growth_pct, 2),
            "growth_per_day_tb": round(growth_per_day, 3),
            "days_to_full": days_to_full,
            "severity": severity
        }

    @staticmethod
    def _get_empty_analysis() -> Dict[str, Any]:
        return {
            "capacity_total_tb": 0.0,
            "capacity_used_tb": 0.0,
            "capacity_free_tb": 0.0,
            "usage_pct": 0.0,
            "w2w_diff_tb": 0.0,
            "w2w_growth_pct": 0.0,
            "mom_diff_tb": 0.0,
            "mom_growth_pct": 0.0,
            "growth_per_day_tb": 0.0,
            "days_to_full": 9999,
            "severity": "HEALTHY"
        }
