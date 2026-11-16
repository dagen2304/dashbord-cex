import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Boxes, Pencil, Plus, Search, Trash2 } from 'lucide-react'
import { api } from '@/lib/api'
import { useAuth } from '@/lib/auth'
import { fmt, pct } from '@/lib/format'
import type { AppMetrics, Application, VmRef } from '@/lib/types'
import {
  Button, Card, EmptyState, ErrorState, Field, IconButton, InlineError, Input, Modal, PageHeader, Spinner, Stat, StatusBadge, UsageBar,
} from '@/components/ui'

function AppEditor({ app, open, onClose }: { app: Application | null; open: boolean; onClose: () => void }) {
  const qc = useQueryClient()
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [selected, setSelected] = useState<Map<number, VmRef>>(new Map())
  const [search, setSearch] = useState('')
  const [debounced, setDebounced] = useState('')

  useEffect(() => {
    if (!open) return
    setName(app?.name ?? '')
    setDescription(app?.description ?? '')
    setSelected(new Map((app?.vms ?? []).map((v) => [v.id, v])))
    setSearch('')
  }, [open, app])

  useEffect(() => {
    const t = window.setTimeout(() => setDebounced(search), 250)
    return () => window.clearTimeout(t)
  }, [search])

  const vms = useQuery({
    queryKey: ['vms-list', debounced],
    queryFn: () => api.get<VmRef[]>('/reports/apps/vms/list', { search: debounced, limit: 200 }),
    enabled: open,
  })

  const save = useMutation({
    mutationFn: () => {
      const body = { name: name.trim(), description: description.trim() || null, vm_ids: [...selected.keys()] }
      return app ? api.put<Application>(`/reports/apps/${app.id}`, body) : api.post<Application>('/reports/apps', body)
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['apps'] })
      qc.invalidateQueries({ queryKey: ['app-metrics'] })
      onClose()
    },
  })

  const toggle = (vm: VmRef) => setSelected((prev) => {
    const next = new Map(prev)
    if (next.has(vm.id)) next.delete(vm.id)
    else next.set(vm.id, vm)
    return next
  })

  return (
    <Modal open={open} onClose={onClose} title={app ? `Modifier « ${app.name} »` : 'Nouvelle application'} wide
      footer={<>
        <Button variant="ghost" onClick={onClose}>Annuler</Button>
        <Button variant="primary" loading={save.isPending} disabled={!name.trim()} onClick={() => save.mutate()}>Enregistrer</Button>
      </>}>
      <div className="flex flex-col gap-4">
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Nom" htmlFor="app-name"><Input id="app-name" value={name} maxLength={128} onChange={(e) => setName(e.target.value)} /></Field>
          <Field label="Description" htmlFor="app-desc"><Input id="app-desc" value={description} onChange={(e) => setDescription(e.target.value)} /></Field>
        </div>
        <div>
          <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
            <p className="text-sm font-medium text-fg-2">VMs associées <span className="num text-muted">({selected.size})</span></p>
            <div className="relative w-64">
              <Search className="pointer-events-none absolute left-2.5 top-2.5 size-4 text-muted" aria-hidden />
              <Input aria-label="Rechercher une VM" placeholder="Rechercher une VM…" className="pl-8" value={search} onChange={(e) => setSearch(e.target.value)} />
            </div>
          </div>
          {selected.size ? (
            <div className="mb-2 flex flex-wrap gap-1.5">
              {[...selected.values()].map((v) => (
                <button key={v.id} type="button" onClick={() => toggle(v)} className="cursor-pointer rounded-full border border-border bg-surface-2 px-2 py-0.5 font-mono text-2xs hover:border-crit" title="Retirer">
                  {v.name} ×
                </button>
              ))}
            </div>
          ) : null}
          <div className="max-h-72 overflow-y-auto rounded-md border border-border">
            {vms.isLoading ? <Spinner /> : !vms.data?.length ? <p className="p-4 text-center text-sm text-muted">Aucune VM trouvée. Les VMs apparaissent après la première collecte d’un vCenter.</p> : (
              <ul>
                {vms.data.map((v) => (
                  <li key={v.id}>
                    <label className="flex cursor-pointer items-center gap-3 border-b border-border/60 px-3 py-2 text-sm hover:bg-surface-2">
                      <input type="checkbox" className="size-4 accent-[rgb(var(--accent))]" checked={selected.has(v.id)} onChange={() => toggle(v)} />
                      <span className="font-mono text-2xs">{v.name}</span>
                      <span className="ml-auto text-2xs text-muted">{v.vcenter} / {v.cluster}</span>
                    </label>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </div>
        <InlineError message={save.isError ? (save.error as Error).message : null} />
      </div>
    </Modal>
  )
}

function AppDetail({ app }: { app: Application }) {
  const m = useQuery({ queryKey: ['app-metrics', app.id], queryFn: () => api.get<AppMetrics>(`/reports/apps/${app.id}/metrics`), refetchInterval: 60_000 })
  if (m.isLoading) return <Spinner />
  if (m.isError || !m.data) return <ErrorState error={m.error} onRetry={() => m.refetch()} />
  const s = m.data.summary
  return (
    <>
      <div className="mb-4 grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Stat label="VMs avec données" value={`${s.vms_active} / ${s.vms_total}`} />
        <Stat label="CPU moyen" value={pct(s.avg_cpu)} />
        <Stat label="RAM moyenne" value={pct(s.avg_ram)} />
        <Stat label="Stockage" value={fmt(s.total_storage_tb, 2, 'To')} />
      </div>
      <div className="overflow-x-auto">
        <table className="table-base">
          <thead><tr><th>VM</th><th>Cluster</th><th>OS</th><th>État</th><th className="w-44">CPU</th><th className="w-44">RAM</th><th className="text-right">Stockage</th></tr></thead>
          <tbody>
            {m.data.vms.map((v) => (
              <tr key={v.id}>
                <td className="font-mono text-2xs">{v.name}<p className="text-muted">{v.ip ?? ''}</p></td>
                <td className="text-2xs text-fg-2">{v.vcenter} / {v.cluster}</td>
                <td className="text-2xs">{v.os ?? '—'}</td>
                <td><StatusBadge status={v.status === 'ONLINE' ? 'ONLINE' : 'NO_DATA'} /></td>
                <td><UsageBar value={v.cpu_usage_pct} /></td>
                <td><UsageBar value={v.ram_usage_pct} /></td>
                <td className="num text-right">{fmt(v.storage_used_gb, 0, 'Go')}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  )
}

export default function Applications() {
  const { can } = useAuth()
  const qc = useQueryClient()
  const apps = useQuery({ queryKey: ['apps'], queryFn: () => api.get<Application[]>('/reports/apps') })
  const [selectedId, setSelectedId] = useState<number | null>(null)
  const [editing, setEditing] = useState<Application | null>(null)
  const [editorOpen, setEditorOpen] = useState(false)
  const [confirmDelete, setConfirmDelete] = useState<Application | null>(null)

  const remove = useMutation({
    mutationFn: (id: number) => api.del(`/reports/apps/${id}`),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['apps'] }); setConfirmDelete(null); setSelectedId(null) },
  })

  const selected = apps.data?.find((a) => a.id === selectedId) ?? apps.data?.[0] ?? null
  const isOperator = can('operator')

  return (
    <>
      <PageHeader
        title="Applications"
        description="Regroupez les VMs par application métier pour suivre leur consommation d’ensemble."
        actions={isOperator ? <Button variant="primary" icon={Plus} onClick={() => { setEditing(null); setEditorOpen(true) }}>Nouvelle application</Button> : null}
      />
      {apps.isLoading ? <Spinner /> : apps.isError ? <ErrorState error={apps.error} onRetry={() => apps.refetch()} /> : !apps.data?.length ? (
        <Card><EmptyState icon={Boxes} title="Aucune application">{isOperator ? 'Créez une application et associez-lui ses VMs.' : 'Un opérateur peut créer des applications.'}</EmptyState></Card>
      ) : (
        <div className="grid gap-4 lg:grid-cols-[16rem_1fr]">
          <Card bodyClassName="p-0" title={`Applications (${apps.data.length})`}>
            <ul>
              {apps.data.map((a) => (
                <li key={a.id}>
                  <button type="button" onClick={() => setSelectedId(a.id)} aria-pressed={selected?.id === a.id}
                    className={`w-full cursor-pointer border-b border-border/60 px-3 py-2.5 text-left hover:bg-surface-2 ${selected?.id === a.id ? 'bg-surface-2 shadow-[inset_2px_0_0_rgb(var(--accent))]' : ''}`}>
                    <p className="text-sm font-medium">{a.name}</p>
                    <p className="text-2xs text-muted">{a.vms.length} VM(s)</p>
                  </button>
                </li>
              ))}
            </ul>
          </Card>
          {selected ? (
            <Card
              title={<>{selected.name}{selected.description ? <span className="ml-2 font-normal text-muted">— {selected.description}</span> : null}</>}
              actions={isOperator ? (
                <div className="flex gap-1">
                  <IconButton icon={Pencil} label="Modifier" onClick={() => { setEditing(selected); setEditorOpen(true) }} />
                  <IconButton icon={Trash2} label="Supprimer" onClick={() => setConfirmDelete(selected)} />
                </div>
              ) : null}
            >
              <AppDetail app={selected} />
            </Card>
          ) : null}
        </div>
      )}

      <AppEditor app={editing} open={editorOpen} onClose={() => setEditorOpen(false)} />
      <Modal open={Boolean(confirmDelete)} onClose={() => setConfirmDelete(null)} title="Supprimer l’application ?"
        footer={<>
          <Button variant="ghost" onClick={() => setConfirmDelete(null)}>Annuler</Button>
          <Button variant="danger" icon={Trash2} loading={remove.isPending} onClick={() => confirmDelete && remove.mutate(confirmDelete.id)}>Supprimer</Button>
        </>}>
        <p className="text-sm">L’application « {confirmDelete?.name} » sera supprimée. Les VMs elles-mêmes ne sont pas affectées.</p>
        <InlineError message={remove.isError ? (remove.error as Error).message : null} />
      </Modal>
    </>
  )
}
