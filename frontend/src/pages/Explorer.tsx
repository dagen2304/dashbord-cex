import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { ChartLine } from 'lucide-react'
import { api } from '@/lib/api'
import { metricLabel } from '@/lib/format'
import type { SeriesPoint } from '@/lib/types'
import { TimeSeriesChart } from '@/components/charts'
import { Card, EmptyState, ErrorState, FilterField, Input, PageHeader, Segmented, Select, Spinner } from '@/components/ui'

interface Objects {
  objects: Record<string, string[]>
  fields: Record<string, string[]>
}

const FIELD_LABEL: Record<string, string> = {
  read_latency_ms: 'Latence lecture',
  write_latency_ms: 'Latence écriture',
  storage_gb: 'Stockage',
  usage_pct: 'Utilisation',
}
const FIELD_UNIT: Record<string, string> = {
  cpu_usage_pct: '%', ram_usage_pct: '%', usage_pct: '%', read_latency_ms: 'ms', write_latency_ms: 'ms', storage_gb: 'Go',
}
const TYPE_LABEL: Record<string, string> = { 'Virtual Machine': 'Machine virtuelle', Datastore: 'Datastore' }

export default function Explorer() {
  const [type, setType] = useState('Virtual Machine')
  const [name, setName] = useState('')
  const [field, setField] = useState('cpu_usage_pct')
  const [hours, setHours] = useState(12)

  const objects = useQuery({ queryKey: ['explorer-objects'], queryFn: () => api.get<Objects>('/metrics/explorer/objects') })
  const names = objects.data?.objects[type] ?? []
  const fields = objects.data?.fields[type] ?? []
  const valid = name && names.includes(name)

  const hist = useQuery({
    queryKey: ['explorer', type, name, field, hours],
    queryFn: () => api.get<{ history: SeriesPoint[] }>('/metrics/explorer/history', { object_type: type, object_name: name, metric_field: field, hours }),
    enabled: Boolean(valid && fields.includes(field)),
  })
  const unit = FIELD_UNIT[field]

  return (
    <>
      <PageHeader title="Explorateur de métriques" description="Tracez l’historique d’une métrique pour une VM ou un datastore." />
      <Card className="mb-4">
        {objects.isLoading ? <Spinner /> : objects.isError ? <ErrorState error={objects.error} onRetry={() => objects.refetch()} /> : (
          <div className="flex flex-wrap items-end gap-3">
            <FilterField label="Type d’objet">
              {(id) => (
                <Select id={id} value={type} onChange={(e) => { const t = e.target.value; setType(t); setName(''); setField(objects.data?.fields[t]?.[0] ?? '') }}>
                  {Object.keys(objects.data?.objects ?? {}).map((t) => <option key={t} value={t}>{TYPE_LABEL[t] ?? t}</option>)}
                </Select>
              )}
            </FilterField>
            <FilterField label={`Objet (${names.length})`}>
              {(id) => (
                <>
                  <Input id={id} list="explorer-names" placeholder="Saisir ou choisir un nom…" className="w-72" value={name} onChange={(e) => setName(e.target.value)} />
                  <datalist id="explorer-names">{names.slice(0, 1000).map((n) => <option key={n} value={n} />)}</datalist>
                </>
              )}
            </FilterField>
            <FilterField label="Métrique">
              {(id) => (
                <Select id={id} value={field} onChange={(e) => setField(e.target.value)}>
                  {fields.map((f) => <option key={f} value={f}>{FIELD_LABEL[f] ?? metricLabel(f)}</option>)}
                </Select>
              )}
            </FilterField>
            <div className="flex flex-col gap-1">
              <span className="text-2xs font-medium text-muted">Période</span>
              <Segmented label="Période" value={hours} onChange={setHours}
                items={[{ value: 1, label: '1 h' }, { value: 12, label: '12 h' }, { value: 24, label: '24 h' }, { value: 168, label: '7 j' }, { value: 720, label: '30 j' }]} />
            </div>
          </div>
        )}
      </Card>

      <Card title={valid ? `${name} — ${FIELD_LABEL[field] ?? metricLabel(field)}` : 'Graphique'}>
        {!valid ? (
          <EmptyState icon={ChartLine} title="Choisissez un objet">{name ? 'Aucun objet ne porte ce nom.' : 'Sélectionnez une VM ou un datastore ci-dessus.'}</EmptyState>
        ) : hist.isLoading ? <Spinner /> : hist.isError ? <ErrorState error={hist.error} onRetry={() => hist.refetch()} /> : hist.data?.history.length ? (
          <TimeSeriesChart
            data={hist.data.history}
            series={[{ key: 'value', label: FIELD_LABEL[field] ?? metricLabel(field) }]}
            unit={unit}
            digits={2}
            height={340}
            spanHours={hours}
            yMax={unit === '%' ? 100 : undefined}
            ariaLabel={`Historique de ${field} pour ${name}`}
          />
        ) : <EmptyState title="Pas de données sur la période" />}
      </Card>
    </>
  )
}
