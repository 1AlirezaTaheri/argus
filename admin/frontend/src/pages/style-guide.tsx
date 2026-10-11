import * as React from 'react'
import { useQuery } from '@tanstack/react-query'
import { Copy, Info, Moon, Network, Sun } from 'lucide-react'
import { toast } from 'sonner'
import { api } from '@/lib/api'
import { useUIStore } from '@/store/ui'
import { cn, formatNumber } from '@/lib/utils'
import {
  Accordion,
  Badge,
  Button,
  Card,
  Checkbox,
  Popover,
  PopoverContent,
  PopoverTrigger,
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
  Tabs,
  TabsContent,
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from '@/components/ui'
import { DataTable, Pagination, type Column, type SortSpec } from '@/components/data-table'
import { KPICard } from '@/components/kpi-card'
import {
  CardSkeleton,
  EmptyState,
  ErrorState,
  Skeleton,
  Spinner,
  StatusDot,
  TableSkeleton,
} from '@/components/feedback'

interface Row {
  id: number
  name: string
  value: string
  group: string | null
  enabled: boolean
  scans: number
}

/**
 * Style guide.
 *
 * Not decoration. A component that no page imports is tree-shaken out of the
 * bundle, so it cannot be exercised in a browser at all -- which is how the
 * Phase 1 verification found `Tabs` missing from the deployed bundle while the
 * source plainly exported it. This route imports every new component, so they
 * are built, shipped and inspectable, and a regression in any of them shows up
 * here rather than in production.
 */
export function StyleGuidePage(): JSX.Element {
  const theme = useUIStore((s) => s.theme)
  const toggleTheme = useUIStore((s) => s.toggleTheme)
  const sidebarCollapsed = useUIStore((s) => s.sidebarCollapsed)
  const toggleSidebar = useUIStore((s) => s.toggleSidebar)

  const [sortSpecs, setSortSpecs] = React.useState<readonly SortSpec[]>([
    { key: 'scans', direction: 'desc' },
  ])
  const [selected, setSelected] = React.useState<ReadonlySet<string | number>>(new Set())
  const [page, setPage] = React.useState(1)

  const { data } = useQuery({
    queryKey: ['targets'],
    queryFn: ({ signal }) => api.listTargets(signal),
  })

  const rows: Row[] = React.useMemo(
    () =>
      (data?.targets ?? []).map((target, index) => ({
        id: target.id,
        name: target.name,
        value: target.value,
        group: target.group,
        enabled: target.enabled === 1,
        scans: ((index * 37) % 90) + 3,
      })),
    [data],
  )

  const columns: Column<Row>[] = [
    { key: 'name', header: 'Name', cell: (row) => <span className="font-medium">{row.name}</span>,
      sortValue: (row) => row.name, sortable: true, resizable: true, width: 160 },
    { key: 'value', header: 'Value', cell: (row) => <span className="font-mono text-xs">{row.value}</span>,
      sortValue: (row) => row.value, sortable: true, resizable: true },
    { key: 'group', header: 'Group', cell: (row) => (row.group ?? <span className="text-faint">—</span>),
      sortValue: (row) => row.group ?? '', resizable: true, hidden: true },
    { key: 'enabled', header: 'Enabled', cell: (row) => (
        <Badge variant={row.enabled ? 'success' : 'muted'}>{row.enabled ? 'yes' : 'no'}</Badge>
      ), sortValue: (row) => (row.enabled ? 1 : 0), sortable: true },
    { key: 'scans', header: 'Scans', cell: (row) => <span className="tabular-nums">{formatNumber(row.scans)}</span>,
      sortValue: (row) => row.scans, sortable: true, resizable: true, keepVisible: true },
  ]

  function Section({
    title,
    description,
    children,
  }: {
    title: string
    description?: string
    children: React.ReactNode
  }): JSX.Element {
    return (
      <section className="space-y-3">
        <div>
          <h2 className="text-sm font-semibold text-fg">{title}</h2>
          {description ? <p className="text-xs text-muted">{description}</p> : null}
        </div>
        {children}
      </section>
    )
  }

  return (
    <div className="space-y-8">
      <Card variant="bordered" className="p-4">
        <div className="flex flex-wrap items-center gap-2">
          <Button variant="default" size="default">Default</Button>
          <Button variant="secondary">Secondary</Button>
          <Button variant="outline">Outline</Button>
          <Button variant="ghost">Ghost</Button>
          <Button variant="danger-soft">Danger (tinted)</Button>
          <Button variant="danger">Danger</Button>
          <Button variant="link">Link</Button>
          <Button size="sm">Small</Button>
          <Button size="lg">Large</Button>
          <Button size="icon" aria-label="Icon only"><Network className="h-4 w-4" /></Button>
          <Button disabled>Disabled</Button>
        </div>
      </Card>

      <Section
        title="Chart series palette"
        description="distinct from the status colours; every series also carries its own dash"
      >
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {(
            [
              ['chart-1', 'scans'],
              ['chart-2', 'hosts'],
              ['chart-3', 'changes'],
              ['chart-4', 'services'],
              ['chart-5', 'reserved'],
              ['danger', 'failed'],
            ] as const
          ).map(([token, use]) => (
            <div key={token} className="flex items-center gap-3 rounded-md border border-border bg-surface p-3">
              {/* The dash is drawn as an SVG so the guide shows the actual
                  encoding a chart uses, not a rectangle. */}
              <svg width="44" height="12" aria-hidden className="shrink-0">
                <line
                  x1="1"
                  y1="6"
                  x2="43"
                  y2="6"
                  stroke={`rgb(var(--${token}))`}
                  strokeWidth="2"
                  strokeDasharray={
                    token === 'danger' ? '1 3' : token === 'chart-3' ? '4 3' : '0'
                  }
                />
              </svg>
              <div className="min-w-0">
                <p className="text-sm font-medium">--{token}</p>
                <p className="text-xs text-muted">{use}</p>
              </div>
            </div>
          ))}
        </div>
      </Section>

      <Section
        title="Depth"
        description="Three elevations, one floating surface, one well"
      >
        <p className="mb-3 max-w-3xl text-sm text-muted">
          A drop shadow alone does not read as elevation on a near-black page:
          there is no light left to darken. Each level therefore pairs the
          shadow with an inset highlight along the top edge, which is the
          surface turning away from a light above it. The highlight is
          deliberately only on the top -- the same line along the bottom reads
          as a glow rather than as height.
        </p>
        <div className="grid gap-4 lg:grid-cols-2">
          {/* The three levels, overlapping.

              Side by side they are indistinguishable, which is the honest
              result -- the difference between them is mostly a soft halo that
              has nothing to fall on when each card is alone on the page. This
              is the arrangement that actually shows elevation: each panel's
              shadow lands on the panel behind it, and the lit top edge reads
              against the darker surface beneath. A grid of three equal tiles
              demonstrates nothing.
          */}
          <div className="relative flex min-h-[15rem] flex-col justify-end gap-3 rounded-lg border border-border bg-bg p-6">
            <div className="absolute right-3 top-3 text-[11px] text-faint">
              overlapping, so each shadow lands on the one behind
            </div>
            <div className="rounded-lg border border-border bg-surface p-4 shadow-elevate-3">
              <p className="text-xs text-muted">elevate-3</p>
              <p className="mt-1 text-sm">Login panel, dialogs</p>
            </div>
            <div className="rounded-lg border border-border bg-surface p-4 shadow-elevate">
              <p className="text-xs text-muted">elevate-2</p>
              <p className="mt-1 text-sm">Hover on an interactive card</p>
            </div>
            <div className="rounded-lg border border-border bg-surface p-4 shadow-elevate-1">
              <p className="text-xs text-muted">elevate-1</p>
              <p className="mt-1 text-sm">Cards, table panels</p>
            </div>
          </div>

          <div className="flex flex-col gap-3">
            <div className="surface-raised p-4">
              <p className="text-xs text-muted">surface-raised</p>
              <p className="mt-1 text-sm">
                A panel floating free of the page. The sheen is the second cue
                and the one that still works where a shadow has no contrast to
                work with.
              </p>
            </div>
            <div className="inset-well rounded-lg border border-input bg-bg p-4">
              <p className="text-xs text-muted">inset-well</p>
              <p className="mt-1 text-sm">
                Pressed in, not resting on. Inputs and read-only panels. An
                input that casts a shadow outward reads as a card with text on
                it, which is the wrong affordance.
              </p>
            </div>
            <div className="rounded-lg border border-border bg-surface p-4 shadow-elevate-1">
              <p className="text-xs text-muted">and in the light theme</p>
              <p className="mt-1 text-sm text-muted">
                Flip the theme and compare: the bevel inverts to a faint dark
                wash, because white on white is invisible and the shadow has to
                do the work instead.
              </p>
            </div>
          </div>
        </div>
        <p className="mt-3 max-w-3xl text-xs text-faint">
          Every transform these imply — the hover lift, the button press, the
          rejected-login shake — is removed under{' '}
          <code className="font-mono">prefers-reduced-motion</code>, not merely
          shortened. The elevation change survives, so the affordance is not
          lost with the movement.
        </p>
      </Section>

      <Section title="Card variants" description="default, bordered, elevated, interactive">
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <Card variant="default" className="p-4">
            <p className="text-xs text-muted">default</p>
            <p className="mt-1 text-sm">Flat, no edge</p>
          </Card>
          <Card variant="bordered" className="p-4">
            <p className="text-xs text-muted">bordered</p>
            <p className="mt-1 text-sm">Explicit edge</p>
          </Card>
          <Card variant="elevated" className="p-4">
            <p className="text-xs text-muted">elevated</p>
            <p className="mt-1 text-sm">Lifted</p>
          </Card>
          <Card
            variant="interactive"
            as="button"
            // shadow-glow is the theme's own focus treatment, applied on
            // hover and focus. This is its only use in the app, which is
            // exactly why the token check looks for it: an unreferenced
            // token is dead config that reads as coverage and provides none.
            className="p-4 hover:shadow-glow focus-visible:shadow-glow"
            onClick={() => toast.success('Interactive card pressed')}
          >
            <p className="text-xs text-muted">interactive</p>
            <p className="mt-1 text-sm">Hover and focus me</p>
          </Card>
        </div>
      </Section>

      <Section title="Badges" description="primary green is now distinct from success green">
        <div className="flex flex-wrap items-center gap-2">
          <Badge>default</Badge>
          <Badge variant="muted">muted</Badge>
          <Badge variant="success">success</Badge>
          <Badge variant="info">info</Badge>
          <Badge variant="warning">warning</Badge>
          <Badge variant="danger">danger</Badge>
        </div>
      </Section>

      <Section title="Stat tiles" description="icon, value, trend, hint tooltip, footer">
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <KPICard label="With trend up" value={1234} icon={Network} trend={12.5} trendLabel="vs yesterday"
            hint="Total targets registered. Help text appears here." />
          <KPICard label="With trend down" value={87} icon={Network} trend={-4.2} trendLabel="vs yesterday"
            tone="danger" />
          <KPICard label="Flat trend" value={512} icon={Network} trend={0.2} trendLabel="vs yesterday"
            tone="info" />
          <KPICard label="No trend" value={9} icon={Network} trendLabel="registered" footer="footer line"
            tone="warning" />
        </div>
      </Section>

      {/* Marked so the render check can tell a real loading state from a specimen.
          These skeletons are the *content* of this section and never resolve, so
          without the marker tools/verify-render.sh reports the style guide as a
          route whose data never arrived -- which is exactly what it should say
          for any other page. */}
      <div data-skeleton-demo="true">
        <Section title="Skeleton variants" description="line, circle, card, tableRow">
          <div className="flex flex-wrap items-center gap-3">
            <Skeleton className="w-40" />
            <Skeleton variant="circle" />
            <Skeleton variant="card" className="w-40" />
          </div>
          <TableSkeleton rows={2} cols={3} />
          <CardSkeleton count={2} />
        </Section>
      </div>

      <Section title="Empty and error states">
        <div className="grid gap-3 lg:grid-cols-2">
          <Card>
            <EmptyState title="No targets yet" description="Add a target to start scanning."
              actionLabel="Add target" onAction={() => toast.success('Would open the target dialog')} />
          </Card>
          <Card>
            <ErrorState description="Could not load data." onRetry={() => toast.info('Retrying')} />
          </Card>
        </div>
      </Section>

      <Section
        title="Select"
        description="Radix. Type-ahead, Home/End, Escape, and a themed popup"
      >
        <div className="flex flex-wrap items-end gap-3">
          <div className="flex flex-col gap-1">
            <label htmlFor="sg-select" className="stat-label">
              A choice
            </label>
            <Select defaultValue="b">
              <SelectTrigger id="sg-select" className="w-56">
                <SelectValue placeholder="Pick one" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="a">First option</SelectItem>
                <SelectItem value="b">Second option</SelectItem>
                <SelectItem value="c" disabled>
                  Third option (disabled)
                </SelectItem>
              </SelectContent>
            </Select>
          </div>
          <div className="flex flex-col gap-1">
            <span className="stat-label">Disabled</span>
            <Select defaultValue="a" disabled>
              <SelectTrigger aria-label="A disabled select" className="w-56">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="a">Cannot be changed</SelectItem>
              </SelectContent>
            </Select>
          </div>
        </div>
        <p className="mt-3 text-xs text-muted">
          Every dropdown in the panel uses this. Five native select elements were
          left in place &mdash; the audit filters, the settings controls and the
          table's page-size picker &mdash; and each one opened the operating
          system's own popup list, which cannot be themed and ignored the panel's
          focus ring. The page-size picker sits inside the DataTable demo below,
          so it was the one mismatch a reader of this page could not avoid.
        </p>
      </Section>

      <Section
        title="Tabs"
        description="Radix. Arrow keys, Home/End. The third tab is disabled: dimmed, and it does not respond."
      >
        <Tabs items={[{ value: 'overview', label: 'Overview', count: 12 }, { value: 'hosts', label: 'Hosts' }, { value: 'ports', label: 'Ports (disabled)', disabled: true }]}>
          <TabsContent value="overview" className="py-3 text-sm text-muted">Overview panel</TabsContent>
          <TabsContent value="hosts" className="py-3 text-sm text-muted">Hosts panel</TabsContent>
        </Tabs>
        <p className="mt-2 text-xs text-muted">
          A disabled tab has no panel, so clicking it does nothing. Its label says
          so, because a tab that silently ignores clicks reads as broken.
        </p>
      </Section>

      <Section title="Accordion" description="disclosure with a height transition, no measurement">
        <Card className="p-4">
          <Accordion
            defaultOpen={['a']}
            items={[
              { value: 'a', title: 'How is the colour scale defined?', content: 'Semantic tokens in index.css, mapped by tailwind.config.js. A theme flip is one attribute change on <html>.' },
              { value: 'b', title: 'What does interactive do?', content: 'Adds hover and focus affordances. Only valid when the card is a button or a link.' },
            ]}
          />
        </Card>
      </Section>

      <Section title="Tooltip and Popover" description="Radix, one shared provider">
        <div className="flex flex-wrap items-center gap-2">
          <Tooltip>
            <TooltipTrigger asChild><Button variant="outline">Hover me</Button></TooltipTrigger>
            <TooltipContent>Tooltip content</TooltipContent>
          </Tooltip>
          <Popover>
            <PopoverTrigger asChild><Button variant="outline">Open popover</Button></PopoverTrigger>
            <PopoverContent className="space-y-2">
              <p className="text-sm font-medium">Popover</p>
              <p className="text-xs text-muted">Collides with the viewport edge and flips automatically.</p>
            </PopoverContent>
          </Popover>
          <Checkbox defaultChecked aria-label="A checked box" />
          <Checkbox indeterminate aria-label="An indeterminate box" />
          <Checkbox aria-label="An unchecked box" />
          <StatusDot ok label="reachable" />
          <StatusDot ok={false} label="down" />
          <Spinner />
        </div>
      </Section>

      <Section title="DataTable" description="real data: sort (shift for multi), resize, select, columns menu">
        <DataTable
          columns={columns}
          rows={rows}
          rowKey={(row) => row.id}
          caption="Registered targets"
          emptyTitle="No targets"
          emptyDescription="Add one to get started."
          emptyIcon={Network}
          showColumnToggle
          multiSort
          sortSpecs={sortSpecs}
          onSortSpecsChange={setSortSpecs}
          selectable
          selectedKeys={selected}
          onSelectedKeysChange={setSelected}
          stickyHeader
          onRowClick={(row) => toast.info(`Row click: ${row.name}`)}
        />
        <Pagination
          page={page}
          pageSize={10}
          total={rows.length}
          onPageChange={setPage}
          onPageSizeChange={() => toast.info('Page size change')}
        />
      </Section>

      <Section title="Shell controls" description="theme and sidebar state, for a visual check">
        <Card className="flex flex-wrap items-center gap-2 p-4">
          <Button variant="outline" size="sm" onClick={toggleTheme}>
            {theme === 'dark' ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
            Switch to {theme === 'dark' ? 'light' : 'dark'}
          </Button>
          <Button variant="outline" size="sm" onClick={toggleSidebar}>
            Sidebar is {sidebarCollapsed ? 'collapsed' : 'expanded'}
          </Button>
          <Button variant="ghost" size="sm" onClick={() => {
            void navigator.clipboard?.writeText('netsentinel').then(() => toast.success('Copied'))
          }}>
            <Copy className="h-4 w-4" /> Copy test
          </Button>
          <span className={cn('flex items-center gap-1 text-xs text-muted')}>
            <Info className="h-3.5 w-3.5" /> {theme} theme
          </span>
        </Card>
      </Section>
    </div>
  )
}