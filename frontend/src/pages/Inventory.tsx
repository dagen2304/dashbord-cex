import { useEffect, useState, type FormEvent } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Archive, HardDrive, Lock, LockOpen, Pencil, Pin, Plus, Radar, RotateCcw, ScanSearch, ShieldAlert } from 'lucide-react'
import { api } from '@/lib/api'
import { useAuth } from '@/lib/auth'
import { CATEGORY_LABEL, TYPE_LABEL, dateTime, isDemoHost, relativeTime } from '@/lib/format'
import type { Device, TlsInspection, TlsMode, Vendor } from '@/lib/types'
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
  tls_mode: TlsMode
  tls_fingerprint: string
}

const SSH_TYPES = new Set(['VERITAS_ACCESS'])

const EMPTY: FormState = { display_name: '', hostname_ip: '', device_type: '', port: '', username: '', password: '', is_active: true, tls_mode: 'verify', tls_fingerprint: '' }

function DeviceForm({ device, open, onClose, vendors }: { device: Device | null; open: boolean; onClose: () => void; vendors: Vendor[] }) {
  const qc = useQueryClient()
  const [form, setForm] = useState<FormState>(EMPTY)
  const [discoverMsg, setDiscoverMsg] = useState<string | null>(null)
  const [inspection, setInspection] = useState<TlsInspection | null>(null)
  const isSsh = SSH_TYPES.has(form.device_type)
  useEffect(() => {
    // Pas d'autorité de certification en SSH : seule l'empreinte de la clé d'hôte permet de vérifier.
    if (isSsh && form.tls_mode === 'verify') setForm((f) => ({ ...f, tls_mode: 'pin' }))
  }, [isSsh, form.tls_mode])
  useEffect(() => setInspection(null), [form.hostname_ip, form.port, form.device_type])

  useEffect(() => {
    if (!open) return
    setDiscoverMsg(null)
    setInspection(null)
    setForm(device
      ? { display_name: device.display_name, hostname_ip: device.hostname_ip, device_type: device.device_type, port: String(device.port), username: device.username, password: '', is_active: device.is_active, tls_mode: device.tls_mode, tls_fingerprint: device.tls_fingerprint ?? '' }
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

  const inspect = useMutation({
    mutationFn: () => api.post<TlsInspection>('/inventory/tls/inspect', {
      hostname_ip: form.hostname_ip.trim(), port: Number(form.port), device_type: form.device_type,
    }),
    onSuccess: setInspection,
  })

  const save = useMutation({
    mutationFn: () => {
      const body: Record<string, unknown> = {
        display_name: form.display_name.trim(), hostname_ip: form.hostname_ip.trim(), device_type: form.device_type,
        port: Number(form.port), username: form.username.trim(),
      }
      if (form.password) body.password = form.password
      body.tls_mode = form.tls_mode
      body.tls_fingerprint = form.tls_mode === 'pin' ? form.tls_fingerprint.trim() : null
      if (device) return api.put<Device>(`/inventory/devices/${device.id}`, { ...body, is_active: form.is_active })
      return api.post<Device>('/inventory/devices', body)
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['inventory'] })
      qc.invalidateQueries({ queryKey: ['realtime'] })
      onClose()
    },
  })

  const valid = form.display_name.trim() && form.hostname_ip.trim() && form.device_type && Number(form.port) > 0 && form.username.trim() &&
    (device || form.password) && (form.tls_mode !== 'pin' || form.tls_fingerprint.trim())

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
        <fieldset className="flex flex-col gap-3 rounded-md border border-border p-3">
          <legend className="px-1 text-sm font-medium text-fg-2">Sécurité de la connexion</legend>
          <Field label={isSsh ? 'Vérification de la clé d’hôte SSH' : 'Vérification du certificat'} htmlFor="d-tls"
            hint={form.tls_mode === 'verify' ? 'Certificat signé par une autorité reconnue (ou l’AC interne DEVICE_CA_BUNDLE) et nom d’hôte correspondant.'
              : form.tls_mode === 'pin' ? 'Seul ce certificat précis est accepté. Recommandé pour les certificats auto-signés et les accès par IP.'
              : 'Aucune vérification : une interception permettrait de voler le mot de passe du compte de service.'}>
            <Select id="d-tls" value={form.tls_mode} onChange={(e) => set('tls_mode', e.target.value as TlsMode)}>
              {!isSsh ? <option value="verify">Vérifié par une autorité de certification</option> : null}
              <option value="pin">{isSsh ? 'Empreinte de la clé d’hôte épinglée' : 'Empreinte du certificat épinglée'}</option>
              <option value="insecure">Non vérifié (déconseillé)</option>
            </Select>
          </Field>
          {form.tls_mode === 'pin' ? (
            <Field label="Empreinte SHA-256" htmlFor="d-fp">
              <Input id="d-fp" className="font-mono text-2xs" placeholder="64 caractères hexadécimaux" value={form.tls_fingerprint}
                onChange={(e) => set('tls_fingerprint', e.target.value)} />
            </Field>
          ) : null}
          <div>
            <Button type="button" size="sm" icon={ScanSearch} loading={inspect.isPending}
              disabled={!form.hostname_ip.trim() || !form.device_type || !Number(form.port)} onClick={() => inspect.mutate()}>
              {isSsh ? 'Lire la clé SSH' : 'Lire le certificat'}
            </Button>
          </div>
          <InlineError message={inspect.isError ? (inspect.error as Error).message : null} />
          {inspection ? (
            <div className="rounded-md bg-surface-2 p-3 text-2xs" role="status">
              <dl className="grid grid-cols-[7rem_1fr] gap-x-2 gap-y-1">
                {inspection.kind === 'tls' ? <>
                  <dt className="text-muted">Sujet</dt><dd className="break-all">{inspection.subject}</dd>
                  <dt className="text-muted">Émetteur</dt><dd className="break-all">{inspection.issuer}{inspection.self_signed ? ' (auto-signé)' : ''}</dd>
                  <dt className="text-muted">Noms</dt><dd className="break-all">{inspection.subject_alt_names?.join(', ') || '—'}</dd>
                  <dt className="text-muted">Validité</dt><dd>jusqu’au {dateTime(inspection.not_after)}{inspection.expired ? ' — EXPIRÉ' : ''}</dd>
                  <dt className="text-muted">Approuvé</dt><dd>{inspection.trusted ? 'Oui (autorité reconnue)' : `Non — ${inspection.trust_error ?? ''}`}</dd>
                </> : <>
                  <dt className="text-muted">Type de clé</dt><dd>{inspection.key_type}</dd>
                </>}
                <dt className="text-muted">SHA-256</dt><dd className="break-all font-mono">{inspection.sha256}</dd>
              </dl>
              <p className="mt-2 text-muted">Comparez cette empreinte avec celle affichée par la console de l’équipement avant de l’épingler.</p>
              <div className="mt-2 flex gap-2">
                <Button type="button" size="sm" icon={Pin} onClick={() => setForm((f) => ({ ...f, tls_mode: 'pin', tls_fingerprint: inspection.sha256 }))}>
                  Épingler cette empreinte
                </Button>
                {inspection.trusted && !isSsh ? (
                  <Button type="button" size="sm" variant="ghost" onClick={() => set('tls_mode', 'verify')}>Utiliser la vérification par AC</Button>
                ) : null}
              </div>
            </div>
          ) : null}
        </fieldset>
        <Field label="Compte de service" htmlFor="d-user" hint={form.device_type === 'VROPS'
          ? 'Compte en lecture seule. Compte d’annuaire déclaré dans vROps : SOURCE\\utilisateur (SOURCE = nom de la source d’authentification dans vROps).'
          : form.device_type === 'NETBACKUP'
            ? 'Compte local : utilisateur. AD avec serveur primaire Windows : DOMAINE\\utilisateur. AD via domaine LDAP (serveur Linux, NetBackup Appliance) : ldap:domaine\\utilisateur. Adresse : le nom du serveur figurant dans son certificat.'
          : form.device_type === 'HUAWEI_DORADO'
            ? 'Compte en lecture seule. Compte LDAP / AD de la baie : LDAP\\utilisateur ; compte local : utilisateur seul.'
            : 'Utilisez un compte en lecture seule dédié à la supervision.'}>
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

function TlsBadge({ mode }: { mode: TlsMode }) {
  if (mode === 'verify') return <span className="inline-flex items-center gap-1 text-2xs text-fg-2" title="Certificat vérifié par une autorité de certification"><Lock className="size-3.5 text-ok" aria-hidden />AC</span>
  if (mode === 'pin') return <span className="inline-flex items-center gap-1 text-2xs text-fg-2" title="Empreinte épinglée"><Pin className="size-3.5 text-ok" aria-hidden />Empreinte</span>
  return <span className="inline-flex items-center gap-1 text-2xs font-medium text-fg" title="Connexion non vérifiée"><LockOpen className="size-3.5 text-warn" aria-hidden />Non vérifié</span>
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
  const insecureCount = (devices.data ?? []).filter((d) => !d.is_archived && d.tls_mode === 'insecure' && !isDemoHost(d.hostname_ip)).length
  const vendors = useQuery({ queryKey: ['vendors'], queryFn: () => api.get<Vendor[]>('/inventory/vendors'), staleTime: Infinity })

  const [resuming, setResuming] = useState<Device | null>(null)
  const resume = useMutation({
    mutationFn: (id: string) => api.post(`/inventory/devices/${id}/resume`),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['inventory'] }); qc.invalidateQueries({ queryKey: ['realtime'] }); setResuming(null) },
  })
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
      {insecureCount > 0 ? (
        <div className="mb-3 flex items-start gap-2 rounded-md border border-warn/50 bg-warn/10 px-3 py-2 text-sm" role="status">
          <ShieldAlert className="mt-0.5 size-4 shrink-0 text-warn" aria-hidden />
          <span>
            {insecureCount} équipement(s) collecté(s) sans vérification TLS/SSH : le mot de passe du compte de service pourrait être intercepté.
            {isAdmin ? ' Modifiez-les, puis « Lire le certificat » et épinglez son empreinte.' : ''}
          </span>
        </div>
      ) : null}
      <Card bodyClassName="p-0">
        {devices.isLoading ? <Spinner /> : devices.isError ? <ErrorState error={devices.error} onRetry={() => devices.refetch()} /> : !devices.data?.length ? (
          <EmptyState icon={HardDrive} title="Aucun équipement">{isAdmin ? 'Ajoutez un premier équipement pour démarrer la collecte.' : 'Un administrateur doit ajouter les équipements.'}</EmptyState>
        ) : (
          <div className="overflow-x-auto">
            <table className="table-base">
              <thead><tr><th>Nom</th><th>Type</th><th>Adresse</th><th>Compte</th><th>Sécurité</th><th>Collecte</th><th>Dernière collecte</th>{isAdmin ? <th className="w-24" /> : null}</tr></thead>
              <tbody>
                {devices.data.map((d) => (
                  <tr key={d.id} className={d.is_archived ? 'opacity-60' : undefined}>
                    <td className="font-medium"><span className="flex items-center gap-2">{d.display_name}{isDemoHost(d.hostname_ip) ? <DemoBadge /> : null}</span></td>
                    <td>{TYPE_LABEL[d.device_type] ?? d.device_type}<p className="text-2xs text-muted">{CATEGORY_LABEL[d.category] ?? d.category}</p></td>
                    <td className="font-mono text-2xs">{d.hostname_ip}:{d.port}</td>
                    <td className="font-mono text-2xs">{d.username}</td>
                    <td><TlsBadge mode={d.tls_mode} /></td>
                    <td>
                      <StatusBadge status={d.is_archived ? 'ARCHIVED' : !d.is_active ? 'NO_DATA' : d.last_sync_status} label={!d.is_archived && !d.is_active ? 'Suspendue' : undefined} />
                      {d.last_sync_error && !d.is_archived ? <p className="mt-1 max-w-xs truncate text-2xs text-fg-2" title={d.last_sync_error}>{d.last_sync_error}</p> : null}
                      {isAdmin && d.last_sync_status === 'AUTH_FAILED' && !d.is_archived ? (
                        <button type="button" className="mt-1 text-2xs font-medium text-accent hover:underline" onClick={() => setResuming(d)}>
                          Relancer la collecte
                        </button>
                      ) : null}
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
      <Modal open={Boolean(resuming)} onClose={() => setResuming(null)} title="Relancer la collecte ?"
        footer={<>
          <Button variant="ghost" onClick={() => setResuming(null)}>Annuler</Button>
          <Button variant="primary" icon={RotateCcw} loading={resume.isPending} onClick={() => resuming && resume.mutate(resuming.id)}>Relancer</Button>
        </>}>
        <p className="text-sm text-fg-2">
          {resuming?.display_name} a refusé les identifiants : la collecte est suspendue pour ne pas verrouiller le compte.
        </p>
        <p className="mt-2 text-sm text-fg-2">
          Ne relancez qu’après avoir corrigé la cause <strong className="text-fg">sur l’équipement</strong> (compte déverrouillé, mot de passe réinitialisé).
          Si le mot de passe a changé, modifiez plutôt l’équipement et saisissez le nouveau : la collecte reprend seule.
          Un seul essai est fait ; s’il échoue, la collecte est de nouveau suspendue.
        </p>
        {resume.isError ? <InlineError message={(resume.error as Error).message} /> : null}
      </Modal>

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
