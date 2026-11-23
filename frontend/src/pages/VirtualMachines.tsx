import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Activity, Search } from 'lucide-react'
import { api } from '@/lib/api'
import { fmt } from '@/lib/format'
import type { FilesystemRow, SaturationRow, SeriesPoint, VmLatency } from '@/lib/types'
import { TimeSeriesChart } from '@/components/charts'
import {
  Card, EmptyState, ErrorState, FilterField, Input, Modal, PageHeader, Segmented, Select, Spinner, Tabs, UsageBar,
} from '@/components/ui'

type Tab = 'saturation' | 'filesystems' | 'latency'

function useFilters() {
  const [vcenter, setVcenter] = useState('')
  const [cluster, setCluster] = useState('')
  const vcenters = useQuery({ queryKey: ['vm-filters', 'vcenters'], queryFn: () => api.get<string[]>('/reports/vms/filters/vcenters') })
  const clusters = useQuery({
    queryKey: ['vm-filters', 'clusters', vcenter],
    queryFn: () => api.get<string[]>('/reports/vms/filters/clusters', { vcenter }),
  })
  return { vcenter, setVcenter: (v: string) => { setVcenter(v); setCluster('') }, cluster, setCluster, vcenters: vcenters.data ?? [], clusters: clusters.data ?? [] }
}

function ScopeFilters({ f }: { f: ReturnType<typeof useFilters> }) {
  return (
    <>
      <FilterField label="vCenter">
        {(id) => (
          <Select id={id} value={f.vcenter} onChange={(e) => f.setVcenter(e.target.value)}>
            <option value="">Tous</option>
            {f.vcenters.map((v) => <option key={v} value={v}>{v}</option>)}
          </Select>
        )}
      </FilterField>
      <FilterField label="Cluster">
        {(id) => (
          <Select id={id} value={f.cluster} onChange={(e) => f.setCluster(e.target.value)}>
            <option value="">Tous</option>
            {f.clusters.map((v) => <option key={v} value={v}>{v}</option>)}
          </Select>
        )}
      </FilterField>
    </>
  )
}

const PERIODS = [
  { value: 6, label: '6 h' },
  { value: 24, label: '24 h' },
  { value: 72, label: '3 j' },
  { value: 168, label: '7 j' },
]

function SaturationTab() {
  const f = useFilters()
  const [hours, setHours] = useState(24)
  const [threshold, setThreshold] = useState(90)
  const [duration, setDuration] = useState(15)
  const [selected, setSelected] = useState<SaturationRow | null>(null)

  const range = useMemo(() => {
    const end = new Date()
    return { start_date: new Date(end.getTime() - hours * 3600_000).toISOString(), end_date: end.toISOString() }
  }, [hours])

  const q = useQuery({
    queryKey: ['saturation', f.vcenter, f.cluster, hours, threshold, duration],
    queryFn: () => api.get<SaturationRow[]>('/reports/vms/saturation', { ...range, vcenter: f.vcenter, cluster: f.cluster, threshold, duration_minutes: duration }),
  })
  const hist = useQuery({
    queryKey: ['vm-history', selected?.vm_name, selected?.resource, hours],
    queryFn: () => api.get<SeriesPoint[]>(`/reports/vms/saturation/${encodeURIComponent(selected!.vm_name)}/history`, { resource: selected!.resource, ...range }),
    enabled: Boolean(selected),
  })

  return (
    <>
      <div className="mb-4 flex flex-wrap items-end gap-3">
        <ScopeFilters f={f} />
        <FilterField label="Seuil (%)">
          {(id) => <Input id={id} type="number" min={50} max={100} value={threshold} onChange={(e) => setThreshold(Number(e.target.value) || 90)} className="w-24" />}
        </FilterField>
        <FilterField label="Durée minimale (min)">
          {(id) => <Input id={id} type="number" min={1} max={1440} value={duration} onChange={(e) => setDuration(Number(e.target.value) || 15)} className="w-28" />}
        </FilterField>
        <div className="flex flex-col gap-1">
          <span className="text-2xs font-medium text-muted">Période</span>
          <Segmented label="Période" value={hours} onChange={setHours} items={PERIODS} />
        </div>
      </div>
      <Card bodyClassName="p-0">
        {q.isLoading ? <Spinner /> : q.isError ? <ErrorState error={q.error} onRetry={() => q.refetch()} /> : !q.data?.length ? (
          <EmptyState icon={Activity} title="Aucune saturation détectée">
            Aucune VM n’est restée au-dessus de {threshold} % pendant {duration} minutes ou plus sur la période.
          </EmptyState>
        ) : (
          <div className="overflow-x-auto">
            <table className="table-base">
              <thead><tr><th>VM</th><th>vCenter / cluster</th><th>Ressource</th><th className="text-right">Pic</th><th className="text-right">Durée continue</th><th>OS</th><th>IP</th></tr></thead>
              <tbody>
                {q.data.map((r) => (
                  <tr key={`${r.vm_name}-${r.resource}`} className="cursor-pointer" onClick={() => setSelected(r)}>
                    <td><button type="button" className="font-mono text-2xs font-medium hover:text-accent" onClick={() => setSelected(r)}>{r.vm_name}</button></td>
                    <td className="text-2xs text-fg-2">{r.vcenter} / {r.cluster}</td>
                    <td>{r.resource}</td>
                    <td className="num text-right font-medium">{fmt(r.max_value, 1, '%')}</td>
                    <td className="num text-right">{r.duration_minutes >= 60 ? `${Math.floor(r.duration_minutes / 60)} h ${r.duration_minutes % 60} min` : `${r.duration_minutes} min`}</td>
                    <td className="text-2xs">{r.os ?? <span className="text-muted">Inconnu</span>}</td>
                    <td className="font-mono text-2xs">{r.ip ?? <span className="text-muted">—</span>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
      <Modal open={Boolean(selected)} onClose={() => setSelected(null)} title={selected ? `${selected.vm_name} — ${selected.resource}` : ''} wide>
        {hist.isLoading ? <Spinner /> : hist.data?.length ? (
          <TimeSeriesChart
            data={hist.data}
            series={[{ key: 'value', label: selected?.resource ?? '' }]}
            unit="%"
            yMax={100}
            spanHours={hours}
            thresholds={[{ value: threshold, label: `Seuil ${threshold} %`, tone: 'crit' }]}
            ariaLabel={`Charge ${selected?.resource} de ${selected?.vm_name}`}
          />
        ) : <EmptyState title="Pas de données sur la période" />}
      </Modal>
    </>
  )
}

function FilesystemsTab() {
  const f = useFilters()
  const [search, setSearch] = useState('')
  const [minUsage, setMinUsage] = useState(80)
  const q = useQuery({
    queryKey: ['filesystems', f.vcenter, f.cluster],
    queryFn: () => api.get<FilesystemRow[]>('/reports/vms/filesystems', { vcenter: f.vcenter, cluster: f.cluster }),
  })
  const rows = (q.data ?? []).filter((r) => (r.usage_pct ?? 0) >= minUsage && (!search || `${r.vm_name} ${r.mount_point}`.toLowerCase().includes(search.toLowerCase())))
  return (
    <>
      <div className="mb-4 flex flex-wrap items-end gap-3">
        <ScopeFilters f={f} />
        <FilterField label="Utilisation minimale">
          {(id) => (
            <Select id={id} value={minUsage} onChange={(e) => setMinUsage(Number(e.target.value))}>
              <option value={0}>Toutes</option><option value={70}>≥ 70 %</option><option value={80}>≥ 80 %</option><option value={90}>≥ 90 %</option>
            </Select>
          )}
        </FilterField>
        <FilterField label="Recherche">
          {(id) => (
            <div className="relative">
              <Search className="pointer-events-none absolute left-2.5 top-2.5 size-4 text-muted" aria-hidden />
              <Input id={id} className="w-56 pl-8" placeholder="VM ou point de montage" value={search} onChange={(e) => setSearch(e.target.value)} />
            </div>
          )}
        </FilterField>
      </div>
      <Card bodyClassName="p-0">
        {q.isLoading ? <Spinner /> : q.isError ? <ErrorState error={q.error} onRetry={() => q.refetch()} /> : !rows.length ? (
          <EmptyState title="Aucun système de fichiers ne correspond">Les données proviennent des VMware Tools ; une VM sans Tools n’apparaît pas.</EmptyState>
        ) : (
          <div className="max-h-[70vh] overflow-auto">
            <table className="table-base">
              <thead><tr><th>VM</th><th>Point de montage</th><th>Cluster</th><th className="text-right">Taille</th><th className="text-right">Libre</th><th className="w-56">Utilisation</th></tr></thead>
              <tbody>
                {rows.slice(0, 500).map((r) => (
                  <tr key={r.id}>
                    <td className="font-mono text-2xs">{r.vm_name}</td>
                    <td className="font-mono text-2xs">{r.mount_point}</td>
                    <td className="text-2xs text-fg-2">{r.cluster}</td>
                    <td className="num text-right">{fmt(r.capacity_gb, 1, 'Go')}</td>
                    <td className="num text-right">{fmt(r.free_gb, 1, 'Go')}</td>
                    <td><UsageBar value={r.usage_pct} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
            {rows.length > 500 ? <p className="p-3 text-center text-2xs text-muted">500 premiers résultats sur {rows.length} — affinez les filtres.</p> : null}
          </div>
        )}
      </Card>
    </>
  )
}

function LatencyTab() {
  const [hours, setHours] = useState(6)
  const [selected, setSelected] = useState<string | null>(null)
  const q = useQuery({ queryKey: ['vm-latency', hours], queryFn: () => api.get<VmLatency[]>('/reports/vms/latency', { hours, min_peak_ms: 10 }) })
  const vm = q.data?.find((v) => v.vm_name === selected) ?? q.data?.[0]
  return (
    <>
      <div className="mb-4 flex items-end gap-3">
        <div className="flex flex-col gap-1">
          <span className="text-2xs font-medium text-muted">Période</span>
          <Segmented label="Période" value={hours} onChange={setHours} items={[{ value: 6, label: '6 h' }, { value: 24, label: '24 h' }, { value: 72, label: '3 j' }]} />
        </div>
      </div>
      {q.isLoading ? <Spinner /> : q.isError ? <ErrorState error={q.error} onRetry={() => q.refetch()} /> : !q.data?.length ? (
        <Card><EmptyState icon={Activity} title="Aucune VM au-dessus de 10 ms">Seules les latences réellement mesurées par le collecteur sont affichées.</EmptyState></Card>
      ) : (
        <div className="grid gap-4 lg:grid-cols-[18rem_1fr]">
          <Card title={`VMs avec pic > 10 ms (${q.data.length})`} bodyClassName="p-0">
            <ul className="max-h-[60vh] overflow-y-auto">
              {q.data.map((v) => (
                <li key={v.vm_name}>
                  <button type="button" onClick={() => setSelected(v.vm_name)}
                    className={`flex w-full cursor-pointer items-center justify-between gap-2 border-b border-border/60 px-3 py-2 text-left text-2xs hover:bg-surface-2 ${vm?.vm_name === v.vm_name ? 'bg-surface-2' : ''}`}
                    aria-pressed={vm?.vm_name === v.vm_name}>
                    <span className="truncate font-mono">{v.vm_name}</span>
                    <span className="num shrink-0 font-medium">{fmt(v.peak_ms, 1, 'ms')}</span>
                  </button>
                </li>
              ))}
            </ul>
          </Card>
          <Card title={vm ? `Latence disque — ${vm.vm_name}` : ''}>
            {vm ? (
              <TimeSeriesChart
                data={vm.history}
                series={[{ key: 'read_latency_ms', label: 'Lecture' }, { key: 'write_latency_ms', label: 'Écriture' }]}
                unit="ms"
                spanHours={hours}
                height={320}
                thresholds={[{ value: 20, label: 'Seuil 20 ms', tone: 'crit' }]}
                ariaLabel={`Latence lecture et écriture de ${vm.vm_name}`}
              />
            ) : null}
          </Card>
        </div>
      )}
    </>
  )
}

export default function VirtualMachines() {
  const [tab, setTab] = useState<Tab>('saturation')
  return (
    <>
      <PageHeader title="Machines virtuelles" description="Diagnostic des VMs collectées sur les vCenter : saturation CPU/RAM, systèmes de fichiers invités, latence disque." />
      <Tabs<Tab> value={tab} onChange={setTab} items={[
        { value: 'saturation', label: 'Saturation CPU / RAM' },
        { value: 'filesystems', label: 'Systèmes de fichiers' },
        { value: 'latency', label: 'Latence disque' },
      ]} />
      {tab === 'saturation' ? <SaturationTab /> : tab === 'filesystems' ? <FilesystemsTab /> : <LatencyTab />}
    </>
  )
}
