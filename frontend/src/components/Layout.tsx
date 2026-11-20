import { useState, type ReactNode } from 'react'
import { NavLink, Outlet, useLocation } from 'react-router-dom'
import clsx from 'clsx'
import {
  Bell, Boxes, ChartLine, Gauge, HardDrive, LayoutDashboard, LogOut, Menu, Moon, Radar, Server, Settings, Sun, UserRound, X,
} from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import { ROLE_LABEL, useAuth } from '@/lib/auth'
import { useLiveUpdates, type LiveState } from '@/lib/live'
import { useTheme } from '@/lib/theme'
import type { Role } from '@/lib/types'

interface NavItem { to: string; label: string; icon: LucideIcon; role?: Role; end?: boolean }

const NAV: Array<{ section: string; items: NavItem[] }> = [
  {
    section: 'Supervision',
    items: [
      { to: '/', label: 'Vue d’ensemble', icon: LayoutDashboard, end: true },
      { to: '/alerts', label: 'Alertes', icon: Bell },
      { to: '/vms', label: 'Machines virtuelles', icon: Server },
      { to: '/applications', label: 'Applications', icon: Boxes },
    ],
  },
  {
    section: 'Analyse',
    items: [
      { to: '/vrealize', label: 'vRealize', icon: Radar },
      { to: '/capacity', label: 'Capacité', icon: Gauge },
      { to: '/explorer', label: 'Explorateur', icon: ChartLine },
    ],
  },
  {
    section: 'Administration',
    items: [
      { to: '/inventory', label: 'Inventaire', icon: HardDrive },
      { to: '/settings', label: 'Paramètres', icon: Settings, role: 'admin' },
    ],
  },
]

const LIVE: Record<LiveState, { label: string; cls: string }> = {
  live: { label: 'Temps réel', cls: 'bg-ok' },
  connecting: { label: 'Connexion…', cls: 'bg-warn' },
  offline: { label: 'Hors ligne — rafraîchissement périodique', cls: 'bg-nodata' },
}

function Sidebar({ onNavigate }: { onNavigate?: () => void }) {
  const { can } = useAuth()
  return (
    <nav aria-label="Navigation principale" className="flex flex-col gap-5 px-3 py-4">
      {NAV.map((group) => (
        <div key={group.section}>
          <p className="px-2 pb-1.5 text-2xs font-medium uppercase tracking-wider text-muted">{group.section}</p>
          <ul className="flex flex-col gap-0.5">
            {group.items.filter((i) => !i.role || can(i.role)).map((item) => (
              <li key={item.to}>
                <NavLink
                  to={item.to}
                  end={item.end}
                  onClick={onNavigate}
                  className={({ isActive }) => clsx(
                    'flex h-9 items-center gap-2.5 rounded-md px-2.5 text-sm transition-colors',
                    isActive ? 'bg-surface-2 font-medium text-fg shadow-[inset_2px_0_0_rgb(var(--accent))]' : 'text-fg-2 hover:bg-surface-2/60 hover:text-fg',
                  )}
                >
                  <item.icon className="size-4 shrink-0" aria-hidden />
                  {item.label}
                </NavLink>
              </li>
            ))}
          </ul>
        </div>
      ))}
    </nav>
  )
}

function Brand() {
  return (
    <div className="flex items-center gap-2.5">
      <span className="flex size-8 items-center justify-center rounded-md bg-accent font-mono text-sm font-bold text-on-accent" aria-hidden>N</span>
      <div className="leading-tight">
        <p className="text-sm font-semibold">NOC INFRA</p>
        <p className="text-2xs text-muted">Supervision centralisée</p>
      </div>
    </div>
  )
}

export function Layout() {
  const { user, logout } = useAuth()
  const [theme, toggleTheme] = useTheme()
  const live = useLiveUpdates(Boolean(user))
  const [menuOpen, setMenuOpen] = useState(false)
  const location = useLocation()

  return (
    <div className="flex min-h-dvh">
      <a href="#main" className="sr-only focus:not-sr-only focus:fixed focus:left-2 focus:top-2 focus:z-50 focus:rounded focus:bg-accent focus:px-3 focus:py-2 focus:text-on-accent">
        Aller au contenu
      </a>

      {/* Menu latéral (bureau) */}
      <aside className="sticky top-0 hidden h-dvh w-60 shrink-0 flex-col border-r border-border bg-surface lg:flex">
        <div className="border-b border-border px-4 py-3.5"><Brand /></div>
        <div className="flex-1 overflow-y-auto"><Sidebar /></div>
      </aside>

      {/* Menu latéral (mobile) */}
      {menuOpen ? (
        <div className="fixed inset-0 z-40 lg:hidden">
          <button type="button" aria-label="Fermer le menu" className="absolute inset-0 bg-black/60" onClick={() => setMenuOpen(false)} />
          <aside className="absolute inset-y-0 left-0 w-64 overflow-y-auto border-r border-border bg-surface">
            <div className="flex items-center justify-between border-b border-border px-4 py-3">
              <Brand />
              <button type="button" aria-label="Fermer le menu" className="flex size-9 items-center justify-center rounded-md hover:bg-surface-2" onClick={() => setMenuOpen(false)}>
                <X className="size-4" aria-hidden />
              </button>
            </div>
            <Sidebar onNavigate={() => setMenuOpen(false)} />
          </aside>
        </div>
      ) : null}

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="sticky top-0 z-30 flex h-14 items-center gap-3 border-b border-border bg-bg/90 px-4 backdrop-blur">
          <button type="button" aria-label="Ouvrir le menu" className="flex size-9 items-center justify-center rounded-md hover:bg-surface-2 lg:hidden" onClick={() => setMenuOpen(true)}>
            <Menu className="size-5" aria-hidden />
          </button>
          <div className="lg:hidden"><Brand /></div>

          <div className="ml-auto flex items-center gap-1 sm:gap-3">
            <span className="hidden items-center gap-2 text-2xs text-muted sm:flex" role="status" aria-live="polite">
              <span className={clsx('size-2 rounded-full', LIVE[live].cls, live === 'live' && 'animate-pulse')} aria-hidden />
              {LIVE[live].label}
            </span>
            <button
              type="button"
              onClick={toggleTheme}
              aria-label={theme === 'dark' ? 'Passer au thème clair' : 'Passer au thème sombre'}
              title={theme === 'dark' ? 'Thème clair' : 'Thème sombre'}
              className="flex size-9 cursor-pointer items-center justify-center rounded-md text-fg-2 hover:bg-surface-2 hover:text-fg"
            >
              {theme === 'dark' ? <Sun className="size-4" aria-hidden /> : <Moon className="size-4" aria-hidden />}
            </button>
            <NavLink to="/account" className="flex h-9 items-center gap-2 rounded-md px-2 text-sm hover:bg-surface-2" title="Mon compte">
              <UserRound className="size-4 text-fg-2" aria-hidden />
              <span className="hidden max-w-[10rem] truncate sm:inline">{user?.full_name || user?.username}</span>
              <span className="hidden rounded bg-surface-2 px-1.5 py-0.5 text-2xs text-muted md:inline">{user ? ROLE_LABEL[user.role] : ''}</span>
            </NavLink>
            <button type="button" onClick={logout} aria-label="Se déconnecter" title="Se déconnecter"
              className="flex size-9 cursor-pointer items-center justify-center rounded-md text-fg-2 hover:bg-surface-2 hover:text-fg">
              <LogOut className="size-4" aria-hidden />
            </button>
          </div>
        </header>

        <main id="main" key={location.pathname} className="mx-auto w-full max-w-[1600px] flex-1 px-4 py-5 sm:px-6">
          <Outlet />
        </main>
      </div>
    </div>
  )
}

export function RequireRole({ role, children }: { role: Role; children: ReactNode }) {
  const { can } = useAuth()
  if (!can(role)) {
    return (
      <div className="card mx-auto mt-10 max-w-md p-6 text-center">
        <p className="font-medium">Accès réservé</p>
        <p className="mt-1 text-sm text-muted">Cette page nécessite le rôle « {ROLE_LABEL[role]} ».</p>
      </div>
    )
  }
  return <>{children}</>
}
