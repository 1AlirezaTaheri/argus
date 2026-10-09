import * as React from 'react'
import { cva, type VariantProps } from 'class-variance-authority'
import {
  ArrowDownRight,
  ArrowUpRight,
  Minus,
  type LucideIcon,
} from 'lucide-react'
import { cn, formatNumber } from '@/lib/utils'
import { Card, Tooltip, TooltipContent, TooltipTrigger } from './ui'
import { Skeleton } from './feedback'

export interface KPICardProps {
  label: string
  value: number | string
  icon: LucideIcon
  trend?: number | null
  trendLabel?: string
  loading?: boolean
  className?: string
  /** Longer explanation, shown in a tooltip on the label. */
  hint?: string
  /** Extra row under the value, e.g. a status line. */
  footer?: React.ReactNode
}

/**
 * Icon colour per tone.
 *
 * This used to be a filled chip -- a 32px block of saturated background per
 * tile. Six of those across the top of the dashboard made the icons the
 * loudest thing on the page, louder than the numbers they were labelling, and
 * they carried a wall of colour that said "these are all alerts". Colouring the
 * glyph alone keeps the tone legible without the blocks.
 */
const toneVariants = cva('shrink-0', {
  variants: {
    tone: {
      neutral: 'text-faint',
      primary: 'text-primary',
      info: 'text-info',
      warning: 'text-warning',
      danger: 'text-danger',
    },
  },
  defaultVariants: { tone: 'primary' },
})

export function KPICard({
  label,
  value,
  icon: Icon,
  trend,
  trendLabel,
  loading = false,
  className,
  hint,
  footer,
  tone,
}: KPICardProps & VariantProps<typeof toneVariants>): JSX.Element {
  const hasTrend = typeof trend === 'number' && Number.isFinite(trend)
  const flat = hasTrend && Math.abs(trend ?? 0) < 0.5
  const up = hasTrend && !flat && (trend ?? 0) > 0
  const hasFooter = hasTrend || Boolean(trendLabel) || Boolean(footer)

  // The label sits on one line next to the icon. `stat-label` is 11px with wide
  // tracking rather than 12px: at six-across on a 1440px screen the tile is
  // about 148px of inner width, and the previous 12px/0.025em spec pushed
  // "CHANGES TODAY" past it, so it rendered as "CHANGES TOD...".
  //
  // Allowed to wrap rather than truncate. At two columns on a 390px phone the
  // tile is ~141px of inner width, which the longest label will not fit in one
  // line; wrapping costs a second line and keeps the words, where truncating
  // silently drops the "AY" off a count that then looks like a different metric.
  // Grid rows stretch, so one wrapped label does not leave the tiles uneven.
  const labelNode = (
    <p className="stat-label leading-tight">{label}</p>
  )

  return (
    // h-full so every tile in a grid row is the same height; mt-auto on the
    // footer pins the supporting line to the bottom, which is what keeps the
    // footers aligned across tiles with and without a trend.
    <Card className={cn('flex h-full flex-col p-4', className)}>
      <div className="flex items-center gap-1.5">
        <Icon className={cn('h-3.5 w-3.5', toneVariants({ tone }))} aria-hidden />
        {hint ? (
          // The trigger is the label itself, not a separate "?" affordance:
          // an extra icon is clutter for something only some readers need.
          <Tooltip>
            <TooltipTrigger asChild>{labelNode}</TooltipTrigger>
            <TooltipContent>{hint}</TooltipContent>
          </Tooltip>
        ) : (
          labelNode
        )}
      </div>

      <div className="mt-2.5">
        {loading ? (
          <Skeleton className="h-7 w-20" />
        ) : (
          <p className="stat-value">{typeof value === 'number' ? formatNumber(value) : value}</p>
        )}
      </div>

      {hasFooter ? (
        <div className="mt-auto space-y-1 pt-3 text-xs">
          {hasTrend || trendLabel ? (
            <div className="flex flex-wrap items-center gap-x-1.5">
              {hasTrend ? (
                <span
                  className={cn(
                    'inline-flex items-center gap-0.5 font-medium',
                    flat ? 'text-muted' : up ? 'text-success' : 'text-danger',
                  )}
                >
                  {flat ? (
                    <Minus className="h-3.5 w-3.5 shrink-0" aria-hidden />
                  ) : up ? (
                    <ArrowUpRight className="h-3.5 w-3.5 shrink-0" aria-hidden />
                  ) : (
                    <ArrowDownRight className="h-3.5 w-3.5 shrink-0" aria-hidden />
                  )}
                  {Math.abs(trend ?? 0).toFixed(0)}%
                </span>
              ) : null}
              {trendLabel ? <span className="text-muted">{trendLabel}</span> : null}
            </div>
          ) : null}
          {footer ? <div className="text-muted">{footer}</div> : null}
        </div>
      ) : null}
    </Card>
  )
}