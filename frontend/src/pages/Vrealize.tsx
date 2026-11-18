import { useMemo, useState, type ReactNode } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import clsx from 'clsx'
import {
  AlertTriangle, BellRing, Camera, CheckCircle2, CircleDashed, Clock, Cpu, Gauge, HardDrive, OctagonAlert, Search, Timer,
  type LucideIcon,
} from 'lucide-react'
import { api } from '@/lib/api'
import { dateTime, fmt, isDemoHost, relativeTime } from '@/lib/format'
import type {
  DeviceState, HealthColor, SeriesPoint, VropsAlert, VropsCluster, VropsContention, VropsHost, VropsRightsizing,
} from '@/lib/types'
import { TimeSeriesChart } from '@/components/charts'
import {
  Card, DemoBadge, EmptyState, ErrorState, FilterField, Input, PageHeader, Segmented, Select, Spinner, Stat, StatusBadge, Tabs, UsageBar,
} from '@/components/ui'

type View = 'synthese' | 'capacite' | 'performance' | 'optimisation' | 'alertes'
const VIEWS: View[] = ['synthese', 'capacite', 'performance', 'optimisation', 'alertes']

// ── Petits éléments communs ─────────────────────────────────────────────────
const HEALTH: Record<HealthColor, { label: string; bar: string; text: string; icon: LucideIcon }> = {
  GREEN: { label: 'Bon', bar: 'bg-ok', text: 'text-ok', icon: CheckCircle2 },
  YELLOW: { label: 'Avertissement', bar: 'bg-warn', text: 'text-warn', icon: AlertTriangle },
  ORANGE: { label: 'Immédiat', bar: 'bg-crit/60', text: 'text-crit', icon: AlertTriangle },
  RED: { label: 'Critique', bar: 'bg-crit', text: 'text-crit', icon: OctagonAlert },
  GREY: { label: 'Inconnu', bar: 'bg-nodata', text: 'text-nodata', icon: CircleDashed },
}
const COLORS: HealthColor[] = ['GREEN', 'YELLOW', 'ORANGE', 'RED', 'GREY']

/** Santé vROps : icône + libellé, jamais la couleur seule. */
function Health({ color }: { color?: HealthColor }) {
  const h = HEALTH[color ?? 'GREY']
  return (
    <span className="inline-flex items-center gap-1.5 whitespace-nowrap text-2xs">
      <h.icon className={clsx('size-3.5', h.text)} aria-hidden />{h.label}
    </span>
  )
}

/** Jours avant saturation ; au-delà d'un an, la précision n'a plus de sens. */
function Days({ value }: { value?: number }) {
  if (value === undefined || value === null) return <span className="text-muted">—</span>
  const tone = value < 30 ? 'text-crit' : value < 90 ? 'text-warn' : undefined
  return (
    <span className={clsx('num inline-flex items-center gap-1', tone)}>
      {value < 30 ? <AlertTriangle className="size-3.5" aria-hidden /> : null}
      {value >= 365 ? '> 1 an' : fmt(value, 0, 'j')}
    </span>
  )
}

/** Score vROps 0–100. `higherIsBetter` faux pour le risque. */
function Score({ label, value, higherIsBetter = true }: { label: string; value?: number; higherIsBetter?: boolean }) {
  const known = value !== undefined && value !== null && value >= 0
  const good = known ? (higherIsBetter ? value : 100 - value) : 0
  const [tone, word] = !known ? ['text-muted', 'inconnu'] : good >= 75 ? ['text-ok', 'bon'] : good >= 50 ? ['text-warn', 'moyen'] : ['text-crit', 'mauvais']
  return (
    <div className="rounded-md bg-surface-2 px-2 py-1.5 text-center">
      <p className="text-2xs text-muted">{label}</p>
      <p className={clsx('num text-base font-medium', tone)}>{known ? fmt(value, 0) : '—'}</p>
      <p className="text-2xs text-muted">{word}</p>
    </div>
  )
}

/** Valeur mise en évidence quand elle dépasse son seuil. */
function Over({ value, limit, digits = 1, unit }: { value?: number; limit: number; digits?: number; unit: string }) {
  if (value === undefined || value === null) return <span className="text-muted">—</span>
  const over = value > limit
  return (
    <span className={clsx('num inline-flex items-center gap-1', over && 'font-medium text-crit')} title={over ? `Au-dessus du seuil (${limit} ${unit})` : undefined}>
      {over ? <AlertTriangle className="size-3.5" aria-hidden /> : null}{fmt(value, digits, unit)}
    </span>
  )
}

function SearchBox({ value, onChange, label }: { value: string; onChange: (v: string) => void; label: string }) {
  return (
    <div className="relative w-full sm:w-64">
      <Search className="pointer-events-none absolute left-2.5 top-1/2 size-4 -translate-y-1/2 text-muted" aria-hidden />
      <Input aria-label={label} placeholder={label} value={value} onChange={(e) => onChange(e.target.value)} className="pl-8" />
    </div>
  )
}

function TableWrap({ children }: { children: ReactNode }) {
  return <div className="max-h-[520px] overflow-auto">{children}</div>
}

const includes = (text: string | undefined | null, q: string) => !q || (text ?? '').toLowerCase().includes(q.toLowerCase())

// ── Page ────────────────────────────────────────────────────────────────────
export default function Vrealize() {
  const [params, setParams] = useSearchParams()
  const view = (VIEWS.includes(params.get('vue') as View) ? params.get('vue') : 'synthese') as View
  const setView = (v: View) => setParams((p) => { p.set('vue', v); return p }, { replace: true })

  const sources = useQuery({
    queryKey: ['realtime', 'light'],
    queryFn: () => api.get<DeviceState[]>('/metrics/realtime', { with_sparkline: false }),
    select: (all) => all.filter((d) => d.device_type === 'VROPS'),
    refetchInterval: 60_000,
  })
  const sourceId = params.get('source') ?? sources.data?.[0]?.device_id
  const q = useQuery({
    queryKey: ['device', sourceId],
    queryFn: () => api.get<DeviceState>(`/metrics/devices/${sourceId}`),
    enabled: Boolean(sourceId),
    refetchInterval: 60_000,
  })

  if (sources.isLoading) return <Spinner />
  if (sources.isError) return <ErrorState error={sources.error} onRetry={() => sources.refetch()} />
  if (!sources.data?.length) {
    return (
      <>
        <PageHeader title="vRealize Operations" />
        <Card>
          <EmptyState icon={Gauge} title="Aucune source vRealize Operations">
            Ajoutez votre vROps depuis <Link to="/inventory" className="text-accent underline">l’inventaire</Link> (type « VMware vRealize Operations », compte en lecture seule).
          </EmptyState>
        </Card>
      </>
    )
  }

  const d = q.data
  return (
    <>
      <PageHeader
        title="vRealize Operations"
        description="Analyse de la plateforme virtuelle par vROps : santé, capacité, contention, optimisation et alertes. Rafraîchi toutes les 5 minutes."
        actions={
          <div className="flex flex-wrap items-center gap-3">
            {sources.data.length > 1 ? (
              <Select aria-label="Source vROps" className="!w-auto" value={sourceId} onChange={(e) => setParams((p) => { p.set('source', e.target.value); return p })}>
                {sources.data.map((s) => <option key={s.device_id} value={s.device_id}>{s.device_name}</option>)}
              </Select>
            ) : null}
            {d ? (
              <>
                <span className="flex items-center gap-1.5 text-2xs text-muted" title={dateTime(d.collected_at)}>
                  <Clock className="size-3.5" aria-hidden />Collecté {relativeTime(d.collected_at)}{d.stale && d.collected_at ? ' (périmé)' : ''}
                </span>
                {isDemoHost(d.hostname_ip) ? <DemoBadge /> : null}
                <StatusBadge status={d.status} />
              </>
            ) : null}
          </div>
        }
      />
      {q.isLoading ? <Spinner /> : q.isError || !d ? <ErrorState error={q.error} onRetry={() => q.refetch()} /> :
        d.status === 'NO_DATA' || d.status === 'OFFLINE' || d.status === 'ERROR' ? (
          <Card>
            <EmptyState icon={CircleDashed} title={d.status === 'NO_DATA' ? 'Aucune donnée collectée' : 'vROps injoignable'}>
              {d.alerts[0]?.message ?? d.last_sync_error ?? 'La première collecte n’a pas encore eu lieu.'}{' '}
              <Link to={`/devices/${d.device_id}`} className="text-accent underline">Voir la fiche de l’équipement</Link>.
            </EmptyState>
          </Card>
        ) : (
          <>
            <Tabs<View>
              value={view}
              onChange={setView}
              items={[
                { value: 'synthese', label: 'Synthèse' },
                { value: 'capacite', label: 'Capacité' },
                { value: 'performance', label: 'Performance', count: d.vm_contention?.length },
                { value: 'optimisation', label: 'Optimisation', count: d.rightsizing?.length },
                { value: 'alertes', label: 'Alertes', count: d.vrops_alerts?.length },
              ]}
            />
            {view === 'synthese' ? <Synthese d={d} onView={setView} /> : null}
            {view === 'capacite' ? <Capacite d={d} /> : null}
            {view === 'performance' ? <Performance d={d} /> : null}
            {view === 'optimisation' ? <Optimisation d={d} /> : null}
            {view === 'alertes' ? <Alertes alerts={d.vrops_alerts ?? []} /> : null}
            <Diagnostics d={d} />
          </>
        )}
    </>
  )
}

// ── Synthèse ────────────────────────────────────────────────────────────────
function Synthese({ d, onView }: { d: DeviceState; onView: (v: View) => void }) {
  const m = d.metrics as Record<string, number | undefined>
  const days = m.capacity_time_remaining_days
  const urgent = (d.vrops_alerts ?? []).filter((a) => a.level === 'CRITICAL' || a.level === 'IMMEDIATE').slice(0, 6)
  return (
    <>
      <div className="mb-4 grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-5">
        <Stat icon={BellRing} label="Alertes actives" tone={m.alerts_critical ? 'crit' : m.alerts_immediate ? 'warn' : 'ok'}
          value={fmt((m.alerts_critical ?? 0) + (m.alerts_immediate ?? 0) + (m.alerts_warning ?? 0), 0)}
          sub={`${fmt(m.alerts_critical, 0)} critique(s) · ${fmt(m.alerts_immediate, 0)} immédiate(s) · ${fmt(m.alerts_warning, 0)} avert.`} />
        <Stat icon={Timer} label="Avant saturation" tone={days === undefined ? 'nodata' : days < 30 ? 'crit' : days < 90 ? 'warn' : 'ok'}
          value={days === undefined ? '—' : days >= 365 ? '> 1 an' : fmt(days, 0, 'j')} sub="Objet le plus proche de la saturation" />
        <Stat icon={Cpu} label="VMs en contention" tone={m.vms_contention ? 'warn' : 'ok'} value={fmt(m.vms_contention, 0)}
          sub={`sur ${fmt(m.vms_total, 0)} VMs`} />
        <Stat icon={Gauge} label="Récupérable" value={`${fmt(m.reclaimable_vcpus, 0)} vCPU`}
          sub={`${fmt(m.reclaimable_memory_gb, 0, 'Go')} de RAM · ${fmt(m.vms_idle, 0)} VM(s) inactive(s)`} />
        <Stat icon={Camera} label="Snapshots" value={fmt(m.snapshots_gb, 0, 'Go')} sub={`${fmt(d.snapshots?.length, 0)} VM(s) concernée(s)`} />
      </div>

      <div className="mb-4 grid grid-cols-1 gap-4 xl:grid-cols-3">
        <Card title="Santé des objets" className="xl:col-span-1">
          <HealthSummary summary={d.health_summary} />
        </Card>
        <Card title="Alertes critiques et immédiates" className="xl:col-span-2" bodyClassName="p-0"
          actions={<button type="button" className="text-2xs text-accent hover:underline" onClick={() => onView('alertes')}>Toutes les alertes</button>}>
          {urgent.length ? (
            <ul className="divide-y divide-border">
              {urgent.map((a, i) => (
                <li key={i} className="flex flex-wrap items-center gap-x-3 gap-y-1 px-4 py-2.5 text-sm">
                  <LevelBadge level={a.level} />
                  <span className="min-w-0 flex-1">{a.name}</span>
                  <span className="font-mono text-2xs text-fg-2">{a.object ?? '—'}</span>
                  <span className="text-2xs text-muted" title={dateTime(a.started_at)}>{relativeTime(a.started_at)}</span>
                </li>
              ))}
            </ul>
          ) : <EmptyState icon={CheckCircle2} title="Aucune alerte critique ou immédiate" />}
        </Card>
      </div>

      <h2 className="mb-2 text-sm font-medium">Clusters</h2>
      <div className="grid grid-cols-1 gap-3 md:grid-cols-2 2xl:grid-cols-4">
        {(d.clusters_metrics ?? []).map((c) => <ClusterCard key={c.name} c={c} />)}
      </div>
    </>
  )
}

function HealthSummary({ summary }: { summary?: DeviceState['health_summary'] }) {
  if (!summary) return <EmptyState title="Non fourni par vROps" />
  return (
    <div className="flex flex-col gap-4">
      {Object.entries(summary).map(([kind, counts]) => {
        const total = COLORS.reduce((s, c) => s + (counts[c] ?? 0), 0)
        return (
          <div key={kind}>
            <div className="mb-1 flex items-baseline justify-between text-2xs">
              <span className="font-medium text-fg">{kind}s</span>
              <span className="num text-muted">{fmt(total, 0)}</span>
            </div>
            <div className="flex h-2.5 overflow-hidden rounded-full bg-surface-2" role="img"
              aria-label={`${kind}s : ${COLORS.filter((c) => counts[c]).map((c) => `${counts[c]} ${HEALTH[c].label.toLowerCase()}`).join(', ')}`}>
              {total ? COLORS.map((c) => counts[c] ? <div key={c} className={clsx('h-full', HEALTH[c].bar)} style={{ width: `${(counts[c] / total) * 100}%` }} /> : null) : null}
            </div>
            <div className="mt-1 flex flex-wrap gap-x-3 text-2xs text-muted">
              {COLORS.filter((c) => counts[c] && c !== 'GREEN').map((c) => (
                <span key={c} className="inline-flex items-center gap-1"><span className={clsx('size-2 rounded-full', HEALTH[c].bar)} />{counts[c]} {HEALTH[c].label.toLowerCase()}</span>
              ))}
              {COLORS.every((c) => c === 'GREEN' || !counts[c]) ? <span className="inline-flex items-center gap-1"><CheckCircle2 className="size-3 text-ok" aria-hidden />tous en bonne santé</span> : null}
            </div>
          </div>
        )
      })}
    </div>
  )
}

function ClusterCard({ c }: { c: VropsCluster }) {
  return (
    <div className="card flex flex-col gap-3 p-4">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="truncate font-mono text-sm font-medium">{c.name}</p>
          <p className="text-2xs text-muted">{c.vcenter ?? 'vCenter inconnu'} · {fmt(c.hosts, 0)} hôte(s) · {fmt(c.running_vms, 0)} VM(s)</p>
        </div>
        <Health color={c.color} />
      </div>
      <div className="grid grid-cols-[3rem_1fr] items-center gap-x-2 gap-y-1.5 text-2xs">
        <span className="text-muted">CPU</span><UsageBar value={c.cpu_usage_pct} />
        <span className="text-muted">RAM</span><UsageBar value={c.ram_usage_pct} />
      </div>
      <div className="flex items-center justify-between border-t border-border pt-2 text-2xs">
        <span className="text-muted">Avant saturation</span><Days value={c.time_remaining_days} />
      </div>
      <div className="grid grid-cols-3 gap-2">
        <Score label="Santé" value={c.health} />
        <Score label="Risque" value={c.risk} higherIsBetter={false} />
        <Score label="Efficacité" value={c.efficiency} />
      </div>
    </div>
  )
}

// ── Capacité ────────────────────────────────────────────────────────────────
const TREND_RANGES = [{ value: 24, label: '24 h' }, { value: 168, label: '7 j' }, { value: 720, label: '30 j' }]

function Capacite({ d }: { d: DeviceState }) {
  const clusters = useMemo(() => [...(d.clusters_metrics ?? [])].sort((a, b) => (a.time_remaining_days ?? 1e9) - (b.time_remaining_days ?? 1e9)), [d.clusters_metrics])
  const [selected, setSelected] = useState<string | null>(null)
  const cluster = selected ?? clusters[0]?.name
  return (
    <>
      <Card className="mb-4" title="Jours avant saturation par cluster" bodyClassName="p-0">
        <TableWrap>
          <table className="table-base">
            <thead><tr>
              <th>Cluster</th><th className="text-right">Global</th><th className="text-right">CPU</th><th className="text-right">Mémoire</th>
              <th className="text-right">Disque</th><th className="text-right">Capacité restante</th><th className="w-44">Demande CPU</th>
            </tr></thead>
            <tbody>
              {clusters.map((c) => (
                <tr key={c.name} className={clsx('cursor-pointer', c.name === cluster && 'bg-surface-2')} onClick={() => setSelected(c.name)}>
                  <td className="font-mono text-2xs">{c.name}</td>
                  <td className="text-right"><Days value={c.time_remaining_days} /></td>
                  <td className="text-right"><Days value={c.cpu_time_remaining_days} /></td>
                  <td className="text-right"><Days value={c.ram_time_remaining_days} /></td>
                  <td className="text-right"><Days value={c.disk_time_remaining_days} /></td>
                  <td className="num text-right">{fmt(c.capacity_remaining_pct, 0, '%')}</td>
                  <td><UsageBar value={c.cpu_demand_pct} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        </TableWrap>
      </Card>

      {cluster ? <ClusterTrend name={cluster} names={clusters.map((c) => c.name)} onSelect={setSelected} /> : null}

      <Card title={`Datastores (${d.vrops_datastores?.length ?? 0})`} bodyClassName="p-0">
        <TableWrap>
          <table className="table-base">
            <thead><tr><th>Datastore</th><th>Santé</th><th className="text-right">Capacité</th><th className="text-right">Utilisé</th><th className="w-56">Utilisation</th><th className="text-right">Avant saturation</th></tr></thead>
            <tbody>
              {(d.vrops_datastores ?? []).map((ds) => (
                <tr key={ds.name}>
                  <td className="font-mono text-2xs">{ds.name}</td>
                  <td><Health color={ds.color} /></td>
                  <td className="num text-right">{fmt(ds.capacity_tb, 2, 'To')}</td>
                  <td className="num text-right">{fmt(ds.used_tb, 2, 'To')}</td>
                  <td><UsageBar value={ds.usage_pct} /></td>
                  <td className="text-right"><Days value={ds.time_remaining_days} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        </TableWrap>
      </Card>
    </>
  )
}

function ClusterTrend({ name, names, onSelect }: { name: string; names: string[]; onSelect: (n: string) => void }) {
  const [hours, setHours] = useState(168)
  const fetchField = (field: string) => api.get<{ history: SeriesPoint[] }>('/metrics/explorer/history',
    { object_type: 'Cluster', object_name: name, metric_field: field, hours })
  const q = useQuery({
    queryKey: ['cluster-trend', name, hours],
    queryFn: async () => {
      const [cpu, ram] = await Promise.all([fetchField('cpu_usage_pct'), fetchField('ram_usage_pct')])
      const rows = new Map<string, { timestamp: string; cpu?: number; ram?: number }>()
      for (const p of cpu.history) rows.set(p.timestamp, { ...rows.get(p.timestamp), timestamp: p.timestamp, cpu: p.value })
      for (const p of ram.history) rows.set(p.timestamp, { ...rows.get(p.timestamp), timestamp: p.timestamp, ram: p.value })
      return [...rows.values()].sort((a, b) => a.timestamp.localeCompare(b.timestamp))
    },
  })
  return (
    <Card className="mb-4" title="Tendance CPU / RAM"
      actions={<Segmented label="Période" value={hours} onChange={setHours} items={TREND_RANGES} />}>
      <div className="mb-3 max-w-xs">
        <FilterField label="Cluster">
          {(id) => <Select id={id} value={name} onChange={(e) => onSelect(e.target.value)}>{names.map((n) => <option key={n}>{n}</option>)}</Select>}
        </FilterField>
      </div>
      {q.isLoading ? <Spinner /> : q.data?.length ? (
        <TimeSeriesChart data={q.data} series={[{ key: 'cpu', label: 'CPU' }, { key: 'ram', label: 'RAM' }]} unit="%" yMax={100} spanHours={hours}
          thresholds={[{ value: 90, label: 'Critique 90 %', tone: 'crit' }, { value: 80, label: 'Attention 80 %', tone: 'warn' }]}
          ariaLabel={`Tendance CPU et RAM du cluster ${name}`} />
      ) : <EmptyState title="Pas encore d’historique">L’historique se construit à chaque collecte (toutes les 5 minutes).</EmptyState>}
    </Card>
  )
}

// ── Performance ─────────────────────────────────────────────────────────────
function Performance({ d }: { d: DeviceState }) {
  const [cluster, setCluster] = useState('')
  const [search, setSearch] = useState('')
  const hosts = (d.vrops_hosts ?? []).filter((h) => (!cluster || h.cluster === cluster) && includes(h.name, search))
  const clusters = [...new Set((d.vrops_hosts ?? []).map((h) => h.cluster).filter(Boolean))] as string[]
  const contention = d.vm_contention ?? []
  return (
    <>
      <Card className="mb-4" title={`VMs en contention (${contention.length})`} bodyClassName="p-0">
        <p className="border-b border-border px-4 py-2 text-2xs text-muted">
          Seuils : CPU ready &gt; 5 %, co-stop &gt; 3 %, ballooning &gt; 1 %, latence disque &gt; 20 ms. Triées par intensité.
        </p>
        {contention.length ? (
          <TableWrap>
            <table className="table-base">
              <thead><tr>
                <th>VM</th><th>Cluster</th><th className="text-right">CPU ready</th><th className="text-right">Co-stop</th>
                <th className="text-right">Ballooning</th><th className="text-right">Latence disque</th><th className="w-40">CPU</th><th className="w-40">RAM</th>
              </tr></thead>
              <tbody>
                {contention.map((v: VropsContention) => (
                  <tr key={v.vm_name}>
                    <td className="font-mono text-2xs">{v.vm_name}</td>
                    <td className="text-2xs">{v.cluster ?? '—'}</td>
                    <td className="text-right"><Over value={v.cpu_ready_pct} limit={5} unit="%" /></td>
                    <td className="text-right"><Over value={v.cpu_costop_pct} limit={3} unit="%" /></td>
                    <td className="text-right"><Over value={v.ram_balloon_pct} limit={1} unit="%" /></td>
                    <td className="text-right"><Over value={v.disk_latency_ms} limit={20} unit="ms" /></td>
                    <td><UsageBar value={v.cpu_usage_pct} /></td>
                    <td><UsageBar value={v.ram_usage_pct} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </TableWrap>
        ) : <EmptyState icon={CheckCircle2} title="Aucune VM en contention">Aucune VM ne dépasse les seuils.</EmptyState>}
      </Card>

      <Card title={`Hôtes ESXi (${hosts.length})`} bodyClassName="p-0"
        actions={
          <div className="flex flex-wrap items-center gap-2">
            <Select aria-label="Cluster" className="!w-auto" value={cluster} onChange={(e) => setCluster(e.target.value)}>
              <option value="">Tous les clusters</option>
              {clusters.map((c) => <option key={c}>{c}</option>)}
            </Select>
            <SearchBox value={search} onChange={setSearch} label="Rechercher un hôte" />
          </div>
        }>
        <TableWrap>
          <table className="table-base">
            <thead><tr><th>Hôte</th><th>Cluster</th><th>Santé</th><th>État</th><th className="w-44">CPU</th><th className="w-44">RAM</th><th className="text-right">VMs</th></tr></thead>
            <tbody>
              {hosts.map((h: VropsHost) => (
                <tr key={h.name}>
                  <td className="font-mono text-2xs">{h.name}</td>
                  <td className="text-2xs">{h.cluster ?? '—'}</td>
                  <td><Health color={h.color} /></td>
                  <td><HostState h={h} /></td>
                  <td><UsageBar value={h.cpu_usage_pct} /></td>
                  <td><UsageBar value={h.ram_usage_pct} /></td>
                  <td className="num text-right">{fmt(h.running_vms, 0)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </TableWrap>
      </Card>
    </>
  )
}

function HostState({ h }: { h: VropsHost }) {
  if (h.maintenance === 'inMaintenance') return <StatusBadge status="WARNING" label="Maintenance" />
  if (!h.connection) return <span className="text-muted">—</span>
  return h.connection === 'connected' ? <StatusBadge status="ONLINE" label="Connecté" /> : <StatusBadge status="OFFLINE" label="Déconnecté" />
}

// ── Optimisation ────────────────────────────────────────────────────────────
const KIND_LABEL: Record<VropsRightsizing['kind'], string> = { oversized: 'Surdimensionnée', undersized: 'Sous-dimensionnée', idle: 'Inactive' }

function Optimisation({ d }: { d: DeviceState }) {
  const m = d.metrics as Record<string, number | undefined>
  const [kind, setKind] = useState<'all' | VropsRightsizing['kind']>('all')
  const [search, setSearch] = useState('')
  const rows = (d.rightsizing ?? []).filter((r) => (kind === 'all' || r.kind === kind) && includes(r.vm_name, search))
  return (
    <>
      <div className="mb-4 grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <Stat icon={Cpu} label="vCPU récupérables" value={fmt(m.reclaimable_vcpus, 0)} sub={`${fmt(m.vms_oversized, 0)} VM(s) surdimensionnée(s)`} />
        <Stat icon={HardDrive} label="RAM récupérable" value={fmt(m.reclaimable_memory_gb, 0, 'Go')} sub="selon les recommandations vROps" />
        <Stat icon={CircleDashed} label="VMs inactives" value={fmt(m.vms_idle, 0)} sub="candidates à l’arrêt ou à la suppression" />
        <Stat icon={Camera} label="Snapshots" value={fmt(m.snapshots_gb, 0, 'Go')} sub={`${fmt(d.snapshots?.length, 0)} VM(s)`} />
      </div>
      <div className="grid grid-cols-1 gap-4 xl:grid-cols-3">
        <Card className="xl:col-span-2" title={`Redimensionnement recommandé (${rows.length})`} bodyClassName="p-0"
          actions={
            <div className="flex flex-wrap items-center gap-2">
              <Segmented label="Recommandation" value={kind} onChange={setKind} items={[
                { value: 'all', label: 'Toutes' }, { value: 'oversized', label: 'Surdim.' },
                { value: 'undersized', label: 'Sous-dim.' }, { value: 'idle', label: 'Inactives' },
              ]} />
              <SearchBox value={search} onChange={setSearch} label="Rechercher une VM" />
            </div>
          }>
          {rows.length ? (
            <TableWrap>
              <table className="table-base">
                <thead><tr><th>VM</th><th>Cluster</th><th>Recommandation</th><th className="text-right">vCPU à ajuster</th><th className="text-right">Mémoire à ajuster</th></tr></thead>
                <tbody>
                  {rows.map((r) => (
                    <tr key={`${r.vm_name}-${r.kind}`}>
                      <td className="font-mono text-2xs">{r.vm_name}</td>
                      <td className="text-2xs">{r.cluster ?? '—'}</td>
                      <td>{KIND_LABEL[r.kind]}</td>
                      <td className="num text-right">{r.vcpus !== undefined ? `${r.kind === 'oversized' ? '−' : '+'}${r.vcpus}` : '—'}</td>
                      <td className="num text-right">{r.memory_gb !== undefined ? `${r.kind === 'oversized' ? '−' : '+'}${fmt(r.memory_gb, 1, 'Go')}` : '—'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </TableWrap>
          ) : <EmptyState title="Aucune recommandation" />}
        </Card>
        <Card title="Plus gros snapshots" bodyClassName="p-0">
          {d.snapshots?.length ? (
            <TableWrap>
              <table className="table-base">
                <thead><tr><th>VM</th><th className="text-right">Taille</th></tr></thead>
                <tbody>
                  {d.snapshots.map((s) => (
                    <tr key={s.vm_name}>
                      <td><p className="font-mono text-2xs">{s.vm_name}</p><p className="text-2xs text-muted">{s.cluster ?? ''}</p></td>
                      <td className="num text-right">{fmt(s.snapshot_gb, 1, 'Go')}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </TableWrap>
          ) : <EmptyState icon={CheckCircle2} title="Aucun snapshot" />}
        </Card>
      </div>
    </>
  )
}

// ── Alertes ─────────────────────────────────────────────────────────────────
const LEVEL: Record<VropsAlert['level'], { status: string; label: string }> = {
  CRITICAL: { status: 'CRITICAL', label: 'Critique' },
  IMMEDIATE: { status: 'CRITICAL', label: 'Immédiate' },
  WARNING: { status: 'WARNING', label: 'Avertissement' },
  INFORMATION: { status: 'NO_DATA', label: 'Information' },
}
const IMPACT: Record<string, string> = { HEALTH: 'Santé', RISK: 'Risque', EFFICIENCY: 'Efficacité' }

function LevelBadge({ level }: { level: VropsAlert['level'] }) {
  return <StatusBadge status={LEVEL[level].status} label={LEVEL[level].label} />
}

function Alertes({ alerts }: { alerts: VropsAlert[] }) {
  const [level, setLevel] = useState<'all' | VropsAlert['level']>('all')
  const [impact, setImpact] = useState('')
  const [search, setSearch] = useState('')
  const rows = alerts.filter((a) => (level === 'all' || a.level === level) && (!impact || a.impact === impact)
    && (includes(a.name, search) || includes(a.object, search)))
  return (
    <Card bodyClassName="p-0" title={`Alertes vROps actives (${rows.length})`}
      actions={
        <div className="flex flex-wrap items-center gap-2">
          <Segmented label="Niveau" value={level} onChange={setLevel} items={[
            { value: 'all', label: 'Toutes' }, { value: 'CRITICAL', label: 'Critiques' }, { value: 'IMMEDIATE', label: 'Immédiates' },
            { value: 'WARNING', label: 'Avert.' }, { value: 'INFORMATION', label: 'Info' },
          ]} />
          <Select aria-label="Impact" className="!w-auto" value={impact} onChange={(e) => setImpact(e.target.value)}>
            <option value="">Tous les impacts</option>
            {Object.entries(IMPACT).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </Select>
          <SearchBox value={search} onChange={setSearch} label="Alerte ou objet" />
        </div>
      }>
      {rows.length ? (
        <TableWrap>
          <table className="table-base">
            <thead><tr><th className="w-36">Niveau</th><th>Alerte</th><th>Objet</th><th>Impact</th><th className="w-36">Depuis</th></tr></thead>
            <tbody>
              {rows.map((a, i) => (
                <tr key={i}>
                  <td><LevelBadge level={a.level} /></td>
                  <td>{a.name}</td>
                  <td><p className="font-mono text-2xs">{a.object ?? '—'}</p><p className="text-2xs text-muted">{a.object_kind ?? ''}</p></td>
                  <td className="text-2xs">{a.impact ? IMPACT[a.impact] ?? a.impact : '—'}</td>
                  <td className="text-2xs text-muted" title={dateTime(a.started_at)}>{relativeTime(a.started_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </TableWrap>
      ) : <EmptyState icon={CheckCircle2} title="Aucune alerte pour ces filtres" />}
    </Card>
  )
}

// ── Diagnostic de collecte ──────────────────────────────────────────────────
function Diagnostics({ d }: { d: DeviceState }) {
  const missing = d.vrops_missing_keys ?? []
  const notes = d.vrops_diagnostics ?? []
  if (!missing.length && !notes.length) return null
  return (
    <details className="mt-4 rounded-md border border-border bg-surface px-4 py-3 text-2xs text-muted">
      <summary className="cursor-pointer text-fg-2">Diagnostic de collecte ({missing.length + notes.length})</summary>
      {notes.map((n) => <p key={n} className="mt-2">{n}</p>)}
      {missing.length ? (
        <p className="mt-2">
          Métriques non fournies par ce vROps (champs laissés vides, aucune valeur inventée) :{' '}
          <span className="font-mono">{missing.join(', ')}</span>
        </p>
      ) : null}
    </details>
  )
}
