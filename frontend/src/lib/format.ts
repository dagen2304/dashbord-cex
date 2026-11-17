const nf = (digits: number) => new Intl.NumberFormat('fr-FR', { maximumFractionDigits: digits, minimumFractionDigits: 0 })

/** Valeur absente → « — » (jamais 0 inventé). */
export function fmt(value: number | string | null | undefined, digits = 1, unit = ''): string {
  if (value === null || value === undefined || value === '') return '—'
  const n = typeof value === 'number' ? value : Number(value)
  if (!Number.isFinite(n)) return String(value)
  return `${nf(digits).format(n)}${unit ? ` ${unit}` : ''}`
}

export function pct(value: number | null | undefined, digits = 1): string {
  return fmt(value, digits, '%')
}

export function signed(value: number | null | undefined, digits = 2, unit = ''): string {
  if (value === null || value === undefined) return '—'
  const s = fmt(Math.abs(value), digits, unit)
  return value > 0 ? `+${s}` : value < 0 ? `−${s}` : s
}

const rtf = new Intl.RelativeTimeFormat('fr', { numeric: 'auto' })

export function relativeTime(iso: string | null | undefined, now = Date.now()): string {
  if (!iso) return 'jamais'
  const t = new Date(iso.endsWith('Z') || /[+-]\d\d:\d\d$/.test(iso) ? iso : `${iso}Z`).getTime()
  if (Number.isNaN(t)) return '—'
  const s = Math.round((t - now) / 1000)
  const abs = Math.abs(s)
  if (abs < 45) return 'à l’instant'
  if (abs < 3600) return rtf.format(Math.round(s / 60), 'minute')
  if (abs < 86400) return rtf.format(Math.round(s / 3600), 'hour')
  return rtf.format(Math.round(s / 86400), 'day')
}

export function dateTime(iso: string | null | undefined): string {
  if (!iso) return '—'
  const d = new Date(iso.endsWith('Z') || /[+-]\d\d:\d\d$/.test(iso) ? iso : `${iso}Z`)
  return d.toLocaleString('fr-FR', { dateStyle: 'short', timeStyle: 'short' })
}

export function shortTime(iso: string, spanHours: number): string {
  const d = new Date(iso)
  return spanHours > 48
    ? d.toLocaleDateString('fr-FR', { day: '2-digit', month: '2-digit' })
    : d.toLocaleTimeString('fr-FR', { hour: '2-digit', minute: '2-digit' })
}

export const CATEGORY_LABEL: Record<string, string> = {
  VIRTUALIZATION: 'Virtualisation',
  STORAGE: 'Stockage',
  BACKUP: 'Sauvegarde',
  OBJECT: 'Objet',
}

export const TYPE_LABEL: Record<string, string> = {
  VCENTER: 'vCenter',
  HUAWEI_DORADO: 'Huawei Dorado',
  DELL_UNITY: 'Dell Unity',
  DELL_POWERSTORE: 'Dell PowerStore',
  DATADOMAIN: 'DataDomain',
  NETBACKUP: 'NetBackup',
  SCALITY: 'Scality RING',
  VERITAS_ACCESS: 'Veritas Access',
}

/** Libellé et unité des métriques connues. */
export const METRIC_META: Record<string, { label: string; unit?: string; digits?: number }> = {
  cpu_usage_pct: { label: 'CPU', unit: '%' },
  ram_usage_pct: { label: 'RAM', unit: '%' },
  datastores_usage_pct: { label: 'Datastores', unit: '%' },
  vms_total: { label: 'VMs', digits: 0 },
  vms_active: { label: 'VMs actives', digits: 0 },
  hosts_total: { label: 'Hôtes', digits: 0 },
  hosts_online: { label: 'Hôtes en ligne', digits: 0 },
  hosts_maintenance: { label: 'Hôtes en maintenance', digits: 0 },
  iops: { label: 'IOPS', digits: 0 },
  latency_ms: { label: 'Latence', unit: 'ms', digits: 2 },
  throughput_mb: { label: 'Débit', unit: 'Mo/s' },
  capacity_usage_pct: { label: 'Capacité utilisée', unit: '%' },
  capacity_used_tb: { label: 'Utilisé', unit: 'To', digits: 2 },
  capacity_total_tb: { label: 'Capacité', unit: 'To', digits: 2 },
  dedup_ratio: { label: 'Déduplication', unit: '×' },
  active_streams: { label: 'Flux actifs', digits: 0 },
  backup_success_rate: { label: 'Succès sauvegardes', unit: '%' },
  jobs_total: { label: 'Jobs', digits: 0 },
  jobs_failed: { label: 'Jobs en échec', digits: 0 },
  jobs_active: { label: 'Jobs en cours', digits: 0 },
  iops_s3: { label: 'IOPS S3', digits: 0 },
  active_nodes: { label: 'Nœuds actifs', digits: 0 },
  total_nodes: { label: 'Nœuds', digits: 0 },
  data_reduction_ratio: { label: 'Réduction', unit: '×' },
  nfs_shares_active: { label: 'Partages NFS', digits: 0 },
  cifs_shares_active: { label: 'Partages CIFS', digits: 0 },
}

export function metricLabel(field: string): string {
  return METRIC_META[field]?.label ?? field
}

export function metricValue(field: string, value: number | string | null | undefined): string {
  const m = METRIC_META[field]
  return fmt(value, m?.digits ?? 1, m?.unit ?? '')
}

/** Indicateurs clés à afficher sur la carte d'un équipement, par catégorie / type. */
export function keyMetrics(category: string, type: string): string[] {
  if (type === 'NETBACKUP') return ['backup_success_rate', 'jobs_failed', 'jobs_active']
  if (type === 'DATADOMAIN') return ['capacity_usage_pct', 'dedup_ratio', 'active_streams']
  if (type === 'SCALITY') return ['capacity_usage_pct', 'iops_s3', 'active_nodes']
  if (type === 'VERITAS_ACCESS') return ['capacity_usage_pct', 'nfs_shares_active', 'cifs_shares_active']
  if (category === 'VIRTUALIZATION') return ['cpu_usage_pct', 'ram_usage_pct', 'datastores_usage_pct']
  return ['capacity_usage_pct', 'iops', 'latency_ms']
}

/** Équipement fictif du mode démonstration (domaine réservé .demo.invalid). */
export function isDemoHost(host: string | null | undefined): boolean {
  return Boolean(host && host.toLowerCase().endsWith('.demo.invalid'))
}
