import { useMemo, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { AlertTriangle, ArrowLeft, Clock, OctagonAlert } from 'lucide-react'
import { api } from '@/lib/api'
import { CATEGORY_LABEL, METRIC_META, TYPE_LABEL, dateTime, isDemoHost, fmt, keyMetrics, metricLabel, metricValue, relativeTime } from '@/lib/format'
import type { DeviceState, SeriesPoint } from '@/lib/types'
import { TimeSeriesChart } from '@/components/charts'
import { Card, DemoBadge, EmptyState, ErrorState, FilterField, PageHeader, Segmented, Select, Spinner, StatusBadge, UsageBar } from '@/components/ui'

const RANGES = [
  { value: 6, label: '6 h' },
  { value: 24, label: '24 h' },
  { value: 168, label: '7 j' },
  { value: 720, label: '30 j' },
]

export default function DeviceDetail() {
  const { id = '' } = useParams()
  const [hours, setHours] = useState(24)
  const [field, setField] = useState<string | null>(null)

  const q = useQuery({
    queryKey: ['device', id],
    queryFn: () => api.get<DeviceState>(`/metrics/devices/${id}`),
    refetchInterval: 60_000,
  })
  const d = q.data
  const numericFields = useMemo(
    () => (d ? Object.entries(d.metrics).filter(([k, v]) => typeof v === 'number' && k in METRIC_META).map(([k]) => k) : []),
    [d],
  )
  const activeField = field ?? (d ? keyMetrics(d.category, d.device_type).find((f) => numericFields.includes(f)) ?? numericFields[0] : null)

  const hist = useQuery({
    queryKey: ['device-history', id, activeField, hours],
    queryFn: () => api.get<{ points: SeriesPoint[] }>(`/metrics/devices/${id}/history`, { field: activeField!, hours }),
    enabled: Boolean(activeField),
  })

  if (q.isLoading) return <Spinner />
  if (q.isError || !d) return <ErrorState error={q.error} onRetry={() => q.refetch()} />

  const unit = activeField ? METRIC_META[activeField]?.unit : undefined
  return (
    <>
      <Link to="/" className="mb-3 inline-flex items-center gap-1.5 text-sm text-muted hover:text-fg">
        <ArrowLeft className="size-4" aria-hidden /> Vue d’ensemble
      </Link>
      <PageHeader
        title={d.device_name}
        description={<>{TYPE_LABEL[d.device_type] ?? d.device_type} · {CATEGORY_LABEL[d.category] ?? d.category} · <span className="font-mono">{d.hostname_ip}</span></>}
        actions={
          <div className="flex items-center gap-3">
            <span className="flex items-center gap-1.5 text-2xs text-muted" title={dateTime(d.collected_at)}>
              <Clock className="size-3.5" aria-hidden />
              Collecté {relativeTime(d.collected_at)}{d.stale && d.collected_at ? ' (périmé)' : ''}
            </span>
            {isDemoHost(d.hostname_ip) ? <DemoBadge /> : null}
            <StatusBadge status={d.status} />
          </div>
        }
      />

      {d.alerts.length ? (
        <div className="mb-4 flex flex-col gap-2">
          {d.alerts.map((a, i) => (
            <div key={i} className="flex items-start gap-2 rounded-md border border-border bg-surface px-3 py-2 text-sm" role="alert">
              {a.severity === 'CRITICAL' ? <OctagonAlert className="mt-0.5 size-4 shrink-0 text-crit" aria-hidden /> : <AlertTriangle className="mt-0.5 size-4 shrink-0 text-warn" aria-hidden />}
              <span><span className="font-medium">{a.component ? `${a.component} — ` : ''}</span>{a.message}</span>
            </div>
          ))}
        </div>
      ) : null}

      {d.status === 'NO_DATA' ? (
        <Card><EmptyState title="Aucune donnée collectée">{d.last_sync_error ?? 'La première collecte n’a pas encore eu lieu.'}</EmptyState></Card>
      ) : (
        <>
          <div className="mb-4 grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
            {numericFields.map((f) => (
              <div key={f} className="card p-3">
                <p className="truncate text-2xs text-muted">{metricLabel(f)}</p>
                <p className="num mt-0.5 text-lg font-medium">{metricValue(f, d.metrics[f])}</p>
              </div>
            ))}
          </div>

          <Card
            className="mb-4"
            title="Historique"
            actions={<Segmented label="Période" value={hours} onChange={setHours} items={RANGES} />}
          >
            <div className="mb-3 max-w-xs">
              <FilterField label="Métrique">
                {(fid) => (
                  <Select id={fid} value={activeField ?? ''} onChange={(e) => setField(e.target.value)}>
                    {numericFields.map((f) => <option key={f} value={f}>{metricLabel(f)}</option>)}
                  </Select>
                )}
              </FilterField>
            </div>
            {hist.isLoading ? <Spinner /> : hist.data?.points.length ? (
              <TimeSeriesChart
                data={hist.data.points}
                series={[{ key: 'value', label: activeField ? metricLabel(activeField) : '' }]}
                unit={unit}
                spanHours={hours}
                yMax={unit === '%' ? 100 : undefined}
                thresholds={unit === '%' ? [{ value: 90, label: 'Critique 90 %', tone: 'crit' }, { value: 80, label: 'Attention 80 %', tone: 'warn' }] : []}
                ariaLabel={`Historique ${activeField ? metricLabel(activeField) : ''} sur ${hours} heures`}
              />
            ) : <EmptyState title="Pas d’historique sur cette période" />}
          </Card>
        </>
      )}

      {d.hosts_metrics?.length ? (
        <Card className="mb-4" title={`Hôtes ESXi (${d.hosts_metrics.length})`} bodyClassName="p-0">
          <div className="max-h-[420px] overflow-auto">
            <table className="table-base">
              <thead><tr><th>Hôte</th><th>Cluster</th><th>État</th><th className="w-48">CPU</th><th className="w-48">RAM</th></tr></thead>
              <tbody>
                {d.hosts_metrics.map((h) => (
                  <tr key={h.host_name}>
                    <td className="font-mono text-2xs">{h.host_name}</td>
                    <td>{h.cluster}</td>
                    <td><StatusBadge status={h.status === 'CONNECTED' ? 'ONLINE' : h.status === 'MAINTENANCE' ? 'WARNING' : 'OFFLINE'}
                      label={h.status === 'CONNECTED' ? 'Connecté' : h.status === 'MAINTENANCE' ? 'Maintenance' : 'Déconnecté'} /></td>
                    <td><UsageBar value={h.cpu_usage_pct} /></td>
                    <td><UsageBar value={h.ram_usage_pct} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      ) : null}

      {d.datastores_metrics?.length ? (
        <Card title={`Datastores (${d.datastores_metrics.length})`} bodyClassName="p-0">
          <div className="max-h-[420px] overflow-auto">
            <table className="table-base">
              <thead><tr><th>Datastore</th><th className="text-right">Capacité</th><th className="text-right">Utilisé</th><th className="w-56">Utilisation</th><th className="text-right">Latence lecture</th><th className="text-right">Latence écriture</th></tr></thead>
              <tbody>
                {[...d.datastores_metrics].sort((a, b) => b.usage_pct - a.usage_pct).map((ds) => (
                  <tr key={ds.datastore_name}>
                    <td className="font-mono text-2xs">{ds.datastore_name}</td>
                    <td className="num text-right">{fmt(ds.capacity_tb, 2, 'To')}</td>
                    <td className="num text-right">{fmt(ds.used_tb, 2, 'To')}</td>
                    <td><UsageBar value={ds.usage_pct} /></td>
                    <td className="num text-right">{fmt(ds.read_latency_ms, 1, 'ms')}</td>
                    <td className="num text-right">{fmt(ds.write_latency_ms, 1, 'ms')}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      ) : null}
    </>
  )
}
