import { useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import clsx from 'clsx'
import { AlertTriangle, CheckCircle2, CircleDashed, Clock, HardDrive, OctagonAlert, Search } from 'lucide-react'
import { api } from '@/lib/api'
import { useAuth } from '@/lib/auth'
import { CATEGORY_LABEL, TYPE_LABEL, isDemoHost, keyMetrics, metricLabel, metricValue, relativeTime } from '@/lib/format'
import type { DeviceState } from '@/lib/types'
import { Sparkline } from '@/components/charts'
import { DemoBadge, EmptyState, ErrorState, Input, PageHeader, Segmented, Spinner, Stat, StatusBadge, statusTone } from '@/components/ui'

const CATEGORIES = ['VIRTUALIZATION', 'STORAGE', 'BACKUP', 'OBJECT'] as const
type Filter = 'ALL' | (typeof CATEGORIES)[number]
const TONE_ORDER = { crit: 0, warn: 1, nodata: 2, ok: 3 } as const

function DeviceCard({ d }: { d: DeviceState }) {
  const fields = keyMetrics(d.category, d.device_type)
  const tone = statusTone(d.status)
  const firstAlert = d.alerts.find((a) => a.severity === 'CRITICAL') ?? d.alerts[0]
  return (
    <Link
      to={d.device_type === 'VROPS' ? `/vrealize?source=${d.device_id}` : `/devices/${d.device_id}`}
      className={clsx(
        'card group flex flex-col gap-3 p-4 transition-colors hover:border-muted',
        tone === 'crit' && 'border-l-[3px] border-l-crit',
        tone === 'warn' && 'border-l-[3px] border-l-warn',
      )}
    >
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <h3 className="flex items-center gap-2 text-sm font-semibold group-hover:text-accent">
            <span className="truncate">{d.device_name}</span>{isDemoHost(d.hostname_ip) ? <DemoBadge /> : null}
          </h3>
          <p className="truncate text-2xs text-muted">{TYPE_LABEL[d.device_type] ?? d.device_type} · <span className="font-mono">{d.hostname_ip}</span></p>
        </div>
        <StatusBadge status={d.last_sync_status === 'AUTH_FAILED' ? 'AUTH_FAILED' : d.status} />
      </div>

      {d.status === 'NO_DATA' || Object.keys(d.metrics).length === 0 ? (
        <p className="line-clamp-3 min-h-[3.75rem] text-2xs text-fg-2">
          {d.last_sync_status === 'UNSYNCED' ? 'Première collecte en attente.' : d.last_sync_error ?? firstAlert?.message ?? 'Aucune donnée collectée.'}
        </p>
      ) : (
        <dl className="grid grid-cols-3 gap-2">
          {fields.map((f) => (
            <div key={f} className="min-w-0">
              <dt className="truncate text-2xs text-muted">{metricLabel(f)}</dt>
              <dd className="num truncate text-base font-medium">{metricValue(f, d.metrics[f])}</dd>
            </div>
          ))}
        </dl>
      )}

      {d.sparkline && d.status !== 'NO_DATA' && Object.keys(d.metrics).length > 0 ? (
        <div>
          <Sparkline points={d.sparkline.points} label={metricLabel(d.sparkline.field)} />
          <p className="text-2xs text-muted">{metricLabel(d.sparkline.field)} — 6 h</p>
        </div>
      ) : null}

      <div className="mt-auto flex items-center justify-between gap-2 border-t border-border/60 pt-2 text-2xs">
        {firstAlert ? (
          <span className="flex min-w-0 items-center gap-1.5 text-fg-2" title={firstAlert.message}>
            {firstAlert.severity === 'CRITICAL'
              ? <OctagonAlert className="size-3.5 shrink-0 text-crit" aria-hidden />
              : <AlertTriangle className="size-3.5 shrink-0 text-warn" aria-hidden />}
            <span className="truncate">{firstAlert.message}</span>
          </span>
        ) : <span className="text-muted">Aucune alerte</span>}
        <span className={clsx('flex shrink-0 items-center gap-1', d.stale && d.collected_at ? 'font-medium text-warn' : 'text-muted')}
          title={d.stale && d.collected_at ? 'Donnée périmée : la dernière collecte date de plus de 3 intervalles' : undefined}>
          <Clock className="size-3" aria-hidden />
          {d.stale && d.collected_at ? 'Périmé · ' : ''}{relativeTime(d.collected_at)}
        </span>
      </div>
    </Link>
  )
}

export default function Overview() {
  const { can } = useAuth()
  const [filter, setFilter] = useState<Filter>('ALL')
  const [search, setSearch] = useState('')
  const q = useQuery({
    queryKey: ['realtime'],
    queryFn: () => api.get<DeviceState[]>('/metrics/realtime'),
    refetchInterval: 60_000,
  })

  const devices = useMemo(() => q.data ?? [], [q.data])
  const counts = useMemo(() => {
    const c = { ok: 0, warn: 0, crit: 0, nodata: 0, stale: 0 }
    for (const d of devices) {
      c[statusTone(d.status)] += 1
      if (d.stale && d.status !== 'NO_DATA') c.stale += 1
    }
    return c
  }, [devices])

  const visible = devices.filter((d) =>
    (filter === 'ALL' || d.category === filter) &&
    (!search || `${d.device_name} ${d.hostname_ip}`.toLowerCase().includes(search.toLowerCase())))

  return (
    <>
      <PageHeader
        title="Vue d’ensemble"
        description={q.dataUpdatedAt ? `État de ${devices.length} équipement(s) — actualisé ${relativeTime(new Date(q.dataUpdatedAt).toISOString())}` : 'État des équipements supervisés'}
      />

      {q.isLoading ? <Spinner /> : q.isError ? <ErrorState error={q.error} onRetry={() => q.refetch()} /> : devices.length === 0 ? (
        <div className="card">
          <EmptyState icon={HardDrive} title="Aucun équipement supervisé">
            {can('admin')
              ? <>Ajoutez un vCenter ou une baie depuis <Link to="/inventory" className="text-accent underline">l’inventaire</Link>.</>
              : 'Un administrateur doit d’abord ajouter des équipements à l’inventaire.'}
          </EmptyState>
        </div>
      ) : (
        <>
          <div className="mb-5 grid grid-cols-2 gap-3 xl:grid-cols-4" role="status" aria-live="polite" aria-atomic="true">
            <Stat label="Sains" value={counts.ok} sub={`sur ${devices.length} équipements`} icon={CheckCircle2} tone="ok" />
            <Stat label="Attention" value={counts.warn} sub="seuil dépassé" icon={AlertTriangle} tone="warn" />
            <Stat label="Critiques / injoignables" value={counts.crit} sub="action requise" icon={OctagonAlert} tone="crit" />
            <Stat label="Sans données" value={counts.nodata} sub={counts.stale ? `+ ${counts.stale} avec données périmées` : 'collecte en attente ou en échec'} icon={CircleDashed} tone="nodata" />
          </div>

          <div className="mb-4 flex flex-wrap items-center gap-3">
            <Segmented<Filter>
              label="Filtrer par catégorie"
              value={filter}
              onChange={setFilter}
              items={[{ value: 'ALL', label: 'Tous' }, ...CATEGORIES.filter((c) => devices.some((d) => d.category === c)).map((c) => ({ value: c, label: CATEGORY_LABEL[c] }))]}
            />
            <div className="relative ml-auto w-full sm:w-64">
              <Search className="pointer-events-none absolute left-2.5 top-2.5 size-4 text-muted" aria-hidden />
              <Input aria-label="Rechercher un équipement" placeholder="Rechercher…" className="pl-8" value={search} onChange={(e) => setSearch(e.target.value)} />
            </div>
          </div>

          {CATEGORIES.map((cat) => {
            // Les équipements en défaut d'abord, puis par nom.
            const list = visible.filter((d) => d.category === cat).sort((a, b) =>
              TONE_ORDER[statusTone(a.status)] - TONE_ORDER[statusTone(b.status)] || a.device_name.localeCompare(b.device_name, 'fr'))
            if (!list.length) return null
            return (
              <section key={cat} className="mb-6" aria-labelledby={`cat-${cat}`}>
                <h2 id={`cat-${cat}`} className="mb-2.5 text-2xs font-medium uppercase tracking-wider text-muted">
                  {CATEGORY_LABEL[cat]} <span className="num">({list.length})</span>
                </h2>
                <div className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-4">
                  {list.map((d) => <DeviceCard key={d.device_id} d={d} />)}
                </div>
              </section>
            )
          })}
          {visible.length === 0 ? <p className="py-6 text-center text-sm text-muted">Aucun équipement ne correspond au filtre.</p> : null}
        </>
      )}
    </>
  )
}
