import * as React from 'react'
import type { TooltipProps } from 'recharts'

/**
 * Shared chart configuration.
 *
 * These lived inline in both dashboard.tsx and target-detail.tsx, which meant
 * the same tooltip and axis styling had two copies that could drift -- and
 * fixing the 30-day axis crowding in one page would have left the other page
 * wrong. Recharts does not accept class names, so these have to be style
 * objects, which is why they are values rather than a component.
 */

/**
 * Axis tick text. 11px --muted on the theme's background. Deliberately muted:
 * the axis is a ruler for the data, not content, and it competes with the series
 * for attention at every size otherwise.
 */
export const AXIS_TICK = { fontSize: 11, fill: 'rgb(var(--muted))' } as const

export const AXIS_LINE = 'rgb(var(--border))' as const

/**
 * Grid lines: horizontal only. Vertical grid lines cut through the series and
 * add ink without adding information, because the x position is already carried
 * by the tick labels.
 */
export const GRID = {
  stroke: AXIS_LINE,
  strokeDasharray: '3 3',
  vertical: false,
} as const

export const CHART_TOOLTIP: TooltipProps<number, string> = {
  contentStyle: {
    background: 'rgb(var(--elevated))',
    border: '1px solid rgb(var(--border-strong))',
    borderRadius: '0.5rem',
    boxShadow: 'var(--shadow-lg)',
    fontSize: 12,
    color: 'rgb(var(--fg))',
  },
  labelStyle: { color: 'rgb(var(--fg))', fontWeight: 600, marginBottom: 4 },
  itemStyle: { color: 'rgb(var(--muted))', padding: 0 },
  cursor: { stroke: 'rgb(var(--border-strong))', strokeWidth: 1 },
}

/**
 * Series colours.
 *
 * Separate from the status palette. `--primary` and `--success` are both green
 * and mean "the accent" and "this worked"; a chart needs hues that are only
 * distinguishable from one another, so it reads its own steps. Each clears 3:1
 * against its own theme surface, which is what a 2px stroke needs to stay
 * legible.
 */
export const SERIES = {
  scans: 'rgb(var(--chart-1))',
  // Not a chart step. A "failed scans" line is an error count, and the whole
  // point of drawing it in red is that the reader recognises it without reading
  // the legend -- this was briefly a chart step and rendered pink, which reads
  // as decorative rather than wrong. Semantic colour wins over palette
  // consistency here.
  failed: 'rgb(var(--danger))',
  changes: 'rgb(var(--chart-3))',
  hosts: 'rgb(var(--chart-2))',
  services: 'rgb(var(--chart-4))',
} as const

/**
 * The dash patterns paired with SERIES, in the same order.
 *
 * Every chart here draws more than one series, and colour alone is not enough:
 * a green and a red line are the classic deuteranopia collision, and "failed
 * scans" is precisely the line a reader must not misread as "scans". Each series
 * therefore carries a distinct dash as well as a distinct hue, so the charts
 * stay readable without colour vision and still print correctly in greyscale.
 */
export const SERIES_DASH = {
  scans: '0',
  failed: '1 3',
  changes: '4 3',
} as const

/**
 * Choose which x positions get a label.
 *
 * Recharts thins labels on its own, and for a 30- or 90-point daily series it
 * thinned badly: the label for 09-23 was dropped from the middle, leaving a gap
 * in the axis that read as missing data. Choosing the positions explicitly --
 * evenly spaced, always including the first and last -- means the axis reads as
 * a deliberate scale instead of a series with a hole in it.
 *
 * Returns indices, which the caller maps through its own data.
 */
export function tickIndices(count: number, max = 7): number[] {
  if (count <= 0) return []
  if (count <= max) return Array.from({ length: count }, (_, i) => i)
  const step = Math.ceil((count - 1) / (max - 1))
  const out: number[] = []
  for (let i = 0; i < count; i += step) out.push(i)
  const last = count - 1
  if (out[out.length - 1] !== last) out.push(last)
  return out
}

/** Map a series of ISO dates to the values `ticks` should be given. */
export function dateTicks(dates: readonly string[], max = 7): string[] {
  return tickIndices(dates.length, max)
    .map((i) => dates[i])
    .filter((d): d is string => typeof d === 'string')
}

/**
 * `MM-DD`, the form the API emits dates in.
 *
 * Used for every window because it stays unambiguous across a 90-day range,
 * where a day-of-month-only label would repeat three times with no indication of
 * which month it belongs to.
 */
export const shortDate = (value: string): string => value.slice(5)

/**
 * Measure a container so tick density can follow the available width.
 *
 * Recharts knows its own width but will not tell the caller, and its own tick
 * thinning drops labels from the middle -- which on the 30-day axis produced a
 * gap that read as missing data. So the ticks are chosen here, and the only
 * thing that has to come from outside is how many of them fit.
 *
 * This returns a *callback* ref, not a RefObject, and that detail is the whole
 * point. The element being measured only exists once the query resolves -- the
 * chart's own body renders a skeleton until then -- so an effect with `[]` deps
 * runs while `ref.current` is still null, never observes anything, and never
 * runs again. The width stays 0, `ticksForWidth` falls back to the wide
 * default, and the axis stays crowded on a phone no matter what the breakpoint
 * table says. A callback ref fires when the node actually attaches.
 *
 * Width 0 therefore means "not measured yet", and the callers treat it as "use
 * the wide default" so the first paint is never sparse.
 */
export function useChartWidth<T extends HTMLElement>(): {
  ref: (node: T | null) => void
  width: number
} {
  const [width, setWidth] = React.useState(0)
  const observer = React.useRef<ResizeObserver | null>(null)

  const ref = React.useCallback((node: T | null): void => {
    observer.current?.disconnect()
    observer.current = null
    if (!node) return

    const measure = (w: number): void =>
      setWidth((prev) => (Math.abs(prev - w) > 1 ? w : prev))

    measure(node.getBoundingClientRect().width)
    if (typeof ResizeObserver === 'undefined') return

    const ro = new ResizeObserver((entries) => {
      const w = entries[0]?.contentRect.width
      if (typeof w === 'number') measure(w)
    })
    ro.observe(node)
    observer.current = ro
  }, [])

  // Detach if the component unmounts while the node is still attached.
  React.useEffect(() => () => observer.current?.disconnect(), [])

  return { ref, width }
}

/** How many date ticks fit at a given width. */
export function ticksForWidth(width: number, seriesLength: number): number {
  // Unmeasured yet: fall back to the widest layout so nothing is lost on the
  // first paint, and tighten once the real width is known.
  if (width <= 0) return Math.min(seriesLength, 7)
  if (width < 420) return 4
  if (width < 640) return 5
  return Math.min(seriesLength, 7)
}

/**
 * Change-event types.
 *
 * Unlike SERIES these *do* carry meaning, so they borrow the status palette:
 * a newly discovered host and a closed port are not interchangeable, and
 * reusing the chart steps here would break that association for no gain.
 *
 * `new_host` uses --success rather than --primary on purpose. Both are green,
 * but --primary means "the accent colour" while --success means "this worked",
 * and a change-event legend that looked like a set of buttons was ambiguous.
 */
export const CHANGE_COLORS: Record<string, string> = {
  new_host: 'rgb(var(--success))',
  new_port: 'rgb(var(--info))',
  service_change: 'rgb(var(--warning))',
  closed_port: 'rgb(var(--danger))',
  closed_host: 'rgb(var(--muted))',
}

export const CHANGE_LABELS: Record<string, string> = {
  new_host: 'New host',
  new_port: 'New port',
  service_change: 'Service changed',
  closed_port: 'Closed port',
  closed_host: 'Closed host',
}