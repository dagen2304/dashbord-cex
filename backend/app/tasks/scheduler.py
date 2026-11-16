import json
import logging
from datetime import datetime, timedelta
from app.tasks.celery_app import celery_app
from app.services.vcenter_collector import VCenterCollector
from app.services.dorado_collector import DoradoCollector
from app.services.unity_collector import UnityCollector
from app.services.powerstore_collector import PowerStoreCollector
from app.services.datadomain_collector import DataDomainCollector
from app.services.netbackup_collector import NetBackupCollector
from app.services.scality_collector import ScalityCollector
from app.services.veritas_collector import VeritasCollector
from app.core.influx import influx_manager
from app.core.database import SessionLocal
from app.models.inventory import InfrastructureDevice, ServiceDiscovery, MetricHistory, AnomalySaturation, LatencyAnomaly, SystemSetting
from app.models.application import VM
from app.core.smtp import send_latency_alert_email
import redis
import os
import random
import concurrent.futures

logger = logging.getLogger(__name__)

# Connecteur Redis pour stocker le dernier état en temps réel
REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")
r_client = redis.Redis.from_url(REDIS_URL)

# ─────────────────────────────────────────────────────────────────────────────
# COLLECTOR REGISTRY — Mapping device_type (SSOT) → Classe collecteur
# Tout nouvel équipement enregistré en DB avec l'un de ces types sera
# automatiquement inclus dans le prochain cycle de polling Celery.
# ─────────────────────────────────────────────────────────────────────────────
COLLECTOR_REGISTRY = {
    "VCENTER":         VCenterCollector,
    "HUAWEI_DORADO":   DoradoCollector,
    "DELL_UNITY":      UnityCollector,
    "DELL_POWERSTORE": PowerStoreCollector,
    "DATADOMAIN":      DataDomainCollector,
    "NETBACKUP":       NetBackupCollector,
    "SCALITY":         ScalityCollector,
    "VERITAS_ACCESS":  VeritasCollector,
}

# ─────────────────────────────────────────────────────────────────────────────
# Configuration de fallback (utilisée SEULEMENT si la DB est vide au 1er boot)
# Garantit que le dashboard n'est pas vide avant le 1er ajout via l'UI.
# ─────────────────────────────────────────────────────────────────────────────
FALLBACK_COLLECTORS_CONFIG = {
    "vcenter-01": {
        "class": VCenterCollector,
        "name": "vCenter Cluster PROD-01",
        "simulate": True,
        "host": "vcenter-prod.noc.infra"
    },
    "dorado-01": {
        "class": DoradoCollector,
        "name": "Huawei Dorado Pool-01",
        "simulate": True,
        "host": "dorado-pool01.noc.infra"
    },
    "unity-01": {
        "class": UnityCollector,
        "name": "Dell Unity Baie-01",
        "simulate": True,
        "host": "unity01.noc.infra"
    },
    "powerstore-01": {
        "class": PowerStoreCollector,
        "name": "Dell PowerStore-01",
        "simulate": True,
        "host": "powerstore01.noc.infra"
    },
    "datadomain-01": {
        "class": DataDomainCollector,
        "name": "DataDomain Backup-01",
        "simulate": True,
        "host": "datadomain01.noc.infra"
    },
    "netbackup-01": {
        "class": NetBackupCollector,
        "name": "NetBackup Master Server",
        "simulate": True,
        "host": "netbackup-master.noc.infra"
    },
    "scality-01": {
        "class": ScalityCollector,
        "name": "Scality RING Object",
        "simulate": True,
        "host": "scality-ring.noc.infra"
    },
    "veritas-01": {
        "class": VeritasCollector,
        "name": "Veritas Access Gateway",
        "simulate": True,
        "host": "veritas-access.noc.infra"
    }
}


def _load_active_devices_from_db():
    """
    Lit la liste des équipements actifs enregistrés dans le registre Service Discovery (SSOT).
    Retourne un dict {device_id: config_dict} compatible avec le format
    attendu par les collecteurs.

    Chaque entrée contient les credentials déchiffrés (host, port, username, password)
    pour que les collecteurs puissent s'authentifier directement sur les équipements réels.

    Fallback automatique sur FALLBACK_COLLECTORS_CONFIG si le Service Discovery est vide.
    """
    try:
        with SessionLocal() as db:
            active_discoveries = (
                db.query(ServiceDiscovery)
                .filter(ServiceDiscovery.is_active == True)
                .all()
            )

            devices = []
            for disc in active_discoveries:
                dev = disc.device
                if dev and not dev.is_archived:
                    devices.append(dev)

            if not devices:
                logger.warning("Aucun équipement actif trouvé dans Service Discovery.")
                return None  # Signal de fallback

            config = {}
            for device in devices:
                collector_class = COLLECTOR_REGISTRY.get(device.device_type)
                if not collector_class:
                    logger.warning(f"Type d'équipement non supporté : {device.device_type} (ID: {device.id}) — ignoré.")
                    continue

                # Déchiffrer le mot de passe stocké dans la DB (Fernet AES-256)
                try:
                    decrypted_password = device.get_password()
                except Exception as pwd_err:
                    logger.warning(f"Impossible de déchiffrer le mot de passe pour {device.display_name}: {pwd_err}")
                    decrypted_password = ""

                config[device.id] = {
                    "class": collector_class,
                    "name": device.display_name,
                    "host": device.hostname_ip,
                    "port": device.port,
                    "username": device.username,
                    "password": decrypted_password,   # Mot de passe déchiffré pour connexion réelle
                    "simulate": False,                 # Connexion réseau réelle — pas de simulation
                    "db_id": device.id,                # UUID DB pour mise à jour last_sync_at
                }

            logger.info(f"Pipeline de collecte initialisé avec {len(config)} équipement(s) actifs depuis le Service Discovery.")
            return config

    except Exception as e:
        logger.error(f"Erreur de lecture du Service Discovery : {str(e)} — fallback potentiel.")
        return None


def _update_device_sync_status(device_id: str, status: str):
    """
    Met à jour last_sync_status et last_sync_at dans PostgreSQL après chaque collecte.
    Permet aux badges de statut dans l'UI Inventaire de refléter l'état du pipeline.
    """
    try:
        with SessionLocal() as db:
            device = db.query(InfrastructureDevice).filter(
                InfrastructureDevice.id == device_id
            ).first()
            if device:
                device.last_sync_status = status
                device.last_sync_at = datetime.utcnow()
                db.commit()
    except Exception as e:
        logger.error(f"Impossible de mettre à jour le statut de sync pour {device_id}: {str(e)}")


@celery_app.on_after_configure.connect
def setup_periodic_tasks(sender, **kwargs):
    # Lire l'intervalle depuis la base de données (si elle est prête)
    interval = 60.0
    try:
        with SessionLocal() as db:
            setting = db.query(SystemSetting).filter(SystemSetting.key == "polling_interval").first()
            if setting:
                interval = float(setting.value)
    except Exception as e:
        logger.warning(f"Impossible de lire l'intervalle de polling en base (défaut 60s) : {e}")
    
    sender.add_periodic_task(interval, poll_infrastructure_metrics.s(), name='polling_infra_60s')


@celery_app.task
def poll_infrastructure_metrics():
    """
    Tâche asynchrone principale — Cœur du Data Pipeline NOC.

    Cycle de vie :
    1. Lit la liste des équipements actifs depuis la DB PostgreSQL (SSOT)
    2. Pour chaque équipement, instancie le collecteur via COLLECTOR_REGISTRY
    3. Exécute la collecte standardisée
    4. Pousse les métriques dans InfluxDB (historique capacitaire + anomalies)
    5. Enregistre le snapshot temps réel dans Redis (Dashboard Global)
    6. Met à jour last_sync_status dans la DB (badges UI Inventaire)

    Routing des données :
    - metrics instantanées → Redis noc_latest:{device_id} → OverviewGrid
    - séries temporelles → InfluxDB measurement "metrics" → CapacityAnalytics
    - pics > seuil CPU/RAM → InfluxDB measurement "anomalies" → VmDiagnostic
    """
    logger.info("▶ Démarrage du cycle de polling — lecture du registre SSOT...")

    # ── Étape 1 : Charger la liste des équipements depuis la DB ──────────────
    collectors_config = _load_active_devices_from_db()

    # Fallback si aucun équipement en DB (1er boot avant ajout via l'UI)
    if collectors_config is None:
        if os.environ.get("DEMO_MODE", "True").lower() == "true":
            logger.info("↩ Fallback sur la configuration de démonstration statique (DEMO_MODE actif).")
            collectors_config = FALLBACK_COLLECTORS_CONFIG
        else:
            logger.warning("Base vide et DEMO_MODE inactif. Aucun polling effectué.")
            return {}

    # ── Étape 2 : Boucler sur les équipements actifs en parallèle ──────────────
    results = {}
    
    # Préparation des tâches
    tasks = [(device_id, cfg) for device_id, cfg in collectors_config.items()]
    
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(len(tasks), 16) if tasks else 1) as executor:
        future_map = {}
        for device_id, cfg in tasks:
            collector_class = cfg["class"]
            collector = collector_class(device_id, cfg["name"], cfg)
            future = executor.submit(collector.collect)
            future_map[future] = (device_id, cfg)
            
        for future in concurrent.futures.as_completed(future_map):
            device_id, cfg = future_map[future]
            try:
                payload = future.result(timeout=300)

                # ── Étape 3 : Aiguillage / Routing des données ─────────────────

                # → InfluxDB : Métriques historiques (CapacityAnalytics + OverviewGrid)
                if "metrics" in payload:
                    influx_manager.write_metrics(
                        device_id=device_id,
                        device_type=payload.get("device_type", "unknown"),
                        metrics=payload["metrics"]
                    )

                # → InfluxDB : Télémétrie individuelle des VMs
                if "vms_metrics" in payload and payload["vms_metrics"]:
                    influx_manager.write_vm_telemetry(
                        device_id=device_id,
                        vms_metrics=payload["vms_metrics"]
                    )
                
                # → InfluxDB : File systems internes des VMs
                if "vms_fs_metrics" in payload and payload["vms_fs_metrics"]:
                    influx_manager.write_vm_fs_telemetry(
                        device_id=device_id,
                        vms_fs_metrics=payload["vms_fs_metrics"]
                    )
                    
                # → InfluxDB : Datastores (pour vROps metrics explorer)
                if "datastores_metrics" in payload and payload["datastores_metrics"]:
                    influx_manager.write_ds_telemetry(
                        device_id=device_id,
                        ds_metrics=payload["datastores_metrics"]
                    )

                # → PostgreSQL : Synchronisation de l'inventaire des VMs
                if "vms_metrics" in payload and payload["vms_metrics"]:
                    try:
                        with SessionLocal() as db_sess_vms:
                            vm_names = [v.get("vm_name") for v in payload["vms_metrics"] if v.get("vm_name")]
                            existing_vms = db_sess_vms.query(VM).filter(VM.name.in_(vm_names)).all() if vm_names else []
                            existing_vms_map = {vm.name: vm for vm in existing_vms}
                            
                            new_vms = []
                            for vm_data in payload["vms_metrics"]:
                                vm_name = vm_data.get("vm_name")
                                if not vm_name:
                                    continue
                                    
                                if vm_name in existing_vms_map:
                                    existing_vm = existing_vms_map[vm_name]
                                    existing_vm.vcenter = vm_data.get("vcenter", "unknown")
                                    existing_vm.cluster = vm_data.get("cluster", "unknown")
                                    existing_vm.ip = vm_data.get("ip", "")
                                    existing_vm.os = vm_data.get("os", "")
                                else:
                                    new_vms.append(VM(
                                        name=vm_name,
                                        vcenter=vm_data.get("vcenter", "unknown"),
                                        cluster=vm_data.get("cluster", "unknown"),
                                        ip=vm_data.get("ip", ""),
                                        os=vm_data.get("os", "")
                                    ))
                                    
                            if new_vms:
                                db_sess_vms.bulk_save_objects(new_vms)
                            db_sess_vms.commit()
                    except Exception as vm_err:
                        logger.error(f"Erreur sync inventaire VM PostgreSQL: {str(vm_err)}")

                # → PostgreSQL : Métriques historiques (Analyse Capacitaire) & Anomalies de Saturation
                if payload.get("status") != "OFFLINE" and payload.get("metrics") and "db_id" in cfg:
                    metrics_data = payload["metrics"]
                    try:
                        with SessionLocal() as db_sess:
                            timestamp = datetime.utcnow()
                            device_type_clean = payload.get("device_type", "").upper()
                            
                            # --- GESTION DES ALERTES DE LATENCE (Virtualisation) ---
                            if device_type_clean == "VIRTUALIZATION" or "vcenter" in cfg["name"].lower():
                                if "datastores_metrics" in payload:
                                    threshold_ms = 20.0
                                    # Lire le seuil depuis la DB
                                    setting_threshold = db_sess.query(SystemSetting).filter(SystemSetting.key == "latency_threshold_ms").first()
                                    if setting_threshold:
                                        try:
                                            threshold_ms = float(setting_threshold.value)
                                        except ValueError:
                                            pass
                                    
                                    for ds in payload["datastores_metrics"]:
                                        # Alerte uniquement sur une latence réellement mesurée
                                        latency = ds.get("latency_ms")
                                        if latency is not None and latency > threshold_ms:
                                            one_hour_ago = datetime.utcnow() - timedelta(hours=1)
                                            recent_anomaly = db_sess.query(LatencyAnomaly).filter(
                                                LatencyAnomaly.device_id == cfg["db_id"],
                                                LatencyAnomaly.target_name == ds["datastore_name"],
                                                LatencyAnomaly.timestamp >= one_hour_ago
                                            ).first()
                                            
                                            if not recent_anomaly:
                                                new_anomaly = LatencyAnomaly(
                                                    device_id=cfg["db_id"],
                                                    target_name=ds["datastore_name"],
                                                    latency_ms=latency,
                                                    threshold_ms=threshold_ms,
                                                    timestamp=datetime.utcnow(),
                                                    is_resolved=False
                                                )
                                                db_sess.add(new_anomaly)
                                                
                                                # Envoi d'email asynchrone (ou synchrone court)
                                                try:
                                                    send_latency_alert_email(
                                                        device_name=cfg["name"],
                                                        target_name=ds["datastore_name"],
                                                        latency_ms=latency,
                                                        threshold_ms=threshold_ms
                                                    )
                                                except Exception as e:
                                                    logger.error(f"Erreur envoi email: {e}")

                            history_entries = []
                            if device_type_clean == "VIRTUALIZATION" or "vcenter" in cfg["name"].lower():
                                if "ram_usage_pct" in metrics_data:
                                    ram_pct = float(metrics_data["ram_usage_pct"])
                                    history_entries.append(MetricHistory(
                                        device_id=cfg["db_id"],
                                        resource_name="RAM", unit="%",
                                        capacity_total_tb=100.0, capacity_used_tb=ram_pct,
                                        capacity_free_tb=max(0.0, 100.0 - ram_pct),
                                        usage_pct=ram_pct,
                                        timestamp=timestamp
                                    ))
                                if "cpu_usage_pct" in metrics_data:
                                    cpu_pct = float(metrics_data["cpu_usage_pct"])
                                    history_entries.append(MetricHistory(
                                        device_id=cfg["db_id"],
                                        resource_name="CPU", unit="%",
                                        capacity_total_tb=100.0, capacity_used_tb=cpu_pct,
                                        capacity_free_tb=max(0.0, 100.0 - cpu_pct),
                                        usage_pct=cpu_pct,
                                        timestamp=timestamp
                                    ))
                                if "datastores_total_tb" in metrics_data and "datastores_used_tb" in metrics_data:
                                    cap_used = float(metrics_data["datastores_used_tb"])
                                    cap_total = float(metrics_data["datastores_total_tb"])
                                    usage_pct = float(metrics_data.get("datastores_usage_pct", (cap_used/cap_total*100) if cap_total > 0 else 0))
                                    history_entries.append(MetricHistory(
                                        device_id=cfg["db_id"],
                                        resource_name="Datastores", unit="TB",
                                        capacity_total_tb=cap_total, capacity_used_tb=cap_used,
                                        capacity_free_tb=max(0.0, cap_total - cap_used),
                                        usage_pct=usage_pct,
                                        timestamp=timestamp
                                    ))
                            else:
                                # Equipements de stockage / autres
                                cap_total = metrics_data.get("capacity_total_tb", metrics_data.get("capacity_total"))
                                cap_used = metrics_data.get("capacity_used_tb", metrics_data.get("capacity_used"))
                                if cap_total is not None and cap_used is not None:
                                    cap_total = float(cap_total)
                                    cap_used = float(cap_used)
                                    usage_pct = float(metrics_data.get("capacity_usage_pct", (cap_used/cap_total*100) if cap_total > 0 else 0))
                                    history_entries.append(MetricHistory(
                                        device_id=cfg["db_id"],
                                        resource_name="Storage Pool Global", unit="TB",
                                        capacity_total_tb=cap_total, capacity_used_tb=cap_used,
                                        capacity_free_tb=max(0.0, cap_total - cap_used),
                                        usage_pct=usage_pct,
                                        timestamp=timestamp
                                    ))
                                
                                # Souscription si disponible (Baies de stockage)
                                if "subscription_pct" in metrics_data:
                                    sub_pct = float(metrics_data["subscription_pct"])
                                    history_entries.append(MetricHistory(
                                        device_id=cfg["db_id"],
                                        resource_name="Souscription", unit="%",
                                        capacity_total_tb=100.0, capacity_used_tb=sub_pct,
                                        capacity_free_tb=max(0.0, 100.0 - sub_pct),
                                        usage_pct=sub_pct,
                                        timestamp=timestamp
                                    ))

                            for entry in history_entries:
                                db_sess.add(entry)

                            db_sess.commit()
                            logger.info(f"✓ Historique capacitaire sauvegardé en PostgreSQL pour {cfg['name']}.")
                    except Exception as db_err:
                        logger.error(f"✗ Erreur d'enregistrement historique/anomalies SQL : {str(db_err)}")

                # → Redis : Snapshot temps réel (Dashboard Global)
                r_client.set(
                    f"noc_latest:{device_id}",
                    json.dumps(payload),
                    ex=60  # TTL 60s — données obsolètes si le collecteur échoue
                )

                logger.info(f"✓ Collecte réussie : {cfg['name']} (Status: {payload.get('status', 'UNKNOWN')})")
                results[device_id] = payload

                # ── Étape 4 : Mise à jour du statut de sync dans la DB ─────────
                # Uniquement pour les équipements issus de la DB (ont un db_id)
                if "db_id" in cfg:
                    sync_status = "ONLINE" if payload.get("status") in ["HEALTHY", "WARNING", "ONLINE"] else "OFFLINE"
                    _update_device_sync_status(cfg["db_id"], sync_status)

            except Exception as e:
                logger.error(f"✗ Échec du polling pour {device_id} ({cfg.get('name', '?')}) : {str(e)}")
                # Marquer comme ERROR dans la DB si c'est un device enregistré
                if "db_id" in cfg:
                    _update_device_sync_status(cfg["db_id"], "ERROR")

    # Notifier le WebSocket Frontend qu'une mise à jour est disponible
    try:
        r_client.publish("noc_updates", json.dumps({"type": "UPDATE_AVAILABLE", "timestamp": datetime.utcnow().isoformat()}))
        logger.info("📡 Signal de mise à jour (WebSocket) publié sur Redis.")
    except Exception as e:
        logger.error(f"Erreur lors de la publication Pub/Sub Redis: {e}")

    logger.info(f"◼ Cycle terminé — {len(results)}/{len(collectors_config)} équipement(s) collectés.")
    return results
