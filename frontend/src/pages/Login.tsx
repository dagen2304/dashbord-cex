import { useState, type FormEvent } from 'react'
import { Navigate, useLocation } from 'react-router-dom'
import { LogIn } from 'lucide-react'
import { useAuth } from '@/lib/auth'
import { Button, Field, InlineError, Input } from '@/components/ui'

export default function Login() {
  const { user, login } = useAuth()
  const location = useLocation()
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  if (user) {
    const from = (location.state as { from?: string } | null)?.from ?? '/'
    return <Navigate to={from} replace />
  }

  async function submit(e: FormEvent) {
    e.preventDefault()
    setError(null)
    setBusy(true)
    try {
      await login(username.trim(), password)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Connexion impossible.')
      setPassword('')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="flex min-h-dvh items-center justify-center bg-bg px-4">
      <div className="w-full max-w-sm">
        <div className="mb-6 flex flex-col items-center text-center">
          <span className="mb-3 flex size-12 items-center justify-center rounded-lg bg-accent font-mono text-xl font-bold text-on-accent" aria-hidden>N</span>
          <h1 className="text-xl font-semibold">NOC INFRA</h1>
          <p className="mt-1 text-sm text-muted">Supervision de l’infrastructure — virtualisation, stockage, sauvegarde</p>
        </div>
        <form onSubmit={submit} className="card flex flex-col gap-4 p-6" noValidate>
          <Field label="Identifiant" htmlFor="username">
            <Input id="username" name="username" autoComplete="username" autoFocus required value={username}
              onChange={(e) => setUsername(e.target.value)} />
          </Field>
          <Field label="Mot de passe" htmlFor="password">
            <Input id="password" name="password" type="password" autoComplete="current-password" required value={password}
              onChange={(e) => setPassword(e.target.value)} />
          </Field>
          <InlineError message={error} />
          <Button type="submit" variant="primary" icon={LogIn} loading={busy} disabled={!username || !password} className="mt-1 h-10">
            Se connecter
          </Button>
        </form>
        <p className="mt-4 text-center text-2xs text-muted">Accès réservé aux équipes d’exploitation. Les connexions sont journalisées.</p>
      </div>
    </div>
  )
}
