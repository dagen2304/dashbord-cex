import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Download, FileSpreadsheet, Gauge, TrendingUp } from 'lucide-react'
import { api, download } from '@/lib/api'
import { useAuth } from '@/lib/auth'
import { TYPE_LABEL, fmt, signed } from '@/lib/format'
import type { CapacityRow } from '@/lib/types'
import { TimeSeriesChart } from '@/components/charts'
import {
  Button, Card, EmptyState, ErrorState, InlineError, Modal, PageHeader, Segmented, SeverityBadge, Spinner, Stat, UsageBar,
} from '@/components/ui'

type Filter = 'ALL' | 'CRITICAL' | 'WARNING'

function unitLabel(u: string) {
  return u === 'TB' ? 'To' : u === 'GB' ? 'Go' : u
}

function HistoryModal({ row, onClose }: { row: CapacityRow | null; onClose: () => void }) {
  const [days, setDays] = useState(30)
  const q = useQuery({
    queryKey: ['capacity-history', row?.device_id, row?.resource_name, days],
    queryFn: () => api.get<Array<{ timestamp: string; used: number; total: number; usage_pct: number }>>(
      `/reports/capacity/${row!.device_id}/${encodeURIComponent(row!.resource_name)}/history`, { days }),
    enabled: Boolean(row),
  })
  const u = row ? unitLabel(row.unit) : ''
  return (
    <Modal open={Boolean(row)} onClose={onClose} title={row ? `${row.device_name} — ${row.resource_name}` : ''} wide>
      <div className="mb-3 flex justify-end">
        <Segmented label="Période" value={days} onChange={setDays} items={[{ value: 7, label: '7 j' }, { value: 30, label: '30 j' }, { value: 90, label: '90 j' }, { value: 365, label: '1 an' }]} />
      </div>
      {q.isLoading ? <Spinner /> : q.data?.length ? (
        <TimeSeriesChart
          data={q.data}
          series={[{ key: row?.unit === '%' ? 'usage_pct' : 'used', label: row?.unit === '%' ? 'Utilisation' : 'Utilisé' }]}
          unit={row?.unit === '%' ? '%' : u}
          digits={2}
          spanHours={days * 24}
          yMax={row?.unit === '%' ? 100 : row?.capacity_total_tb ?? undefined}
          thresholds={row?.unit !== '%' && row?.capacity_total_tb ? [{ value: row.capacity_total_tb, label: `Capacité ${fmt(row.capacity_total_tb, 1, u)}`, tone: 'crit' }] : []}
          ariaLabel={`Évolution de ${row?.resource_name} sur ${days} jours`}
        />
      ) : <EmptyState title="Pas d’historique sur la période" />}
    </Modal>
  )
}

export default function Capacity() {
  const { can } = useAuth()
  const [filter, setFilter] = useState<Filter>('ALL')
  const [selected, setSelected] = useState<CapacityRow | null>(null)
  const [exportError, setExportError] = useState<string | null>(null)
  const [exporting, setExporting] = useState<'csv' | 'excel' | null>(null)
  const q = useQuery({ queryKey: ['capacity'], queryFn: () => api.get<CapacityRow[]>('/reports/capacity/data') })

  const rows = (q.data ?? []).filter((r) => filter === 'ALL' || r.severity === filter)
  const crit = q.data?.filter((r) => r.severity === 'CRITICAL').length ?? 0
  const warn = q.data?.filter((r) => r.severity === 'WARNING').length ?? 0
  const soonest = q.data?.filter((r) => r.days_to_full !== null).sort((a, b) => a.days_to_full! - b.days_to_full!)[0]

  async function doExport(kind: 'csv' | 'excel') {
    setExportError(null)
    setExporting(kind)
    try {
      await download(`/reports/capacity/${kind}`)
    } catch (e) {
      setExportError(e instanceof Error ? e.message : 'Export impossible.')
    } finally {
      setExporting(null)
    }
  }

  return (
    <>
      <PageHeader
        title="Analyse capacitaire"
        description="Tendances sur 60 jours (un point par heure) et projection de saturation par régression linéaire sur 30 jours."
        actions={can('operator') ? <>
          <Button icon={Download} loading={exporting === 'csv'} onClick={() => doExport('csv')}>CSV</Button>
          <Button icon={FileSpreadsheet} loading={exporting === 'excel'} onClick={() => doExport('excel')}>Excel</Button>
        </> : null}
      />
      <div className="mb-3"><InlineError message={exportError} /></div>

      {q.isLoading ? <Spinner /> : q.isError ? <ErrorState error={q.error} onRetry={() => q.refetch()} /> : !q.data?.length ? (
        <Card><EmptyState icon={Gauge} title="Pas encore d’historique capacitaire">Un point est enregistré chaque heure par équipement ; la projection apparaît après 3 jours de données.</EmptyState></Card>
      ) : (
        <>
          <div className="mb-5 grid grid-cols-1 gap-3 sm:grid-cols-3">
            <Stat label="Ressources critiques" value={crit} sub="saturation < 90 j ou usage ≥ 90 %" tone="crit" icon={Gauge} />
            <Stat label="À surveiller" value={warn} sub="saturation < 180 j ou usage ≥ 80 %" tone="warn" icon={Gauge} />
            <Stat label="Saturation la plus proche" icon={TrendingUp}
              value={soonest ? `${soonest.days_to_full} j` : '—'}
              sub={soonest ? `${soonest.device_name} · ${soonest.resource_name}` : 'aucune tendance à la hausse'} />
          </div>

          <div className="mb-3">
            <Segmented<Filter> label="Filtrer par criticité" value={filter} onChange={setFilter}
              items={[{ value: 'ALL', label: `Toutes (${q.data.length})` }, { value: 'CRITICAL', label: `Critiques (${crit})` }, { value: 'WARNING', label: `Attention (${warn})` }]} />
          </div>

          <Card bodyClassName="p-0">
            <div className="overflow-x-auto">
              <table className="table-base">
                <thead>
                  <tr>
                    <th>Équipement</th><th>Ressource</th><th className="text-right">Utilisé / capacité</th><th className="w-48">Utilisation</th>
                    <th className="text-right">Semaine</th><th className="text-right">Mois</th><th className="text-right">Saturation</th><th>Criticité</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((r) => {
                    const u = unitLabel(r.unit)
                    return (
                      <tr key={`${r.device_id}-${r.resource_name}`} className="cursor-pointer" onClick={() => setSelected(r)}>
                        <td>
                          <button type="button" className="text-left font-medium hover:text-accent" onClick={() => setSelected(r)}>{r.device_name}</button>
                          <p className="text-2xs text-muted">{TYPE_LABEL[r.device_type] ?? r.device_type}{r.is_archived ? ' · archivé' : ''}</p>
                        </td>
                        <td>{r.resource_name}</td>
                        <td className="num whitespace-nowrap text-right">{r.unit === '%' ? '—' : `${fmt(r.capacity_used_tb, 1)} / ${fmt(r.capacity_total_tb, 1, u)}`}</td>
                        <td><UsageBar value={r.usage_pct} /></td>
                        <td className="num whitespace-nowrap text-right">{r.unit === '%' ? '—' : signed(r.w2w_diff_tb, 2, u)}</td>
                        <td className="num whitespace-nowrap text-right">{r.unit === '%' ? '—' : signed(r.mom_diff_tb, 2, u)}</td>
                        <td className="num whitespace-nowrap text-right" title={r.growth_per_day_tb ? `${signed(r.growth_per_day_tb, 3, u)} / jour` : undefined}>
                          {r.days_to_full !== null ? `${r.days_to_full} j` : <span className="text-muted">{r.points < 5 ? 'Données insuffisantes' : 'Stable'}</span>}
                        </td>
                        <td><SeverityBadge severity={r.severity} /></td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          </Card>
        </>
      )}
      <HistoryModal row={selected} onClose={() => setSelected(null)} />
    </>
  )
}
