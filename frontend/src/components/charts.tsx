/**
 * Graphiques (règles du skill dataviz) :
 * - un seul axe Y, traits fins (2 px), grille discrète ;
 * - couleurs de séries validées, jamais les couleurs de statut ;
 * - infobulle à réticule au survol ; légende dès 2 séries ;
 * - seuils dessinés en pointillés et nommés (pas la couleur seule).
 */
import {
  Area, AreaChart, CartesianGrid, Legend, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts'
import { fmt, shortTime } from '@/lib/format'

const C = {
  s1: 'rgb(var(--series-1))',
  s2: 'rgb(var(--series-2))',
  grid: 'rgb(var(--border) / 0.6)',
  axis: 'rgb(var(--muted))',
  crit: 'rgb(var(--crit))',
  warn: 'rgb(var(--warn))',
}
const SERIES = [C.s1, C.s2]

export interface Series {
  key: string
  label: string
}

interface TimeSeriesProps {
  data: Array<{ timestamp: string }>
  series: Series[]
  unit?: string
  digits?: number
  height?: number
  yMax?: number
  thresholds?: Array<{ value: number; label: string; tone: 'warn' | 'crit' }>
  spanHours: number
  ariaLabel: string
}

function TooltipBox({ active, payload, label, unit, digits }: { active?: boolean; payload?: Array<{ name: string; value: number; color: string }>; label?: string; unit?: string; digits: number }) {
  if (!active || !payload?.length || !label) return null
  return (
    <div className="rounded-md border border-border bg-surface px-3 py-2 text-2xs shadow-lg">
      <p className="mb-1 text-muted">{new Date(label).toLocaleString('fr-FR', { dateStyle: 'short', timeStyle: 'short' })}</p>
      {payload.map((p) => (
        <p key={p.name} className="flex items-center gap-2 text-fg">
          <span className="inline-block h-0.5 w-3 rounded" style={{ background: p.color }} aria-hidden />
          <span className="text-fg-2">{p.name}</span>
          <span className="num ml-auto pl-3 font-medium">{fmt(p.value, digits, unit)}</span>
        </p>
      ))}
    </div>
  )
}

export function TimeSeriesChart({ data, series, unit, digits = 1, height = 260, yMax, thresholds = [], spanHours, ariaLabel }: TimeSeriesProps) {
  return (
    <div role="img" aria-label={ariaLabel} style={{ height }}>
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={data} margin={{ top: 8, right: 16, bottom: 0, left: 0 }}>
          <CartesianGrid stroke={C.grid} strokeDasharray="0" vertical={false} />
          <XAxis
            dataKey="timestamp"
            tickFormatter={(v: string) => shortTime(v, spanHours)}
            stroke={C.axis}
            tick={{ fontSize: 11, fill: C.axis }}
            tickLine={false}
            axisLine={{ stroke: C.grid }}
            minTickGap={40}
          />
          <YAxis
            stroke={C.axis}
            tick={{ fontSize: 11, fill: C.axis }}
            tickLine={false}
            axisLine={false}
            width={48}
            domain={[0, yMax ?? 'auto']}
            allowDecimals={digits > 0}
            tickFormatter={(v: number) => fmt(v, Number.isInteger(v) ? 0 : 1)}
            unit={unit === '%' ? '%' : undefined}
          />
          <Tooltip
            content={<TooltipBox unit={unit} digits={digits} />}
            cursor={{ stroke: C.axis, strokeWidth: 1, strokeDasharray: '3 3' }}
            isAnimationActive={false}
          />
          {series.length > 1 ? <Legend iconType="plainline" wrapperStyle={{ fontSize: 12, color: 'rgb(var(--fg-2))' }} /> : null}
          {thresholds.map((t) => (
            <ReferenceLine
              key={t.label}
              y={t.value}
              stroke={t.tone === 'crit' ? C.crit : C.warn}
              strokeDasharray="4 4"
              label={{ value: t.label, position: t.tone === 'crit' ? 'insideTopRight' : 'insideBottomRight', fill: 'rgb(var(--fg-2))', fontSize: 11 }}
            />
          ))}
          {series.map((s, i) => (
            <Line
              key={s.key}
              type="monotone"
              dataKey={s.key}
              name={s.label}
              stroke={SERIES[i % SERIES.length]}
              strokeWidth={2}
              dot={false}
              activeDot={{ r: 4, strokeWidth: 2, stroke: 'rgb(var(--surface))' }}
              connectNulls={false}
              isAnimationActive={false}
            />
          ))}
        </LineChart>
      </ResponsiveContainer>
    </div>
  )
}

/** Mini-courbe d'une carte d'équipement (une seule série, sans axes). */
export function Sparkline({ points, label }: { points: Array<{ timestamp: string; value: number }>; label: string }) {
  if (points.length < 2) {
    return <div className="flex h-10 items-center text-2xs text-muted">Historique insuffisant</div>
  }
  const last = points[points.length - 1]
  return (
    <div className="h-10" role="img" aria-label={`${label} sur 6 heures, dernière valeur ${fmt(last.value)}`}>
      <ResponsiveContainer width="100%" height="100%">
        <AreaChart data={points} margin={{ top: 2, right: 0, bottom: 2, left: 0 }}>
          <defs>
            <linearGradient id="spark" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={C.s1} stopOpacity={0.25} />
              <stop offset="100%" stopColor={C.s1} stopOpacity={0} />
            </linearGradient>
          </defs>
          <YAxis hide domain={['auto', 'auto']} />
          <Area type="monotone" dataKey="value" stroke={C.s1} strokeWidth={1.5} fill="url(#spark)" isAnimationActive={false} />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  )
}
