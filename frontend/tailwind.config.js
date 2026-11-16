/** @type {import('tailwindcss').Config} */
export default {
  darkMode: 'class',
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        orange: {
          brand: '#FF7900',
          accessible: '#F16E00',
          blue: '#4BB4E6',
          'blue-dark': '#085EBD',
          green: '#50BE87',
          'green-dark': '#0A6E31',
          pink: '#FFB4E6',
          'pink-dark': '#FF8AD4',
          purple: '#A885D8',
          'purple-dark': '#492191',
          yellow: '#FFD200',
          'yellow-dark': '#FFB400',
        },
        // Semantic Theme colors for light/dark mode
        theme: {
          base: 'rgb(var(--bg-base) / <alpha-value>)',
          surface: 'rgb(var(--bg-surface) / <alpha-value>)',
          popover: 'rgb(var(--bg-popover) / <alpha-value>)',
          border: 'rgb(var(--border-color) / <alpha-value>)',
          text: 'rgb(var(--text-base) / <alpha-value>)',
          muted: 'rgb(var(--text-muted) / <alpha-value>)',
          accent: 'rgb(var(--accent-color) / <alpha-value>)',
        }
      },
      fontFamily: {
        sans: ['"Helvetica Neue"', 'Helvetica', 'Arial', 'sans-serif'],
        mono: ['"JetBrains Mono"', 'monospace'],
      }
    },
  },
  plugins: [],
}


