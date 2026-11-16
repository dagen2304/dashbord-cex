import { useState } from 'react'
import { Link } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { BellOff, Check } from 'lucide-react'
import { api } from '@/lib/api'
import { useAuth } from '@/lib/auth'
import { TYPE_LABEL, dateTime, fmt, relativeTime } from '@/lib/format'
import type { ActiveAlert, LatencyAnomaly } from '@/lib/types'
import { Button, Card, EmptyState, ErrorState, InlineError, PageHeader, SeverityBadge, Spinner, Tabs } from '@/components/ui'

type Tab = 'active' | 'latency'

export default function Alerts() {
  const { can } = useAuth()
  const qc = useQueryClient()
  const [tab, setTab] = useState<Tab>('active')
  const [showResolved, setShowResolved] = useState(false)

  const active = useQuery({ queryKey: ['alerts', 'active'], queryFn: () => api.get<ActiveAlert[]>('/alerts/active'), refetchInterval: 60_000 })
  const latency = useQuery({
    queryKey: ['alerts', 'latency', showResolved],
    queryFn: () => api.get<LatencyAnomaly[]>('/alerts/latency', { include_resolved: showResolved }),
  })
  const resolve = useMutation({
    mutationFn: (id: number) => api.post(`/alerts/latency/${id}/resolve`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['alerts', 'latency'] }),
  })

  const openLatency = latency.data?.filter((a) => !a.is_resolved).length

  return (
    <>
      <PageHeader title="Alertes" description="Alertes remontées par la dernière collecte de chaque équipement, et dépassements de latence historisés." />
      <Tabs<Tab>
        value={tab}
        onChange={setTab}
        items={[
          { value: 'active', label: 'En cours', count: active.data?.length },
          { value: 'latency', label: 'Latence datastores', count: openLatency },
        ]}
      />

      {tab === 'active' ? (
        <Card bodyClassName="p-0">
          {active.isLoading ? <Spinner /> : active.isError ? <ErrorState error={active.error} onRetry={() => active.refetch()} /> :
            !active.data?.length ? <EmptyState icon={BellOff} title="Aucune alerte en cours">Tous les équipements collectés sont dans les seuils.</EmptyState> : (
              <div className="overflow-x-auto">
                <table className="table-base">
                  <thead><tr><th className="w-32">Sévérité</th><th>Équipement</th><th>Composant</th><th>Message</th><th className="w-36">Collecte</th></tr></thead>
                  <tbody>
                    {active.data.map((a, i) => (
                      <tr key={`${a.device_id}-${i}`}>
                        <td><SeverityBadge severity={a.severity} /></td>
                        <td>
                          <Link to={`/devices/${a.device_id}`} className="font-medium hover:text-accent">{a.device_name}</Link>
                          <p className="text-2xs text-muted">{TYPE_LABEL[a.device_type] ?? a.device_type}</p>
                        </td>
                        <td className="text-fg-2">{a.component ?? '—'}</td>
                        <td>{a.message}</td>
                        <td className="text-2xs text-muted" title={dateTime(a.collected_at)}>{relativeTime(a.collected_at)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
        </Card>
      ) : (
        <Card
          bodyClassName="p-0"
          title="Dépassements du seuil de latence"
          actions={
            <label className="flex cursor-pointer items-center gap-2 text-sm text-fg-2">
              <input type="checkbox" className="size-4 accent-[rgb(var(--accent))]" checked={showResolved} onChange={(e) => setShowResolved(e.target.checked)} />
              Afficher les alertes traitées
            </label>
          }
        >
          {resolve.isError ? <div className="p-3"><InlineError message={(resolve.error as Error).message} /></div> : null}
          {latency.isLoading ? <Spinner /> : latency.isError ? <ErrorState error={latency.error} onRetry={() => latency.refetch()} /> :
            !latency.data?.length ? <EmptyState icon={BellOff} title="Aucun dépassement">La latence n’est mesurée que lorsque le collecteur la remonte réellement.</EmptyState> : (
              <div className="overflow-x-auto">
                <table className="table-base">
                  <thead><tr><th>Date</th><th>Équipement</th><th>Datastore</th><th className="text-right">Latence</th><th className="text-right">Seuil</th><th>État</th><th /></tr></thead>
                  <tbody>
                    {latency.data.map((a) => (
                      <tr key={a.id}>
                        <td className="whitespace-nowrap text-2xs">{dateTime(a.timestamp)}</td>
                        <td>{a.device_name ?? '—'}</td>
                        <td className="font-mono text-2xs">{a.target_name}</td>
                        <td className="num text-right font-medium">{fmt(a.latency_ms, 1, 'ms')}</td>
                        <td className="num text-right text-muted">{fmt(a.threshold_ms, 0, 'ms')}</td>
                        <td>{a.is_resolved ? <span className="text-2xs text-muted">Traitée</span> : <SeverityBadge severity="WARNING" />}</td>
                        <td className="text-right">
                          {!a.is_resolved && can('operator') ? (
                            <Button size="sm" icon={Check} loading={resolve.isPending && resolve.variables === a.id} onClick={() => resolve.mutate(a.id)}>
                              Marquer traitée
                            </Button>
                          ) : null}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
        </Card>
      )}
    </>
  )
}
