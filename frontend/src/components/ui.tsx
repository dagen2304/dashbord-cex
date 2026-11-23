import {
  forwardRef, useEffect, useId, useRef,
  type ButtonHTMLAttributes, type InputHTMLAttributes, type ReactNode, type SelectHTMLAttributes,
} from 'react'
import clsx from 'clsx'
import {
  AlertTriangle, CheckCircle2, CircleDashed, Clock, KeyRound, Loader2, OctagonAlert, PowerOff, X,
} from 'lucide-react'
import type { LucideIcon } from 'lucide-react'

// ── Boutons ──────────────────────────────────────────────────────────────────
type Variant = 'primary' | 'secondary' | 'ghost' | 'danger'
interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant
  size?: 'sm' | 'md'
  icon?: LucideIcon
  loading?: boolean
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { variant = 'secondary', size = 'md', icon: Icon, loading, className, children, disabled, ...rest },
  ref,
) {
  return (
    <button
      ref={ref}
      disabled={disabled || loading}
      className={clsx(
        'inline-flex shrink-0 cursor-pointer items-center justify-center gap-2 rounded-md font-medium transition-colors',
        'disabled:cursor-not-allowed disabled:opacity-50',
        size === 'md' ? 'h-9 px-3.5 text-sm' : 'h-8 px-2.5 text-2xs',
        variant === 'primary' && 'bg-accent text-on-accent hover:bg-accent/90',
        variant === 'secondary' && 'border border-border bg-surface-2 text-fg hover:bg-surface-2/70 hover:border-muted',
        variant === 'ghost' && 'text-fg-2 hover:bg-surface-2 hover:text-fg',
        variant === 'danger' && 'border border-crit/50 bg-crit/10 text-fg hover:bg-crit/20',
        className,
      )}
      {...rest}
    >
      {loading ? <Loader2 className="size-4 animate-spin" aria-hidden /> : Icon ? <Icon className="size-4" aria-hidden /> : null}
      {children}
    </button>
  )
})

export function IconButton({ icon: Icon, label, className, ...rest }: ButtonHTMLAttributes<HTMLButtonElement> & { icon: LucideIcon; label: string }) {
  return (
    <button
      type="button"
      aria-label={label}
      title={label}
      className={clsx('inline-flex size-9 cursor-pointer items-center justify-center rounded-md text-fg-2 transition-colors hover:bg-surface-2 hover:text-fg disabled:opacity-40', className)}
      {...rest}
    >
      <Icon className="size-4" aria-hidden />
    </button>
  )
}

// ── Formulaires ─────────────────────────────────────────────────────────────
export function Field({ label, hint, error, children, htmlFor }: { label: string; hint?: string; error?: string | null; children: ReactNode; htmlFor: string }) {
  return (
    <div className="flex flex-col gap-1.5">
      <label htmlFor={htmlFor} className="text-sm font-medium text-fg-2">{label}</label>
      {children}
      {error ? (
        <p className="flex items-center gap-1.5 text-2xs text-fg" role="alert">
          <OctagonAlert className="size-3.5 text-crit" aria-hidden />{error}
        </p>
      ) : hint ? <p className="text-2xs text-muted">{hint}</p> : null}
    </div>
  )
}

const inputCls = 'h-9 w-full rounded-md border border-border bg-bg px-3 text-sm text-fg placeholder:text-muted/70 transition-colors hover:border-muted focus:border-accent focus:outline-none focus-visible:outline-2 disabled:opacity-60'

export const Input = forwardRef<HTMLInputElement, InputHTMLAttributes<HTMLInputElement>>(function Input({ className, ...rest }, ref) {
  return <input ref={ref} className={clsx(inputCls, className)} {...rest} />
})

export const Select = forwardRef<HTMLSelectElement, SelectHTMLAttributes<HTMLSelectElement>>(function Select({ className, children, ...rest }, ref) {
  return <select ref={ref} className={clsx(inputCls, 'cursor-pointer pr-8', className)} {...rest}>{children}</select>
})

/** Champ compact pour les barres de filtres : libellé visible au-dessus. */
export function FilterField({ label, children }: { label: string; children: (id: string) => ReactNode }) {
  const id = useId()
  return (
    <div className="flex min-w-[9rem] flex-col gap-1">
      <label htmlFor={id} className="text-2xs font-medium text-muted">{label}</label>
      {children(id)}
    </div>
  )
}

// ── Mise en page ────────────────────────────────────────────────────────────
export function PageHeader({ title, description, actions }: { title: string; description?: ReactNode; actions?: ReactNode }) {
  return (
    <div className="mb-5 flex flex-wrap items-end justify-between gap-3">
      <div>
        <h1 className="text-xl font-semibold tracking-tight">{title}</h1>
        {description ? <p className="mt-1 text-sm text-muted">{description}</p> : null}
      </div>
      {actions ? <div className="flex flex-wrap items-center gap-2">{actions}</div> : null}
    </div>
  )
}

export function Card({ title, actions, children, className, bodyClassName }: { title?: ReactNode; actions?: ReactNode; children: ReactNode; className?: string; bodyClassName?: string }) {
  return (
    <section className={clsx('card', className)}>
      {title || actions ? (
        <header className="flex items-center justify-between gap-2 border-b border-border px-4 py-2.5">
          {title ? <h2 className="text-sm font-semibold">{title}</h2> : <span />}
          {actions}
        </header>
      ) : null}
      <div className={clsx('p-4', bodyClassName)}>{children}</div>
    </section>
  )
}

export function Stat({ label, value, sub, icon: Icon, tone }: { label: string; value: ReactNode; sub?: ReactNode; icon?: LucideIcon; tone?: 'ok' | 'warn' | 'crit' | 'nodata' }) {
  return (
    <div className="card flex items-start gap-3 p-4">
      {Icon ? (
        <span className={clsx('mt-0.5 flex size-9 items-center justify-center rounded-md bg-surface-2',
          tone === 'ok' && 'text-ok', tone === 'warn' && 'text-warn', tone === 'crit' && 'text-crit', tone === 'nodata' && 'text-nodata', !tone && 'text-accent')}>
          <Icon className="size-5" aria-hidden />
        </span>
      ) : null}
      <div className="min-w-0">
        <p className="text-2xs font-medium uppercase tracking-wide text-muted">{label}</p>
        <p className="num mt-0.5 text-2xl font-medium leading-tight">{value}</p>
        {sub ? <p className="mt-0.5 text-2xs text-muted">{sub}</p> : null}
      </div>
    </div>
  )
}

// ── Statuts (icône + libellé, jamais la couleur seule) ─────────────────────
type Tone = 'ok' | 'warn' | 'crit' | 'nodata'
const STATUS: Record<string, { label: string; tone: Tone; icon: LucideIcon }> = {
  HEALTHY: { label: 'Sain', tone: 'ok', icon: CheckCircle2 },
  ONLINE: { label: 'En ligne', tone: 'ok', icon: CheckCircle2 },
  WARNING: { label: 'Attention', tone: 'warn', icon: AlertTriangle },
  CRITICAL: { label: 'Critique', tone: 'crit', icon: OctagonAlert },
  ERROR: { label: 'Erreur', tone: 'crit', icon: OctagonAlert },
  OFFLINE: { label: 'Injoignable', tone: 'crit', icon: PowerOff },
  NO_DATA: { label: 'Sans données', tone: 'nodata', icon: CircleDashed },
  UNSYNCED: { label: 'En attente', tone: 'nodata', icon: Clock },
  AUTH_FAILED: { label: 'Identifiants refusés', tone: 'crit', icon: KeyRound },
  ARCHIVED: { label: 'Archivé', tone: 'nodata', icon: CircleDashed },
  NO_DATA_VM: { label: 'Sans données', tone: 'nodata', icon: CircleDashed },
}

export function statusTone(status: string): Tone {
  return STATUS[status]?.tone ?? 'nodata'
}

export function StatusBadge({ status, label, className }: { status: string; label?: string; className?: string }) {
  const s = STATUS[status] ?? { label: status, tone: 'nodata' as Tone, icon: CircleDashed }
  const Icon = s.icon
  return (
    <span className={clsx('inline-flex items-center gap-1.5 whitespace-nowrap rounded-full border px-2 py-0.5 text-2xs font-medium text-fg',
      s.tone === 'ok' && 'border-ok/40 bg-ok/10', s.tone === 'warn' && 'border-warn/50 bg-warn/10',
      s.tone === 'crit' && 'border-crit/50 bg-crit/10', s.tone === 'nodata' && 'border-border bg-surface-2', className)}>
      <Icon className={clsx('size-3.5', `text-${s.tone}`)} aria-hidden />
      {label ?? s.label}
    </span>
  )
}

export function SeverityBadge({ severity }: { severity: string }) {
  const map: Record<string, string> = { CRITICAL: 'CRITICAL', WARNING: 'WARNING', HEALTHY: 'HEALTHY', INFO: 'NO_DATA' }
  const label: Record<string, string> = { CRITICAL: 'Critique', WARNING: 'Attention', HEALTHY: 'Normal', INFO: 'Info' }
  return <StatusBadge status={map[severity] ?? 'NO_DATA'} label={label[severity] ?? severity} />
}

/** Jauge horizontale d'utilisation ; seuils 80 / 90 %. */
export function UsageBar({ value, className }: { value: number | null | undefined; className?: string }) {
  if (value === null || value === undefined) return <span className="text-muted">—</span>
  const v = Math.max(0, Math.min(100, value))
  const tone = v >= 90 ? 'bg-crit' : v >= 80 ? 'bg-warn' : 'bg-series-1'
  return (
    <div className={clsx('flex items-center gap-2', className)}>
      <div className="h-1.5 w-full min-w-[3rem] overflow-hidden rounded-full bg-surface-2" role="meter" aria-valuenow={v} aria-valuemin={0} aria-valuemax={100} aria-label="Utilisation">
        <div className={clsx('h-full rounded-full', tone)} style={{ width: `${v}%` }} />
      </div>
      <span className="num w-12 shrink-0 text-right text-2xs">{v.toLocaleString('fr-FR', { maximumFractionDigits: 1 })} %</span>
    </div>
  )
}

// ── États ───────────────────────────────────────────────────────────────────
export function Spinner({ label = 'Chargement…' }: { label?: string }) {
  return (
    <div className="flex items-center justify-center gap-2 py-10 text-sm text-muted" role="status">
      <Loader2 className="size-4 animate-spin" aria-hidden />{label}
    </div>
  )
}

export function EmptyState({ icon: Icon = CircleDashed, title, children }: { icon?: LucideIcon; title: string; children?: ReactNode }) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 px-4 py-10 text-center">
      <Icon className="size-8 text-muted" aria-hidden />
      <p className="text-sm font-medium">{title}</p>
      {children ? <div className="max-w-md text-sm text-muted">{children}</div> : null}
    </div>
  )
}

export function ErrorState({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  const message = error instanceof Error ? error.message : 'Erreur inattendue.'
  return (
    <div className="flex flex-col items-center gap-3 py-10 text-center" role="alert">
      <OctagonAlert className="size-8 text-crit" aria-hidden />
      <p className="text-sm">{message}</p>
      {onRetry ? <Button size="sm" onClick={onRetry}>Réessayer</Button> : null}
    </div>
  )
}

export function InlineError({ message }: { message: string | null }) {
  if (!message) return null
  return (
    <div className="flex items-start gap-2 rounded-md border border-crit/50 bg-crit/10 px-3 py-2 text-sm" role="alert">
      <OctagonAlert className="mt-0.5 size-4 shrink-0 text-crit" aria-hidden />
      <span>{message}</span>
    </div>
  )
}

// ── Fenêtre modale (élément <dialog> natif : focus piégé, Échap) ──────────
export function Modal({ open, onClose, title, children, footer, wide }: { open: boolean; onClose: () => void; title: string; children: ReactNode; footer?: ReactNode; wide?: boolean }) {
  const ref = useRef<HTMLDialogElement>(null)
  useEffect(() => {
    const d = ref.current
    if (!d) return
    if (open && !d.open) d.showModal()
    if (!open && d.open) d.close()
  }, [open])
  return (
    <dialog
      ref={ref}
      onClose={onClose}
      onCancel={(e) => { e.preventDefault(); onClose() }}
      aria-labelledby="modal-title"
      className={clsx('m-auto w-[calc(100%-2rem)] rounded-lg border border-border bg-surface p-0 text-fg shadow-2xl', wide ? 'max-w-4xl' : 'max-w-lg')}
    >
      {open ? (
        <div className="flex max-h-[85vh] flex-col">
          <header className="flex items-center justify-between border-b border-border px-5 py-3">
            <h2 id="modal-title" className="text-base font-semibold">{title}</h2>
            <IconButton icon={X} label="Fermer" onClick={onClose} />
          </header>
          <div className="overflow-y-auto px-5 py-4">{children}</div>
          {footer ? <footer className="flex justify-end gap-2 border-t border-border px-5 py-3">{footer}</footer> : null}
        </div>
      ) : null}
    </dialog>
  )
}

// ── Onglets ─────────────────────────────────────────────────────────────────
export function Tabs<T extends string>({ value, onChange, items }: { value: T; onChange: (v: T) => void; items: Array<{ value: T; label: string; count?: number }> }) {
  return (
    <div role="tablist" className="mb-4 flex gap-1 border-b border-border">
      {items.map((it) => (
        <button
          key={it.value}
          role="tab"
          type="button"
          aria-selected={value === it.value}
          onClick={() => onChange(it.value)}
          className={clsx('-mb-px flex h-10 cursor-pointer items-center gap-2 border-b-2 px-3 text-sm transition-colors',
            value === it.value ? 'border-accent font-medium text-fg' : 'border-transparent text-muted hover:text-fg')}
        >
          {it.label}
          {it.count !== undefined ? <span className="num rounded bg-surface-2 px-1.5 text-2xs">{it.count}</span> : null}
        </button>
      ))}
    </div>
  )
}

/** Segmented control (plages de temps). */
export function Segmented<T extends string | number>({ value, onChange, items, label }: { value: T; onChange: (v: T) => void; items: Array<{ value: T; label: string }>; label: string }) {
  return (
    <div role="radiogroup" aria-label={label} className="inline-flex rounded-md border border-border bg-bg p-0.5">
      {items.map((it) => (
        <button
          key={String(it.value)}
          type="button"
          role="radio"
          aria-checked={value === it.value}
          onClick={() => onChange(it.value)}
          className={clsx('h-7 cursor-pointer rounded px-2.5 text-2xs font-medium transition-colors',
            value === it.value ? 'bg-surface-2 text-fg' : 'text-muted hover:text-fg')}
        >
          {it.label}
        </button>
      ))}
    </div>
  )
}

/** Badge des équipements fictifs du mode démonstration. */
export function DemoBadge() {
  return (
    <span className="inline-flex items-center rounded border border-dashed border-muted px-1.5 py-px text-2xs font-medium uppercase tracking-wide text-muted"
      title="Équipement fictif du mode démonstration — données générées">
      Démo
    </span>
  )
}
