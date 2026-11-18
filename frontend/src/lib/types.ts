export type Role = 'viewer' | 'operator' | 'admin'

export interface User {
  id: number
  username: string
  full_name: string | null
  role: Role
  is_active: boolean
  auth_source: 'local' | 'ldap'
  created_at: string
  last_login_at: string | null
}

export type DeviceStatus = 'HEALTHY' | 'WARNING' | 'CRITICAL' | 'OFFLINE' | 'ERROR' | 'NO_DATA' | string

export interface DeviceAlert {
  severity: 'CRITICAL' | 'WARNING' | 'INFO' | string
  message: string
  component?: string | null
}

export interface SeriesPoint {
  timestamp: string
  value: number
}

export interface DeviceState {
  device_id: string
  device_name: string
  device_type: string
  category: 'VIRTUALIZATION' | 'STORAGE' | 'BACKUP' | 'OBJECT' | string
  hostname_ip: string
  is_active: boolean
  last_sync_status: string
  last_sync_error: string | null
  collected_at: string | null
  stale: boolean
  status: DeviceStatus
  metrics: Record<string, number | string | null>
  alerts: DeviceAlert[]
  sparkline?: { field: string; points: SeriesPoint[] }
  hosts_metrics?: Array<{ host_name: string; cluster: string; status: string; cpu_usage_pct: number; ram_usage_pct: number }>
  datastores_metrics?: Array<{ datastore_name: string; capacity_tb: number; used_tb: number; usage_pct: number; latency_ms?: number; read_latency_ms?: number; write_latency_ms?: number }>
  /** vRealize Operations : champ absent = valeur non fournie par vROps. */
  clusters_metrics?: VropsCluster[]
  vrops_datastores?: Array<{ name: string; capacity_tb?: number; used_tb?: number; usage_pct?: number; time_remaining_days?: number; health?: number }>
  rightsizing?: Array<{ vm_name: string; kind: 'oversized' | 'undersized' | 'idle'; vcpus?: number; memory_gb?: number }>
}

export interface VropsCluster {
  name: string
  cpu_usage_pct?: number
  ram_usage_pct?: number
  time_remaining_days?: number
  capacity_remaining_pct?: number
  health?: number
  risk?: number
  efficiency?: number
  running_vms?: number
}

export interface Device {
  id: string
  display_name: string
  hostname_ip: string
  device_type: string
  category: string
  port: number
  username: string
  is_active: boolean
  is_archived: boolean
  last_sync_status: string
  last_sync_at: string | null
  last_sync_error: string | null
  tls_mode: TlsMode
  tls_fingerprint: string | null
}

export type TlsMode = 'verify' | 'pin' | 'insecure'

export interface TlsInspection {
  kind: 'tls' | 'ssh'
  sha256: string
  trusted: boolean
  trust_error: string | null
  subject?: string
  issuer?: string
  self_signed?: boolean
  subject_alt_names?: string[]
  not_before?: string
  not_after?: string
  expired?: boolean
  key_type?: string
}

export interface Vendor {
  device_type: string
  port: number
  category: string
  label: string
}

export interface Setting {
  key: string
  value: string
  description: string | null
  category: string
}

export interface ActiveAlert {
  device_id: string
  device_name: string
  device_type: string
  severity: string
  component: string | null
  message: string
  collected_at: string | null
}

export interface LatencyAnomaly {
  id: number
  device_id: string | null
  device_name: string | null
  target_name: string
  latency_ms: number
  threshold_ms: number
  timestamp: string
  is_resolved: boolean
}

export interface CapacityRow {
  device_id: string
  device_name: string
  device_type: string
  category: string
  is_archived: boolean
  resource_name: string
  unit: string
  capacity_total_tb: number | null
  capacity_used_tb: number | null
  capacity_free_tb: number | null
  usage_pct: number | null
  w2w_diff_tb: number | null
  w2w_growth_pct: number | null
  mom_diff_tb: number | null
  mom_growth_pct: number | null
  growth_per_day_tb: number | null
  days_to_full: number | null
  severity: 'HEALTHY' | 'WARNING' | 'CRITICAL'
  points: number
}

export interface VmRef {
  id: number
  name: string
  vcenter: string
  cluster: string
  ip: string | null
  os: string | null
}

export interface Application {
  id: number
  name: string
  description: string | null
  vms: VmRef[]
}

export interface AppMetrics {
  summary: { avg_cpu: number | null; avg_ram: number | null; total_storage_tb: number; vms_active: number; vms_total: number }
  vms: Array<VmRef & { cpu_usage_pct: number | null; ram_usage_pct: number | null; storage_used_gb: number | null; status: string }>
}

export interface SaturationRow {
  vm_name: string
  vcenter: string
  cluster: string
  resource: 'CPU' | 'RAM'
  max_value: number
  duration_minutes: number
  os: string | null
  ip: string | null
}

export interface FilesystemRow {
  id: string
  vm_name: string
  vcenter: string
  cluster: string
  mount_point: string
  capacity_gb: number | null
  free_gb: number | null
  used_gb: number | null
  usage_pct: number | null
}

export interface VmLatency {
  vm_name: string
  device_id: string
  peak_ms: number
  history: Array<{ timestamp: string; read_latency_ms: number | null; write_latency_ms: number | null }>
}

export interface AuditEvent {
  id: number
  timestamp: string
  username: string
  action: string
  target: string | null
  details: string | null
}
