import { useEffect, useState, type FormEvent } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Archive, HardDrive, Pencil, Plus, Radar } from 'lucide-react'
import { api } from '@/lib/api'
import { useAuth } from '@/lib/auth'
import { CATEGORY_LABEL, TYPE_LABEL, dateTime, isDemoHost, relativeTime } from '@/lib/format'
import type { Device, Vendor } from '@/lib/types'
import {
  Button, Card, DemoBadge, EmptyState, ErrorState, Field, IconButton, InlineError, Input, Modal, PageHeader, Select, Spinner, StatusBadge,
} from '@/components/ui'

interface FormState {
  display_name: string
  hostname_ip: string
  device_type: string
  port: string
  username: string
  password: string
  is_active: boolean
}

const EMPTY: FormState = { display_name: '', hostname_ip: '', device_type: '', port: '', username: '', password: '', is_active: true }

function DeviceForm({ device, open, onClose, vendors }: { device: Device | null; open: boolean; onClose: () => void; vendors: Vendor[] }) {
  const qc = useQueryClient()
  const [form, setForm] = useState<FormState>(EMPTY)
  const [discoverMsg, setDiscoverMsg] = useState<string | null>(null)

  useEffect(() => {
    if (!open) return
    setDiscoverMsg(null)
    setForm(device
      ? { display_name: device.display_name, hostname_ip: device.hostname_ip, device_type: device.device_type, port: String(device.port), username: device.username, password: '', is_active: device.is_active }
      : EMPTY)
  }, [open, device])

  const set = <K extends keyof FormState>(k: K, v: FormState[K]) => setForm((f) => ({ ...f, [k]: v }))

  const discover = useMutation({
    mutationFn: () => api.post<{ detected_type: string; recommended_port: number }>('/inventory/discover', { hostname_ip: form.hostname_ip.trim() }),
    onSuccess: (r) => {
      setForm((f) => ({ ...f, device_type: r.detected_type, port: String(r.recommended_port) }))
      setDiscoverMsg(`Détecté : ${TYPE_LABEL[r.detected_type] ?? r.detected_type} (port ${r.recommended_port}).`)
    },
    onError: (e) => setDiscoverMsg((e as Error).message),
  })

  const save = useMutation({
    mutationFn: () => {
      const body: Record<string, unknown> = {
        display_name: form.display_name.trim(), hostname_ip: form.hostname_ip.trim(), device_type: form.device_type,
        port: Number(form.port), username: form.username.trim(),
      }
      if (form.password) body.password = form.password
      if (device) return api.put<Device>(`/inventory/devices/${device.id}`, { ...body, is_active: form.is_active })
      return api.post<Device>('/inventory/devices', body)
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['inventory'] })
      qc.invalidateQueries({ queryKey: ['realtime'] })
      onClose()
    },
  })

  const valid = form.display_name.trim() && form.hostname_ip.trim() && form.device_type && Number(form.port) > 0 && form.username.trim() && (device || form.password)

  function submit(e: FormEvent) {
    e.preventDefault()
    if (valid) save.mutate()
  }

  return (
    <Modal open={open} onClose={onClose} title={device ? `Modifier « ${device.display_name} »` : 'Ajouter un équipement'}
      footer={<>
        <Button variant="ghost" onClick={onClose}>Annuler</Button>
        <Button variant="primary" type="submit" form="device-form" loading={save.isPending} disabled={!valid}>Enregistrer</Button>
      </>}>
      <form id="device-form" onSubmit={submit} className="flex flex-col gap-4">
        <Field label="Nom affiché" htmlFor="d-name"><Input id="d-name" value={form.display_name} maxLength={128} onChange={(e) => set('display_name', e.target.value)} /></Field>
        <Field label="Adresse IP ou nom d’hôte" htmlFor="d-host" hint="Sans « https:// » ni port.">
          <div className="flex gap-2">
            <Input id="d-host" className="font-mono" value={form.hostname_ip} onChange={(e) => set('hostname_ip', e.target.value)} />
            <Button type="button" icon={Radar} loading={discover.isPending} disabled={!form.hostname_ip.trim()} onClick={() => discover.mutate()}>Détecter</Button>
          </div>
        </Field>
        {discoverMsg ? <p className="-mt-2 text-2xs text-fg-2" role="status">{discoverMsg}</p> : null}
        <div className="grid grid-cols-[1fr_7rem] gap-3">
          <Field label="Type d’équipement" htmlFor="d-type">
            <Select id="d-type" value={form.device_type} onChange={(e) => {
              const v = vendors.find((x) => x.device_type === e.target.value)
              setForm((f) => ({ ...f, device_type: e.target.value, port: f.port || String(v?.port ?? '') }))
            }}>
              <option value="" disabled>Choisir…</option>
              {vendors.map((v) => <option key={v.device_type} value={v.device_type}>{v.label}</option>)}
            </Select>
          </Field>
          <Field label="Port" htmlFor="d-port"><Input id="d-port" type="number" min={1} max={65535} className="num" value={form.port} onChange={(e) => set('port', e.target.value)} /></Field>
        </div>
        <Field label="Compte de service" htmlFor="d-user" hint="Utilisez un compte en lecture seule dédié à la supervision.">
          <Input id="d-user" autoComplete="off" value={form.username} onChange={(e) => set('username', e.target.value)} />
        </Field>
        <Field label="Mot de passe" htmlFor="d-pass" hint={device ? 'Laisser vide pour conserver le mot de passe actuel. Il n’est jamais réaffiché.' : 'Chiffré en base ; il n’est jamais réaffiché.'}>
          <Input id="d-pass" type="password" autoComplete="new-password" value={form.password} onChange={(e) => set('password', e.target.value)} />
        </Field>
        {device ? (
          <label className="flex cursor-pointer items-center gap-2 text-sm">
            <input type="checkbox" className="size-4 accent-[rgb(var(--accent))]" checked={form.is_active} onChange={(e) => set('is_active', e.target.checked)} />
            Collecte active
          </label>
        ) : null}
        <InlineError message={save.isError ? (save.error as Error).message : null} />
      </form>
    </Modal>
  )
}

export default function Inventory() {
  const { can } = useAuth()
  const isAdmin = can('admin')
  const qc = useQueryClient()
  const [showArchived, setShowArchived] = useState(false)
  const [editing, setEditing] = useState<Device | null>(null)
  const [formOpen, setFormOpen] = useState(false)
  const [archiving, setArchiving] = useState<Device | null>(null)

  const devices = useQuery({
    queryKey: ['inventory', showArchived],
    queryFn: () => api.get<Device[]>('/inventory/devices', { include_archived: showArchived }),
    refetchInterval: 60_000,
  })
  const vendors = useQuery({ queryKey: ['vendors'], queryFn: () => api.get<Vendor[]>('/inventory/vendors'), staleTime: Infinity })

  const archive = useMutation({
    mutationFn: (id: string) => api.del(`/inventory/devices/${id}`),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['inventory'] }); qc.invalidateQueries({ queryKey: ['realtime'] }); setArchiving(null) },
  })

  return (
    <>
      <PageHeader
        title="Inventaire"
        description="Équipements supervisés et état de leur dernière collecte."
        actions={<>
          <label className="flex cursor-pointer items-center gap-2 text-sm text-fg-2">
            <input type="checkbox" className="size-4 accent-[rgb(var(--accent))]" checked={showArchived} onChange={(e) => setShowArchived(e.target.checked)} />
            Afficher les archivés
          </label>
          {isAdmin ? <Button variant="primary" icon={Plus} onClick={() => { setEditing(null); setFormOpen(true) }}>Ajouter</Button> : null}
        </>}
      />
      <Card bodyClassName="p-0">
        {devices.isLoading ? <Spinner /> : devices.isError ? <ErrorState error={devices.error} onRetry={() => devices.refetch()} /> : !devices.data?.length ? (
          <EmptyState icon={HardDrive} title="Aucun équipement">{isAdmin ? 'Ajoutez un premier équipement pour démarrer la collecte.' : 'Un administrateur doit ajouter les équipements.'}</EmptyState>
        ) : (
          <div className="overflow-x-auto">
            <table className="table-base">
              <thead><tr><th>Nom</th><th>Type</th><th>Adresse</th><th>Compte</th><th>Collecte</th><th>Dernière collecte</th>{isAdmin ? <th className="w-24" /> : null}</tr></thead>
              <tbody>
                {devices.data.map((d) => (
                  <tr key={d.id} className={d.is_archived ? 'opacity-60' : undefined}>
                    <td className="font-medium"><span className="flex items-center gap-2">{d.display_name}{isDemoHost(d.hostname_ip) ? <DemoBadge /> : null}</span></td>
                    <td>{TYPE_LABEL[d.device_type] ?? d.device_type}<p className="text-2xs text-muted">{CATEGORY_LABEL[d.category] ?? d.category}</p></td>
                    <td className="font-mono text-2xs">{d.hostname_ip}:{d.port}</td>
                    <td className="font-mono text-2xs">{d.username}</td>
                    <td>
                      <StatusBadge status={d.is_archived ? 'ARCHIVED' : !d.is_active ? 'NO_DATA' : d.last_sync_status} label={!d.is_archived && !d.is_active ? 'Suspendue' : undefined} />
                      {d.last_sync_error && !d.is_archived ? <p className="mt-1 max-w-xs truncate text-2xs text-fg-2" title={d.last_sync_error}>{d.last_sync_error}</p> : null}
                    </td>
                    <td className="text-2xs text-muted" title={dateTime(d.last_sync_at)}>{relativeTime(d.last_sync_at)}</td>
                    {isAdmin ? (
                      <td className="text-right">
                        {!d.is_archived ? (
                          <div className="flex justify-end">
                            <IconButton icon={Pencil} label={`Modifier ${d.display_name}`} onClick={() => { setEditing(d); setFormOpen(true) }} />
                            <IconButton icon={Archive} label={`Archiver ${d.display_name}`} onClick={() => setArchiving(d)} />
                          </div>
                        ) : null}
                      </td>
                    ) : null}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <DeviceForm device={editing} open={formOpen} onClose={() => setFormOpen(false)} vendors={vendors.data ?? []} />
      <Modal open={Boolean(archiving)} onClose={() => setArchiving(null)} title="Archiver l’équipement ?"
        footer={<>
          <Button variant="ghost" onClick={() => setArchiving(null)}>Annuler</Button>
          <Button variant="danger" icon={Archive} loading={archive.isPending} onClick={() => archiving && archive.mutate(archiving.id)}>Archiver</Button>
        </>}>
        <p className="text-sm">La collecte de « {archiving?.display_name} » s’arrête. Son historique est conservé et l’équipement peut être réactivé en l’ajoutant de nouveau avec la même adresse.</p>
        <InlineError message={archive.isError ? (archive.error as Error).message : null} />
      </Modal>
    </>
  )
}
