import { useState } from 'react'
import clsx from 'clsx'
import { fmt } from '@/lib/format'
import type { DeviceState } from '@/lib/types'
import { Card, Segmented, UsageBar } from './ui'

type Rightsizing = NonNullable<DeviceState['rightsizing']>[number]

const KIND_LABEL: Record<Rightsizing['kind'], string> = {
  oversized: 'Surdimensionnée',
  undersized: 'Sous-dimensionnée',
  idle: 'Inactive',
}

/** Jours avant saturation : au-delà d'un an, la précision n'a plus de sens. */
function Days({ value }: { value?: number }) {
  if (value === undefined || value === null) return <span className="text-muted">—</span>
  const tone = value < 30 ? 'text-crit' : value < 90 ? 'text-warn' : undefined
  return <span className={clsx('num', tone)}>{value >= 365 ? '> 1 an' : fmt(value, 0, 'j')}</span>
}

/** Score vROps 0–100. `higherIsBetter` faux pour le risque. Couleur doublée du libellé. */
function Score({ value, higherIsBetter = true }: { value?: number; higherIsBetter?: boolean }) {
  if (value === undefined || value === null || value < 0) return <span className="text-muted">—</span>
  const good = higherIsBetter ? value : 100 - value
  const [tone, word] = good >= 75 ? ['text-ok', 'bon'] : good >= 50 ? ['text-warn', 'moyen'] : ['text-crit', 'mauvais']
  return (
    <span className={clsx('num', tone)} title={word}>
      {fmt(value, 0)} <span className="sr-only">({word})</span>
    </span>
  )
}

export function VropsSections({ d }: { d: DeviceState }) {
  const [kind, setKind] = useState<'all' | Rightsizing['kind']>('all')
  const clusters = d.clusters_metrics ?? []
  const datastores = d.vrops_datastores ?? []
  const rightsizing = d.rightsizing ?? []
  const shown = kind === 'all' ? rightsizing : rightsizing.filter((r) => r.kind === kind)

  return (
    <>
      {clusters.length ? (
        <Card className="mb-4" title={`Clusters (${clusters.length})`} bodyClassName="p-0">
          <div className="max-h-[420px] overflow-auto">
            <table className="table-base">
              <thead>
                <tr>
                  <th>Cluster</th><th className="w-44">CPU</th><th className="w-44">RAM</th>
                  <th className="text-right">Capacité restante</th><th className="text-right">Avant saturation</th>
                  <th className="text-right">Santé</th><th className="text-right">Risque</th><th className="text-right">Efficacité</th>
                  <th className="text-right">VMs</th>
                </tr>
              </thead>
              <tbody>
                {clusters.map((c) => (
                  <tr key={c.name}>
                    <td className="font-mono text-2xs">{c.name}</td>
                    <td><UsageBar value={c.cpu_usage_pct} /></td>
                    <td><UsageBar value={c.ram_usage_pct} /></td>
                    <td className="num text-right">{fmt(c.capacity_remaining_pct, 0, '%')}</td>
                    <td className="text-right"><Days value={c.time_remaining_days} /></td>
                    <td className="text-right"><Score value={c.health} /></td>
                    <td className="text-right"><Score value={c.risk} higherIsBetter={false} /></td>
                    <td className="text-right"><Score value={c.efficiency} /></td>
                    <td className="num text-right">{fmt(c.running_vms, 0)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      ) : null}

      {datastores.length ? (
        <Card className="mb-4" title={`Datastores selon vROps (${datastores.length})`} bodyClassName="p-0">
          <div className="max-h-[420px] overflow-auto">
            <table className="table-base">
              <thead><tr><th>Datastore</th><th className="text-right">Capacité</th><th className="text-right">Utilisé</th><th className="w-56">Utilisation</th><th className="text-right">Avant saturation</th></tr></thead>
              <tbody>
                {datastores.map((ds) => (
                  <tr key={ds.name}>
                    <td className="font-mono text-2xs">{ds.name}</td>
                    <td className="num text-right">{fmt(ds.capacity_tb, 2, 'To')}</td>
                    <td className="num text-right">{fmt(ds.used_tb, 2, 'To')}</td>
                    <td><UsageBar value={ds.usage_pct} /></td>
                    <td className="text-right"><Days value={ds.time_remaining_days} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      ) : null}

      {rightsizing.length ? (
        <Card
          title={`Redimensionnement recommandé (${rightsizing.length})`}
          bodyClassName="p-0"
          actions={
            <Segmented label="Recommandation" value={kind} onChange={setKind} items={[
              { value: 'all', label: 'Toutes' },
              { value: 'oversized', label: 'Surdim.' },
              { value: 'undersized', label: 'Sous-dim.' },
              { value: 'idle', label: 'Inactives' },
            ]} />
          }
        >
          <div className="max-h-[420px] overflow-auto">
            <table className="table-base">
              <thead><tr><th>VM</th><th>Recommandation</th><th className="text-right">vCPU à ajuster</th><th className="text-right">Mémoire à ajuster</th></tr></thead>
              <tbody>
                {shown.map((r) => (
                  <tr key={`${r.vm_name}-${r.kind}`}>
                    <td className="font-mono text-2xs">{r.vm_name}</td>
                    <td>{KIND_LABEL[r.kind]}</td>
                    <td className="num text-right">{fmt(r.vcpus, 0)}</td>
                    <td className="num text-right">{fmt(r.memory_gb, 1, 'Go')}</td>
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
