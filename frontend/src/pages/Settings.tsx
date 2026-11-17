import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Check, Pencil, Plus, UserRound } from 'lucide-react'
import { api } from '@/lib/api'
import { ROLE_LABEL, useAuth } from '@/lib/auth'
import { dateTime, relativeTime } from '@/lib/format'
import type { AuditEvent, Role, Setting, User } from '@/lib/types'
import {
  Button, Card, ErrorState, Field, IconButton, InlineError, Input, Modal, PageHeader, Select, Spinner, StatusBadge, Tabs,
} from '@/components/ui'

type Tab = 'general' | 'users' | 'audit'

const SETTING_GROUPS: Array<{ category: string; title: string }> = [
  { category: 'POLLING', title: 'Collecte' },
  { category: 'ALERTING', title: 'Alertes' },
  { category: 'SMTP', title: 'Serveur de messagerie' },
]

function SettingRow({ s }: { s: Setting }) {
  const qc = useQueryClient()
  const [value, setValue] = useState(s.value)
  useEffect(() => setValue(s.value), [s.value])
  const save = useMutation({
    mutationFn: () => api.put<Setting>(`/inventory/settings/${s.key}`, { value }),
    onSuccess: (updated) => { setValue(updated.value); qc.invalidateQueries({ queryKey: ['settings'] }) },
  })
  const id = `setting-${s.key}`
  const dirty = value !== s.value
  return (
    <div className="grid gap-2 border-b border-border/60 py-3 last:border-0 sm:grid-cols-[1fr_20rem_auto] sm:items-start">
      <label htmlFor={id} className="text-sm">
        <span className="font-medium">{s.description ?? s.key}</span>
        <span className="block font-mono text-2xs text-muted">{s.key}</span>
      </label>
      <div>
        {s.key === 'smtp_use_tls' ? (
          <Select id={id} value={value} onChange={(e) => setValue(e.target.value)}>
            <option value="true">Oui</option><option value="false">Non</option>
          </Select>
        ) : (
          <Input id={id} value={value} onChange={(e) => setValue(e.target.value)} className={/port|interval|threshold/.test(s.key) ? 'num' : undefined} />
        )}
        {save.isError ? <p className="mt-1 text-2xs text-fg" role="alert">{(save.error as Error).message}</p> : null}
      </div>
      <Button size="sm" icon={save.isSuccess && !dirty ? Check : undefined} disabled={!dirty} loading={save.isPending} onClick={() => save.mutate()}>
        {save.isSuccess && !dirty ? 'Enregistré' : 'Enregistrer'}
      </Button>
    </div>
  )
}

function GeneralTab() {
  const q = useQuery({ queryKey: ['settings'], queryFn: () => api.get<Setting[]>('/inventory/settings') })
  if (q.isLoading) return <Spinner />
  if (q.isError) return <ErrorState error={q.error} onRetry={() => q.refetch()} />
  return (
    <div className="flex flex-col gap-4">
      {SETTING_GROUPS.map((g) => {
        const items = q.data?.filter((s) => s.category === g.category) ?? []
        if (!items.length) return null
        return <Card key={g.category} title={g.title} bodyClassName="py-1">{items.map((s) => <SettingRow key={s.key} s={s} />)}</Card>
      })}
      <p className="text-2xs text-muted">Les identifiants SMTP (utilisateur, mot de passe) se configurent dans les variables d’environnement du serveur, jamais dans l’interface.</p>
    </div>
  )
}

function UserForm({ user, open, onClose }: { user: User | null; open: boolean; onClose: () => void }) {
  const qc = useQueryClient()
  const [username, setUsername] = useState('')
  const [fullName, setFullName] = useState('')
  const [role, setRole] = useState<Role>('viewer')
  const [password, setPassword] = useState('')
  const [active, setActive] = useState(true)
  useEffect(() => {
    if (!open) return
    setUsername(user?.username ?? ''); setFullName(user?.full_name ?? ''); setRole(user?.role ?? 'viewer'); setPassword(''); setActive(user?.is_active ?? true)
  }, [open, user])
  const isAd = user?.auth_source === 'ldap'
  const save = useMutation({
    mutationFn: () => isAd
      ? api.put(`/auth/users/${user!.id}`, { is_active: active })
      : user
      ? api.put(`/auth/users/${user.id}`, { full_name: fullName || null, role, is_active: active, ...(password ? { password } : {}) })
      : api.post('/auth/users', { username: username.trim(), full_name: fullName || null, role, password }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['users'] }); onClose() },
  })
  return (
    <Modal open={open} onClose={onClose} title={user ? `Modifier ${user.username}` : 'Nouvel utilisateur'}
      footer={<>
        <Button variant="ghost" onClick={onClose}>Annuler</Button>
        <Button variant="primary" loading={save.isPending} disabled={!user && (!username.trim() || !password)} onClick={() => save.mutate()}>Enregistrer</Button>
      </>}>
      <div className="flex flex-col gap-4">
        {isAd ? (
          <p className="rounded-md bg-surface-2 p-3 text-sm text-fg-2">
            Compte Active Directory : le nom, le mot de passe et le rôle ({user ? ROLE_LABEL[user.role] : ''}) viennent de l’AD et de ses groupes, synchronisés à chaque connexion. Seule l’activation se règle ici.
          </p>
        ) : null}
        {!user ? <Field label="Identifiant" htmlFor="u-name"><Input id="u-name" autoComplete="off" value={username} onChange={(e) => setUsername(e.target.value)} /></Field> : null}
        {!isAd ? <>
        <Field label="Nom complet" htmlFor="u-full"><Input id="u-full" value={fullName} onChange={(e) => setFullName(e.target.value)} /></Field>
        <Field label="Rôle" htmlFor="u-role" hint="Lecteur : consultation. Opérateur : + exports, applications, alertes. Administrateur : + équipements, paramètres, utilisateurs.">
          <Select id="u-role" value={role} onChange={(e) => setRole(e.target.value as Role)}>
            {(['viewer', 'operator', 'admin'] as Role[]).map((r) => <option key={r} value={r}>{ROLE_LABEL[r]}</option>)}
          </Select>
        </Field>
        <Field label={user ? 'Nouveau mot de passe' : 'Mot de passe'} htmlFor="u-pass" hint="12 caractères minimum, mélangeant au moins 3 types (minuscules, majuscules, chiffres, symboles).">
          <Input id="u-pass" type="password" autoComplete="new-password" value={password} onChange={(e) => setPassword(e.target.value)} />
        </Field>
        </> : null}
        {user ? (
          <label className="flex cursor-pointer items-center gap-2 text-sm">
            <input type="checkbox" className="size-4 accent-[rgb(var(--accent))]" checked={active} onChange={(e) => setActive(e.target.checked)} />
            Compte actif
          </label>
        ) : null}
        <InlineError message={save.isError ? (save.error as Error).message : null} />
      </div>
    </Modal>
  )
}

function UsersTab() {
  const { user: me } = useAuth()
  const q = useQuery({ queryKey: ['users'], queryFn: () => api.get<User[]>('/auth/users') })
  const [editing, setEditing] = useState<User | null>(null)
  const [open, setOpen] = useState(false)
  return (
    <Card title="Utilisateurs" bodyClassName="p-0" actions={<Button size="sm" variant="primary" icon={Plus} onClick={() => { setEditing(null); setOpen(true) }}>Ajouter</Button>}>
      {q.isLoading ? <Spinner /> : q.isError ? <ErrorState error={q.error} onRetry={() => q.refetch()} /> : (
        <div className="overflow-x-auto">
          <table className="table-base">
            <thead><tr><th>Utilisateur</th><th>Rôle</th><th>Source</th><th>État</th><th>Dernière connexion</th><th className="w-12" /></tr></thead>
            <tbody>
              {q.data?.map((u) => (
                <tr key={u.id}>
                  <td><span className="flex items-center gap-2"><UserRound className="size-4 text-muted" aria-hidden /><span className="font-medium">{u.username}</span>{u.id === me?.id ? <span className="text-2xs text-muted">(vous)</span> : null}</span>
                    {u.full_name ? <p className="pl-6 text-2xs text-muted">{u.full_name}</p> : null}</td>
                  <td>{ROLE_LABEL[u.role]}</td>
                  <td className="text-2xs">{u.auth_source === 'ldap' ? 'Active Directory' : 'Local'}</td>
                  <td><StatusBadge status={u.is_active ? 'ONLINE' : 'NO_DATA'} label={u.is_active ? 'Actif' : 'Désactivé'} /></td>
                  <td className="text-2xs text-muted" title={dateTime(u.last_login_at)}>{relativeTime(u.last_login_at)}</td>
                  <td><IconButton icon={Pencil} label={`Modifier ${u.username}`} onClick={() => { setEditing(u); setOpen(true) }} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <UserForm user={editing} open={open} onClose={() => setOpen(false)} />
    </Card>
  )
}

const ACTION_LABEL: Record<string, string> = {
  'auth.login': 'Connexion', 'auth.login_failed': 'Échec de connexion', 'user.created': 'Utilisateur créé', 'user.updated': 'Utilisateur modifié',
  'user.password_changed': 'Mot de passe changé', 'user.ldap_provisioned': 'Compte AD créé', 'user.ldap_role_synced': 'Rôle AD synchronisé', 'auth.ldap_denied': 'Connexion AD refusée (groupe)', 'auth.ldap_unavailable': 'Annuaire AD indisponible', 'user.bootstrap': 'Compte initial créé', 'device.created': 'Équipement ajouté',
  'device.updated': 'Équipement modifié', 'device.archived': 'Équipement archivé', 'device.reactivated': 'Équipement réactivé',
  'device.discover': 'Détection lancée', 'setting.updated': 'Paramètre modifié', 'application.created': 'Application créée',
  'application.updated': 'Application modifiée', 'application.deleted': 'Application supprimée', 'alert.resolved': 'Alerte traitée',
  'report.export_csv': 'Export CSV', 'report.export_excel': 'Export Excel',
}

function AuditTab() {
  const q = useQuery({ queryKey: ['audit'], queryFn: () => api.get<AuditEvent[]>('/auth/audit', { limit: 300 }) })
  return (
    <Card title="Journal d’audit (300 derniers évènements)" bodyClassName="p-0">
      {q.isLoading ? <Spinner /> : q.isError ? <ErrorState error={q.error} onRetry={() => q.refetch()} /> : (
        <div className="max-h-[70vh] overflow-auto">
          <table className="table-base">
            <thead><tr><th>Date</th><th>Utilisateur</th><th>Action</th><th>Cible</th><th>Détails</th></tr></thead>
            <tbody>
              {q.data?.map((e) => (
                <tr key={e.id}>
                  <td className="whitespace-nowrap text-2xs">{dateTime(e.timestamp)}</td>
                  <td className="font-mono text-2xs">{e.username}</td>
                  <td>{ACTION_LABEL[e.action] ?? e.action}</td>
                  <td className="text-2xs">{e.target ?? '—'}</td>
                  <td className="max-w-md truncate font-mono text-2xs text-muted" title={e.details ?? undefined}>{e.details ?? ''}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  )
}

export default function Settings() {
  const [tab, setTab] = useState<Tab>('general')
  return (
    <>
      <PageHeader title="Paramètres" description="Réglages de la plateforme, comptes utilisateurs et traçabilité." />
      <Tabs<Tab> value={tab} onChange={setTab} items={[
        { value: 'general', label: 'Général' },
        { value: 'users', label: 'Utilisateurs' },
        { value: 'audit', label: 'Journal d’audit' },
      ]} />
      {tab === 'general' ? <GeneralTab /> : tab === 'users' ? <UsersTab /> : <AuditTab />}
    </>
  )
}
