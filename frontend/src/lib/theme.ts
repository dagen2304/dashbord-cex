import { useCallback, useState } from 'react'

export type Theme = 'dark' | 'light'
const KEY = 'noc.theme'

export function initialTheme(): Theme {
  try {
    const saved = localStorage.getItem(KEY)
    if (saved === 'dark' || saved === 'light') return saved
  } catch {
    /* stockage indisponible */
  }
  // Un NOC travaille par défaut en thème sombre (écrans allumés 24 h/24).
  return 'dark'
}

export function applyTheme(theme: Theme) {
  document.documentElement.dataset.theme = theme
}

export function useTheme(): [Theme, () => void] {
  const [theme, setTheme] = useState<Theme>(() => (document.documentElement.dataset.theme as Theme) || 'dark')
  const toggle = useCallback(() => {
    setTheme((t) => {
      const next: Theme = t === 'dark' ? 'light' : 'dark'
      applyTheme(next)
      try {
        localStorage.setItem(KEY, next)
      } catch {
        /* rien */
      }
      return next
    })
  }, [])
  return [theme, toggle]
}
