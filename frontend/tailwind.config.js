/** @type {import('tailwindcss').Config} */
const token = (name) => `rgb(var(--${name}) / <alpha-value>)`

export default {
  darkMode: ['class', '[data-theme="dark"]'],
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  safelist: ['text-ok', 'text-warn', 'text-crit', 'text-nodata'],
  theme: {
    extend: {
      colors: {
        bg: token('bg'),
        surface: token('surface'),
        'surface-2': token('surface-2'),
        border: token('border'),
        fg: token('fg'),
        'fg-2': token('fg-2'),
        muted: token('muted'),
        accent: token('accent'),
        'on-accent': token('on-accent'),
        ring: token('ring'),
        ok: token('ok'),
        warn: token('warn'),
        crit: token('crit'),
        nodata: token('nodata'),
        'series-1': token('series-1'),
        'series-2': token('series-2'),
      },
      fontFamily: {
        sans: ['"Fira Sans"', 'system-ui', 'sans-serif'],
        mono: ['"Fira Code"', 'ui-monospace', 'monospace'],
      },
      fontSize: {
        '2xs': ['0.75rem', { lineHeight: '1rem' }],
      },
      transitionDuration: { DEFAULT: '180ms' },
    },
  },
  plugins: [],
}
