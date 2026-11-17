import { useState, type FormEvent } from 'react'
import { useMutation } from '@tanstack/react-query'
import { api } from '@/lib/api'
import { ROLE_LABEL, useAuth } from '@/lib/auth'
import { dateTime } from '@/lib/format'
import { Button, Card, Field, InlineError, Input, PageHeader } from '@/components/ui'

export default function Account() {
  const { user, logout } = useAuth()
  const [current, setCurrent] = useState('')
  const [next, setNext] = useState('')
  const [confirm, setConfirm] = useState('')
  const change = useMutation({
    mutationFn: () => api.post('/auth/me/password', { current_password: current, new_password: next }),
    // Le changement invalide toutes les sessions, y compris celle-ci : reconnexion nécessaire.
    onSuccess: () => logout(),
  })
  const mismatch = confirm.length > 0 && next !== confirm

  function submit(e: FormEvent) {
    e.preventDefault()
    if (!mismatch && current && next) change.mutate()
  }

  return (
    <>
      <PageHeader title="Mon compte" />
      <div className="grid max-w-4xl gap-4 md:grid-cols-2">
        <Card title="Profil">
          <dl className="grid grid-cols-[8rem_1fr] gap-y-2 text-sm">
            <dt className="text-muted">Identifiant</dt><dd className="font-mono">{user?.username}</dd>
            <dt className="text-muted">Nom</dt><dd>{user?.full_name ?? '—'}</dd>
            <dt className="text-muted">Rôle</dt><dd>{user ? ROLE_LABEL[user.role] : ''}</dd>
            <dt className="text-muted">Compte</dt><dd>{user?.auth_source === 'ldap' ? 'Active Directory' : 'Local'}</dd>
            <dt className="text-muted">Connexion</dt><dd>{dateTime(user?.last_login_at)}</dd>
          </dl>
        </Card>
        {user?.auth_source === 'ldap' ? (
          <Card title="Mot de passe">
            <p className="text-sm text-fg-2">Votre compte est géré par l’Active Directory : changez votre mot de passe Windows. Votre rôle dépend de vos groupes AD et est mis à jour à chaque connexion.</p>
          </Card>
        ) : (
        <Card title="Changer mon mot de passe">
          <form onSubmit={submit} className="flex flex-col gap-3">
            <Field label="Mot de passe actuel" htmlFor="p-cur"><Input id="p-cur" type="password" autoComplete="current-password" value={current} onChange={(e) => setCurrent(e.target.value)} /></Field>
            <Field label="Nouveau mot de passe" htmlFor="p-new" hint="12 caractères minimum, au moins 3 types de caractères.">
              <Input id="p-new" type="password" autoComplete="new-password" value={next} onChange={(e) => setNext(e.target.value)} />
            </Field>
            <Field label="Confirmation" htmlFor="p-conf" error={mismatch ? 'Les deux mots de passe diffèrent.' : null}>
              <Input id="p-conf" type="password" autoComplete="new-password" value={confirm} onChange={(e) => setConfirm(e.target.value)} />
            </Field>
            <InlineError message={change.isError ? (change.error as Error).message : null} />
            <Button type="submit" variant="primary" loading={change.isPending} disabled={!current || !next || mismatch}>Changer le mot de passe</Button>
            <p className="text-2xs text-muted">Vos autres sessions seront déconnectées.</p>
          </form>
        </Card>
        )}
      </div>
    </>
  )
}
