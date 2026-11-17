import ssl
import logging
from typing import Dict, Any
from pyVmomi import vim, vmodl
from pyVim.connect import SmartConnect, Disconnect
from app.services.base_collector import BaseCollector
from app.services.vsphere_perf import datastore_latencies, datastore_uuid, vm_latencies

logger = logging.getLogger(__name__)


class VCenterCollector(BaseCollector):
    """
    Collecteur de production pour VMware vCenter Server.

    Connexion via SDK pyVmomi (vSphere API officielle).
    Collecte :
        - CPU agrégé de tous les hôtes ESXi du cluster (%)
        - RAM agrégée des hôtes ESXi (%)
        - VMs totales / actives (powerState == poweredOn)
        - Datastores : total TB, utilisé TB, % usage
        - Hôtes ESXi online / total
    """

    # ── Constantes pyVmomi ────────────────────────────────────────────────────
    # Counter IDs stables sur vSphere 6.x / 7.x / 8.x
    _PERF_CPU_USAGE = "cpu.usage.average"          # %
    _PERF_MEM_USAGE = "mem.usage.average"          # %

    def collect(self) -> Dict[str, Any]:
        logger.info(f"[vCenter] Démarrage collecte pour '{self.device_name}' ({self.config.get('host')})")
        return self._collect_real()

    def _collect_properties(self, si, vimtype, properties):
        collector = si.content.propertyCollector
        container = si.content.viewManager.CreateContainerView(si.content.rootFolder, vimtype, True)
        
        try:
            obj_spec = vim.PropertyCollector.ObjectSpec()
            obj_spec.obj = container
            obj_spec.skip = True
            
            traversal_spec = vim.PropertyCollector.TraversalSpec()
            traversal_spec.name = 'traverseEntities'
            traversal_spec.path = 'view'
            traversal_spec.skip = False
            traversal_spec.type = container.__class__
            obj_spec.selectSet = [traversal_spec]
            
            property_spec = vim.PropertyCollector.PropertySpec()
            property_spec.type = vimtype[0]
            property_spec.pathSet = properties
            
            filter_spec = vim.PropertyCollector.FilterSpec()
            filter_spec.objectSet = [obj_spec]
            filter_spec.propSet = [property_spec]
            
            props = collector.RetrievePropertiesEx([filter_spec], vim.PropertyCollector.RetrieveOptions())
            
            data = []
            if props:
                for obj in props.objects:
                    properties_dict = {'obj': obj.obj}
                    if obj.propSet:
                        for prop in obj.propSet:
                            properties_dict[prop.name] = prop.val
                    data.append(properties_dict)
                    
                while props.token:
                    props = collector.ContinueRetrievePropertiesEx(token=props.token)
                    for obj in props.objects:
                        properties_dict = {'obj': obj.obj}
                        if obj.propSet:
                            for prop in obj.propSet:
                                properties_dict[prop.name] = prop.val
                        data.append(properties_dict)
            return data
        finally:
            try:
                container.Destroy()
            except Exception:
                pass

    # ─────────────────────────────────────────────────────────────────────────
    # CONNEXION RÉELLE — pyVmomi vSphere SDK
    # ─────────────────────────────────────────────────────────────────────────
    def _collect_real(self) -> Dict[str, Any]:
        """
        Connexion au vCenter via pyVmomi et collecte complète de l'inventaire.
        Désactive la vérification SSL (environnement lab / certificats auto-signés).
        """

        payload = self.get_base_payload(status="HEALTHY")
        payload["device_type"] = "virtualization"

        host = self.config.get("host")
        port = int(self.config.get("port", 443))
        user = self.config.get("username", "")
        password = self.config.get("password", "")

        si = None
        try:
            # Contexte SSL — désactive la vérification pour les certificats auto-signés
            ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            ssl_context.check_hostname = False
            ssl_context.verify_mode = ssl.CERT_NONE

            logger.info(f"[vCenter] 🔌 Tentative de connexion sécurisée à l'équipement {host}:{port} (User: {user})")
            si = SmartConnect(
                host=host,
                port=port,
                user=user,
                pwd=password,
                sslContext=ssl_context,
            )
            # Note : pas d'atexit.register — le bloc finally ci-dessous gère la déconnexion proprement
            logger.info(f"[vCenter] ✅ Connexion réussie à {host}. Démarrage de la collecte PropertyCollector (ultra-rapide).")

            # ── 1. Inventaire des hôtes ESXi ─────────────────────────────
            logger.info(f"[vCenter] Collecte des ESXi...")
            host_props = [
                'name',
                'parent',
                'runtime.connectionState',
                'runtime.inMaintenanceMode',
                'summary.hardware.cpuMhz',
                'summary.hardware.numCpuCores',
                'summary.hardware.memorySize',
                'summary.quickStats.overallCpuUsage',
                'summary.quickStats.overallMemoryUsage'
            ]
            hosts_data = self._collect_properties(si, [vim.HostSystem], host_props)
            hosts_total = len(hosts_data)
            hosts_online = 0
            hosts_maintenance = 0
            cpu_usages = []
            ram_usages = []
            
            host_info_map = {}
            hosts_metrics_list = []
            
            for h in hosts_data:
                h_obj = h.get('obj')
                
                is_connected = h.get('runtime.connectionState') == vim.HostSystem.ConnectionState.connected
                is_maintenance = h.get('runtime.inMaintenanceMode', False)
                
                if is_connected:
                    hosts_online += 1
                if is_maintenance:
                    hosts_maintenance += 1
                    
                cpu_hz = h.get('summary.hardware.cpuMhz', 0)
                cpu_cores = h.get('summary.hardware.numCpuCores', 0)
                used_cpu = h.get('summary.quickStats.overallCpuUsage') or 0
                mem_bytes = h.get('summary.hardware.memorySize', 0)
                used_mem = h.get('summary.quickStats.overallMemoryUsage') or 0
                
                total_mhz = cpu_hz * cpu_cores
                memory_mb = mem_bytes / (1024 * 1024)
                
                host_cpu_pct = 0.0
                host_ram_pct = 0.0
                
                if total_mhz > 0:
                    host_cpu_pct = round(used_cpu / total_mhz * 100, 1)
                    if is_connected:
                        cpu_usages.append(host_cpu_pct)
                if memory_mb > 0:
                    host_ram_pct = round(used_mem / memory_mb * 100, 1)
                    if is_connected:
                        ram_usages.append(host_ram_pct)
                        
                # Map pour la collecte de VM
                cluster_name = "Unknown Cluster"
                try:
                    parent = h.get('parent')
                    if parent and hasattr(parent, 'name'):
                        cluster_name = parent.name
                    elif h_obj and hasattr(h_obj, 'parent') and hasattr(h_obj.parent, 'name'):
                        cluster_name = h_obj.parent.name
                except Exception:
                    pass
                host_info_map[h_obj] = {
                    'mhz': h.get('summary.hardware.cpuMhz', 2000),
                    'cluster': cluster_name
                }
                
                status_str = "MAINTENANCE" if is_maintenance else ("CONNECTED" if is_connected else "DISCONNECTED")
                hosts_metrics_list.append({
                    "host_name": h.get('name', 'Unknown'),
                    "cluster": cluster_name,
                    "status": status_str,
                    "cpu_usage_pct": min(100.0, max(0.0, host_cpu_pct)),
                    "ram_usage_pct": min(100.0, max(0.0, host_ram_pct)),
                })

            cpu_usage_pct = round(sum(cpu_usages) / len(cpu_usages), 1) if cpu_usages else 0.0
            ram_usage_pct = round(sum(ram_usages) / len(ram_usages), 1) if ram_usages else 0.0

            # ── 2. Inventaire des VMs (Individuel) ─────────────────────────────────────
            logger.info(f"[vCenter] Collecte détaillée des VMs...")
            vm_props = [
                'name',
                'runtime.powerState',
                'runtime.host',
                'summary.config.numCpu',
                'summary.config.memorySizeMB',
                'summary.quickStats.overallCpuUsage',
                'summary.quickStats.guestMemoryUsage',
                'config.hardware.device',
                'guest.guestFullName',
                'guest.ipAddress',
                'guest.disk'
            ]
            vms_data = self._collect_properties(si, [vim.VirtualMachine], vm_props)
            vms_total = len(vms_data)
            vms_active = 0
            powered_on_vms = []  # (objet VM, entrée) : pour rattacher les latences mesurées
            vms_metrics_list = []
            vms_fs_metrics = []
            
            for vm in vms_data:
                if vm.get('runtime.powerState') == vim.VirtualMachinePowerState.poweredOn:
                    vms_active += 1
                    
                    # Extraire métriques individuelles pour InfluxDB
                    host_obj = vm.get('runtime.host')
                    h_info = host_info_map.get(host_obj, {'mhz': 2000, 'cluster': 'Unknown Cluster'})
                    
                    num_cpu = vm.get('summary.config.numCpu')
                    mem_mb = vm.get('summary.config.memorySizeMB')
                    used_cpu_mhz = vm.get('summary.quickStats.overallCpuUsage')
                    used_mem_mb = vm.get('summary.quickStats.guestMemoryUsage')
                    
                    vm_cpu_pct = 0.0
                    vm_ram_pct = 0.0
                    
                    if num_cpu and used_cpu_mhz is not None and h_info['mhz']:
                        total_mhz = num_cpu * h_info['mhz']
                        if total_mhz > 0:
                            vm_cpu_pct = round((used_cpu_mhz / total_mhz) * 100, 1)
                            
                    if mem_mb and used_mem_mb is not None:
                        if mem_mb > 0:
                            vm_ram_pct = round((used_mem_mb / mem_mb) * 100, 1)
                            
                    # Cap à 100% max
                    vm_cpu_pct = min(100.0, max(0.0, vm_cpu_pct))
                    vm_ram_pct = min(100.0, max(0.0, vm_ram_pct))
                    
                    storage_kb = 0
                    devices = vm.get('config.hardware.device')
                    if devices:
                        for device in devices:
                            if type(device).__name__ == 'vim.vm.device.VirtualDisk':
                                storage_kb += getattr(device, 'capacityInKB', 0)
                                
                    storage_gb = round(storage_kb / (1024**2), 1)
                    
                    vm_entry = {
                        "vm_name": vm.get('name', 'Unknown'),
                        "vcenter": host,
                        "cluster": h_info['cluster'],
                        "cpu_usage_pct": vm_cpu_pct,
                        "ram_usage_pct": vm_ram_pct,
                        "storage_gb": storage_gb,
                        "os": vm.get('guest.guestFullName') or "Unknown OS",
                        "ip": vm.get('guest.ipAddress') or "N/A"
                    }
                    vms_metrics_list.append(vm_entry)
                    powered_on_vms.append((vm.get('obj'), vm_entry))
                    
                    # File systems internes de la VM
                    guest_disks = vm.get('guest.disk')
                    if guest_disks:
                        for disk in guest_disks:
                            if hasattr(disk, 'diskPath') and disk.capacity:
                                fs_capacity_gb = round(disk.capacity / (1024**3), 2)
                                fs_free_gb = round(disk.freeSpace / (1024**3), 2) if hasattr(disk, 'freeSpace') and disk.freeSpace else 0.0
                                fs_used_gb = max(0.0, fs_capacity_gb - fs_free_gb)
                                fs_usage_pct = round((fs_used_gb / fs_capacity_gb) * 100, 1) if fs_capacity_gb > 0 else 0.0
                                
                                # Skip completely empty/0 size drives that might be floppy/cdroms
                                if fs_capacity_gb > 0:
                                    vms_fs_metrics.append({
                                        "vm_name": vm.get('name', 'Unknown'),
                                        "vcenter": host,
                                        "cluster": h_info['cluster'],
                                        "mount_point": disk.diskPath,
                                        "capacity_gb": fs_capacity_gb,
                                        "free_gb": fs_free_gb,
                                        "used_gb": round(fs_used_gb, 2),
                                        "usage_pct": fs_usage_pct
                                    })

            # ── 3. Datastores ─────────────────────────────────────────────
            logger.info(f"[vCenter] Collecte des Datastores...")
            ds_props = ['name', 'summary.accessible', 'summary.capacity', 'summary.freeSpace', 'summary.url']
            ds_data = self._collect_properties(si, [vim.Datastore], ds_props)
            
            ds_total_bytes = 0
            ds_free_bytes = 0
            ds_metrics_list = []
            ds_uuid_to_name = {}

            for ds in ds_data:
                if ds.get('summary.accessible'):
                    ds_cap = ds.get('summary.capacity') or 0
                    ds_free = ds.get('summary.freeSpace') or 0
                    ds_total_bytes += ds_cap
                    ds_free_bytes += ds_free
                    
                    ds_used = ds_cap - ds_free
                    ds_usage_pct = round((ds_used / ds_cap * 100), 1) if ds_cap > 0 else 0.0
                    
                    uuid = datastore_uuid(ds.get('summary.url'))
                    if uuid:
                        ds_uuid_to_name[uuid] = ds.get('name', 'Unknown')
                    ds_metrics_list.append({
                        "datastore_name": ds.get('name', 'Unknown'),
                        "capacity_tb": round(ds_cap / (1024**4), 2),
                        "used_tb": round(ds_used / (1024**4), 2),
                        "usage_pct": ds_usage_pct,
                    })
                    
            ds_used_bytes = ds_total_bytes - ds_free_bytes
            ds_total_tb = round(ds_total_bytes / (1024 ** 4), 2)
            ds_used_tb = round(ds_used_bytes / (1024 ** 4), 2)
            ds_usage_pct = round((ds_used_bytes / ds_total_bytes * 100), 1) if ds_total_bytes > 0 else 0.0

            # ── 4. Latences disque réelles (PerformanceManager) ───────────
            alerts = []
            latency_max_ms = None
            try:
                perf = si.content.perfManager
                connected_hosts = [h.get('obj') for h in hosts_data
                                   if h.get('runtime.connectionState') == vim.HostSystem.ConnectionState.connected]
                ds_lat = datastore_latencies(perf, connected_hosts, ds_uuid_to_name)
                for entry in ds_metrics_list:
                    lat = ds_lat.get(entry["datastore_name"])
                    if lat:
                        entry.update(lat)
                        entry["latency_ms"] = max(lat.values())
                measured = [e["latency_ms"] for e in ds_metrics_list if "latency_ms" in e]
                latency_max_ms = max(measured) if measured else None

                vm_lat = vm_latencies(perf, [obj for obj, _ in powered_on_vms if obj is not None])
                for obj, entry in powered_on_vms:
                    if obj in vm_lat:
                        entry.update(vm_lat[obj])
                logger.info(f"[vCenter] Latences mesurées : {len(measured)} datastore(s), {len(vm_lat)} VM(s)")
            except Exception as perf_err:
                # Sans latence, le reste de la collecte reste valable : on le signale, sans inventer de valeur.
                logger.warning(f"[vCenter] Latences non collectées sur {host} : {perf_err}")
                alerts.append({
                    "severity": "INFO",
                    "message": f"Latences disque non collectées (PerformanceManager) : {type(perf_err).__name__}",
                    "component": "PerformanceManager",
                })

            # ── 5. Alertes de santé ───────────────────────────────────────
            status = "HEALTHY"

            if cpu_usage_pct > 85:
                status = "WARNING"
                alerts.append({
                    "severity": "WARNING",
                    "message": f"Charge CPU cluster élevée : {cpu_usage_pct}%",
                    "component": "Cluster-ESXi"
                })
            if ram_usage_pct > 90:
                status = "CRITICAL" if ram_usage_pct > 95 else "WARNING"
                alerts.append({
                    "severity": status,
                    "message": f"Charge RAM cluster élevée : {ram_usage_pct}%",
                    "component": "Cluster-ESXi"
                })
            if ds_usage_pct > 80:
                alerts.append({
                    "severity": "WARNING",
                    "message": f"Datastores à {ds_usage_pct}% de capacité",
                    "component": "Datastores"
                })

            payload["status"] = status
            payload["alerts"] = alerts
            payload["metrics"] = {
                "cpu_usage_pct": cpu_usage_pct,
                "ram_usage_pct": ram_usage_pct,
                "vms_total": vms_total,
                "vms_active": vms_active,
                "hosts_total": hosts_total,
                "hosts_online": hosts_online,
                "hosts_maintenance": hosts_maintenance,
                "datastores_total_tb": ds_total_tb,
                "datastores_used_tb": ds_used_tb,
                "datastores_usage_pct": ds_usage_pct,
                **({"latency_ms": latency_max_ms} if latency_max_ms is not None else {}),
            }
            payload["vms_metrics"] = vms_metrics_list
            payload["hosts_metrics"] = hosts_metrics_list
            payload["datastores_metrics"] = ds_metrics_list
            if "vms_fs_metrics" in locals():
                payload["vms_fs_metrics"] = vms_fs_metrics
            else:
                payload["vms_fs_metrics"] = []

            logger.info(
                f"[vCenter] ✓ Collecte réussie — {vms_active}/{vms_total} VMs, "
                f"CPU {cpu_usage_pct}%, RAM {ram_usage_pct}%, DS {ds_usage_pct}%"
            )
            return payload

        except Exception as e:
            logger.error(f"[vCenter] ✗ Connexion échouée vers {host}: {e}")
            payload["status"] = "OFFLINE"
            payload["alerts"] = [{
                "severity": "CRITICAL",
                "message": f"Impossible de se connecter au vCenter {host}:{port} — {type(e).__name__}: {e}",
                "component": "vSphere-API"
            }]
            payload["metrics"] = {}
            return payload

        finally:
            if si:
                try:
                    Disconnect(si)
                except Exception:
                    pass
