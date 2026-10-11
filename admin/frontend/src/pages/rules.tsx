import * as React from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import {
  ArrowDown,
  ArrowUp,
  Ban,
  CheckCircle2,
  ChevronRight,
  CircleSlash,
  Download,
  Globe,
  Info,
  Plus,
  ShieldCheck,
  Timer,
  Trash2,
  Upload,
  Users,
} from 'lucide-react'
import { toast } from 'sonner'
import {
  api,
  ApiError,
  RULE_TYPES,
  type Rule,
  type RuleScope,
  type RuleType,
} from '@/lib/api'
import { cn, formatNumber } from '@/lib/utils'
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogTitle,
  Badge,
  Button,
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  Input,
  Label,
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
  Switch,
  Textarea,
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from '@/components/ui'
import { EmptyState, ErrorState, Skeleton } from '@/components/feedback'
import { RulesTester } from '@/components/rules-tester'

/**
 * How each rule type is described, and what a valid value looks like.
 *
 * Kept beside the page rather than fetched, because it is a property of the
 * engine's vocabulary and not of this deployment. The value grammar mirrors
 * what the server accepts, so the form can explain the rule before it is
 * rejected -- the server's own messages are good, but arriving after a
 * round trip is a poor way to learn the syntax.
 */
const TYPE_META: Record<
  RuleType,
  { label: string; icon: typeof Globe; valueLabel: string; hint: string; example: string }
> = {
  allow_cidr: {
    label: 'Allow network',
    icon: Globe,
    valueLabel: 'CIDR range',
    hint: 'Scans inside this range are permitted. Leave empty to allow any address; add an allow rule only to narrow an existing deny.',
    example: '192.168.1.0/24',
  },
  deny_cidr: {
    label: 'Deny network',
    icon: Ban,
    valueLabel: 'CIDR range',
    hint: 'Scans against this range are refused. A deny at a lower priority number beats any allow.',
    example: '10.0.0.0/8',
  },
  allow_domain: {
    label: 'Allow domain',
    icon: Globe,
    valueLabel: 'Domain suffix',
    hint: 'Hostnames ending in this suffix are permitted. Suffix match, so "example.com" also covers "api.example.com".',
    example: 'example.com',
  },
  deny_domain: {
    label: 'Deny domain',
    icon: Ban,
    valueLabel: 'Domain suffix',
    hint: 'Hostnames ending in this suffix are refused. The engine never resolves DNS, so an address that maps to a denied domain is not caught by this rule.',
    example: 'malicious.test',
  },
  allow_port: {
    label: 'Allow port',
    icon: CheckCircle2,
    valueLabel: 'Ports or ranges',
    hint: 'Only these ports may be scanned. Requires a scan profile that declares a port list, or the rule is reported as skipped.',
    example: '22,80,443,8000-8100',
  },
  deny_port: {
    label: 'Deny port',
    icon: CircleSlash,
    valueLabel: 'Ports or ranges',
    hint: 'These ports are never scanned. Requires a scan profile that declares a port list, or the rule is reported as skipped.',
    example: '23,3389',
  },
  max_scan_time: {
    label: 'Cap scan duration',
    icon: Timer,
    valueLabel: 'Seconds',
    hint: 'A ceiling for how long a scan may run. A rule may only lower the configured timeout, never raise it.',
    example: '600',
  },
  rate_limit: {
    label: 'Rate limit',
    icon: Timer,
    valueLabel: 'Seconds between scans',
    hint: 'The shortest wait between two scans of the same key. Shadows the global rate limit rather than running beside it.',
    example: '120',
  },
}

const SCOPES: ReadonlyArray<{ value: RuleScope; label: string; hint: string }> = [
  { value: 'global', label: 'Everyone', hint: 'Applies to every request.' },
  {
    value: 'user',
    label: 'One Telegram user',
    hint: 'Applies to one Telegram user id. Leave the subject empty to match every user, which is how a global quota per user is written.',
  },
  {
    value: 'target',
    label: 'One target',
    hint: 'Applies to one target by name.',
  },
]

interface DraftRule {
  name: string
  rule_type: RuleType
  value: string
  priority: string
  scope: RuleScope
  scope_id: string
  description: string
}

const EMPTY_DRAFT: DraftRule = {
  name: '',
  rule_type: 'deny_port',
  value: '',
  priority: '50',
  scope: 'global',
  scope_id: '',
  description: '',
}

function draftFrom(rule: Rule): DraftRule {
  return {
    name: rule.name,
    rule_type: rule.rule_type,
    value: rule.value,
    priority: String(rule.priority),
    scope: rule.scope,
    scope_id: rule.scope_id ?? '',
    description: rule.description ?? '',
  }
}

/** Server detail, which is the authoritative explanation of a rejection. */
function explain(error: unknown): string {
  if (error instanceof ApiError) return error.message
  if (error instanceof Error) return error.message
  return 'Something went wrong.'
}

export function RulesPage(): JSX.Element {
  const queryClient = useQueryClient()
  const navigate = useNavigate()

  const rules = useQuery({
    queryKey: ['rules'],
    queryFn: ({ signal }) => api.listRules(signal),
  })

  const [editing, setEditing] = React.useState<Rule | null>(null)
  const [creating, setCreating] = React.useState(false)
  const [purging, setPurging] = React.useState<Rule | null>(null)
  const [importing, setImporting] = React.useState(false)

  const invalidate = (): void => {
    void queryClient.invalidateQueries({ queryKey: ['rules'] })
  }

  const toggle = useMutation({
    mutationFn: (id: number) => api.toggleRule(id),
    onSuccess: (result) => {
      invalidate()
      // The server returns the new state, so the toast reports what is true
      // rather than what the click assumed.
      toast.success(result.enabled ? 'Rule enabled' : 'Rule disabled')
    },
    onError: (error) => toast.error(explain(error)),
  })

  const remove = useMutation({
    mutationFn: (id: number) => api.deleteRule(id),
    onSuccess: (result) => {
      invalidate()
      toast.success(`Deleted ${result.deleted}`, {
        description: result.hits_kept
          ? `${formatNumber(result.hits_kept)} recorded hit(s) kept as history.`
          : undefined,
      })
    },
    onError: (error) => toast.error(explain(error)),
  })

  const reorder = useMutation({
    mutationFn: (ids: number[]) => api.reorderRules(ids),
    onSuccess: invalidate,
    onError: (error) => toast.error(explain(error)),
  })

  const exportSnapshot = useMutation({
    mutationFn: () => api.exportRules(),
    onSuccess: (snapshot) => {
      const blob = new Blob([JSON.stringify(snapshot, null, 2)], {
        type: 'application/json',
      })
      const url = URL.createObjectURL(blob)
      const link = document.createElement('a')
      link.href = url
      link.download = `netsentinel-rules-${snapshot.exported_at.slice(0, 10)}.json`
      link.click()
      URL.revokeObjectURL(url)
      toast.success(`Exported ${snapshot.rules.length} rule(s)`)
    },
    onError: (error) => toast.error(explain(error)),
  })

  function move(id: number, direction: -1 | 1): void {
    const current = rules.data?.rules ?? []
    const index = current.findIndex((rule) => rule.id === id)
    const target = index + direction
    if (index === -1 || target < 0 || target >= current.length) return
    const next = [...current]
    const [moved] = next.splice(index, 1)
    // `index` is known to be in range, so exactly one element came back. The
    // guard is a real check rather than `moved!` so that a future change to the
    // bounds above cannot silently push `undefined` into the ordering.
    if (moved === undefined) return
    next.splice(target, 0, moved)
    reorder.mutate(next.map((rule) => rule.id))
  }

  const list = rules.data?.rules ?? []
  const enabled = list.filter((rule) => rule.enabled).length

  return (
    <div className="space-y-6">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold tracking-tight text-fg">Rules</h1>
          <p className="mt-1 max-w-2xl text-sm text-muted">
            What the engine is allowed to do, and what it refuses. Rules are
            evaluated by ascending priority: lower runs first, and the first
            denial wins. An allow family with no matching rule denies the
            request, so an allow list narrows only where it exists.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Button variant="outline" onClick={() => setImporting(true)}>
            <Upload className="h-4 w-4" aria-hidden />
            Import
          </Button>
          <Button
            variant="outline"
            onClick={() => exportSnapshot.mutate()}
            disabled={exportSnapshot.isPending || list.length === 0}
          >
            <Download className="h-4 w-4" aria-hidden />
            Export
          </Button>
          <Button onClick={() => setCreating(true)}>
            <Plus className="h-4 w-4" aria-hidden />
            New rule
          </Button>
        </div>
      </header>

      <div className="grid gap-4 lg:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]">
        <div className="space-y-4">
          <Card>
            <CardHeader>
              <div>
                <CardTitle>Configured rules</CardTitle>
                <p className="mt-0.5 text-xs text-muted">
                  {rules.isLoading
                    ? 'Loading…'
                    : `${list.length} rule(s), ${enabled} enabled`}
                </p>
              </div>
            </CardHeader>
            <CardContent className="pt-0">
              {rules.isLoading ? (
                <div className="space-y-2">
                  {[0, 1, 2].map((i) => (
                    <Skeleton key={i} className="h-16 w-full" />
                  ))}
                </div>
              ) : rules.isError ? (
                <ErrorState
                  title="Could not load rules"
                  description={explain(rules.error)}
                />
              ) : list.length === 0 ? (
                <EmptyState
                  icon={ShieldCheck}
                  title="No rules yet"
                  description="Nothing is restricted: every scan is allowed by default. Add a rule to narrow that."
                  actionLabel="Add the first rule"
                  onAction={() => setCreating(true)}
                />
              ) : (
                <ul className="space-y-2">
                  {list.map((rule, index) => (
                    <RuleRow
                      key={rule.id}
                      rule={rule}
                      first={index === 0}
                      last={index === list.length - 1}
                      busy={toggle.isPending || reorder.isPending}
                      onEdit={() => setEditing(rule)}
                      onDelete={() => setPurging(rule)}
                      onToggle={() => toggle.mutate(rule.id)}
                      onMove={(direction) => move(rule.id, direction)}
                    />
                  ))}
                </ul>
              )}
            </CardContent>
          </Card>
        </div>

        <div className="space-y-4">
          <RulesTester />
          <Card>
            <CardHeader>
              <CardTitle>How a request is judged</CardTitle>
            </CardHeader>
            <CardContent className="space-y-3 text-sm text-muted">
              <p>
                Each rule family is asked independently: <em>did any allow rule
                in this family match?</em> If a family has allow rules and none
                matched, the request is refused. If it has none, it imposes
                nothing. Two families never compensate for each other, so a
                denied network is not rescued by a matching domain allow.
              </p>
              <p>
                Allow gates fail closed, which is the safe direction: a
                misconfigured allow list refuses traffic rather than admitting
                it.
              </p>
              <p>
                Nothing is resolved. A hostname is judged by its own domain
                rules, and a hostname that resolves to a denied address is not
                caught, because the engine never performs a DNS lookup.
              </p>
              <div className="rounded-md border border-border bg-elevated p-3">
                <p className="flex items-start gap-2 text-xs">
                  <Info className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden />
                  <span>
                    Port rules only apply when the scan profile declares its own
                    port list. <strong className="text-fg">web</strong>,{' '}
                    <strong className="text-fg">mail</strong>,{' '}
                    <strong className="text-fg">database</strong>,{' '}
                    <strong className="text-fg">remote</strong> and{' '}
                    <strong className="text-fg">common</strong> do;
                    quick, service and deep defer to nmap&apos;s own tables, so
                    their ports are unknown and port rules are skipped against
                    them. Pick one in{' '}
                    <button
                      type="button"
                      onClick={() => navigate('/settings')}
                      className="link underline-offset-2"
                    >
                      Settings
                    </button>
                    , and the tester above will tell you which of your rules are
                    actually taking effect.
                  </span>
                </p>
              </div>
            </CardContent>
          </Card>
        </div>
      </div>

      <RuleDialog
        open={creating || editing !== null}
        rule={editing}
        onClose={() => {
          setCreating(false)
          setEditing(null)
        }}
      />

      <PurgeDialog
        rule={purging}
        pending={remove.isPending}
        onClose={() => setPurging(null)}
        onConfirm={() => {
          if (purging) remove.mutate(purging.id)
        }}
      />

      <ImportDialog
        open={importing}
        onClose={() => setImporting(false)}
        onDone={() => {
          setImporting(false)
          invalidate()
        }}
      />

      <p className="text-xs text-faint">
        Need to restrict what gets scanned?{' '}
        <button
          type="button"
          onClick={() => navigate('/settings')}
          className="link underline-offset-2"
        >
          Scan profiles and the global limits
        </button>{' '}
        live in Settings.
      </p>
    </div>
  )
}

function RuleRow({
  rule,
  first,
  last,
  busy,
  onEdit,
  onDelete,
  onToggle,
  onMove,
}: {
  rule: Rule
  first: boolean
  last: boolean
  busy: boolean
  onEdit: () => void
  onDelete: () => void
  onToggle: () => void
  onMove: (direction: -1 | 1) => void
}): JSX.Element {
  const meta = TYPE_META[rule.rule_type] ?? TYPE_META.deny_port
  const Icon = meta.icon
  const isDeny = rule.rule_type.startsWith('deny_')

  return (
    <li
      className={cn(
        'card flex items-start gap-3 p-3',
        // A disabled rule is dimmed but still readable, and still clickable:
        // the row has to remain editable to be re-enabled.
        !rule.enabled && 'opacity-60',
      )}
    >
      <div
        className={cn(
          'mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-md',
          isDeny ? 'bg-danger-soft text-danger' : 'bg-success-soft text-success',
        )}
        aria-hidden
      >
        <Icon className="h-4 w-4" />
      </div>

      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2">
          <p className="truncate text-sm font-medium text-fg">{rule.name}</p>
          <Badge variant={isDeny ? 'danger' : 'success'}>{meta.label}</Badge>
          {!rule.enabled ? <Badge variant="muted">off</Badge> : null}
          {rule.scope !== 'global' ? (
            <Badge variant="info">
              <Users className="mr-1 h-3 w-3" aria-hidden />
              {rule.scope}
              {rule.scope_id ? `: ${rule.scope_id}` : ': any'}
            </Badge>
          ) : null}
          <span className="tabular-nums text-xs text-faint">
            priority {rule.priority}
          </span>
        </div>
        <p className="mono mt-1 truncate text-fg" title={rule.value}>
          {rule.value}
        </p>
        {rule.description ? (
          <p className="mt-1 line-clamp-2 text-xs text-muted">{rule.description}</p>
        ) : null}
      </div>

      <div className="flex shrink-0 items-center gap-1">
        <Tooltip>
          <TooltipTrigger asChild>
            <span className="flex">
              <Button
                variant="ghost"
                size="icon"
                className="h-8 w-8"
                disabled={first || busy}
                onClick={() => onMove(-1)}
                aria-label={`Move ${rule.name} earlier`}
              >
                <ArrowUp className="h-4 w-4" aria-hidden />
              </Button>
              <Button
                variant="ghost"
                size="icon"
                className="h-8 w-8"
                disabled={last || busy}
                onClick={() => onMove(1)}
                aria-label={`Move ${rule.name} later`}
              >
                <ArrowDown className="h-4 w-4" aria-hidden />
              </Button>
            </span>
          </TooltipTrigger>
          <TooltipContent>Lower priority number runs first</TooltipContent>
        </Tooltip>

        <Switch
          checked={rule.enabled}
          onCheckedChange={onToggle}
          disabled={busy}
          aria-label={`${rule.enabled ? 'Disable' : 'Enable'} ${rule.name}`}
        />
        <Button
          variant="ghost"
          size="icon"
          className="h-8 w-8"
          onClick={onEdit}
          aria-label={`Edit ${rule.name}`}
        >
          <ChevronRight className="h-4 w-4" aria-hidden />
        </Button>
        <Button
          variant="ghost"
          size="icon"
          className="h-8 w-8 text-muted hover:text-danger"
          onClick={onDelete}
          aria-label={`Delete ${rule.name}`}
        >
          <Trash2 className="h-4 w-4" aria-hidden />
        </Button>
      </div>
    </li>
  )
}

function RuleDialog({
  open,
  rule,
  onClose,
}: {
  open: boolean
  rule: Rule | null
  onClose: () => void
}): JSX.Element {
  const [draft, setDraft] = React.useState<DraftRule>(EMPTY_DRAFT)

  // Re-seed whenever the dialog opens, so a previous edit never leaks into a
  // new one. `rule` is the identity here, not a value: two different rules can
  // carry the same fields.
  React.useEffect(() => {
    if (open) setDraft(rule ? draftFrom(rule) : EMPTY_DRAFT)
  }, [open, rule])

  const queryClient = useQueryClient()
  const invalidate = (): void => {
    void queryClient.invalidateQueries({ queryKey: ['rules'] })
  }

  const create = useMutation({
    mutationFn: (body: DraftRule) =>
      api.createRule({
        name: body.name.trim(),
        rule_type: body.rule_type,
        value: body.value.trim(),
        priority: Number(body.priority) || 50,
        scope: body.scope,
        scope_id: body.scope_id.trim() || null,
        description: body.description.trim() || null,
      }),
    onSuccess: () => {
      invalidate()
      toast.success('Rule created')
      onClose()
    },
    onError: (error) => toast.error(explain(error)),
  })

  const update = useMutation({
    mutationFn: (body: DraftRule) => {
      if (!rule) throw new Error('no rule to update')
      return api.updateRule(rule.id, {
        rule_type: body.rule_type,
        value: body.value.trim(),
        priority: Number(body.priority) || 50,
        scope: body.scope,
        scope_id: body.scope_id.trim() || null,
        description: body.description.trim() || null,
      })
    },
    onSuccess: () => {
      invalidate()
      toast.success('Rule updated')
      onClose()
    },
    onError: (error) => toast.error(explain(error)),
  })

  const meta = TYPE_META[draft.rule_type]
  const scopeMeta = SCOPES.find((s) => s.value === draft.scope)
  const busy = create.isPending || update.isPending
  const nameError = draft.name.trim().length === 0
  const valueError = draft.value.trim().length === 0

  function submit(event: React.FormEvent): void {
    event.preventDefault()
    if (nameError || valueError) return
    if (rule) update.mutate(draft)
    else create.mutate(draft)
  }

  return (
    <DialogShell
      open={open}
      onClose={onClose}
      title={rule ? `Edit ${rule.name}` : 'New rule'}
      description={
        rule
          ? 'The name is fixed once a rule exists, so audit rows keep pointing at something recognisable.'
          : 'Rules are evaluated by ascending priority. The first denial wins.'
      }
    >
      <form onSubmit={submit} className="space-y-4">
        <div className="space-y-1.5">
          <Label htmlFor="rule-name">Name</Label>
          <Input
            id="rule-name"
            value={draft.name}
            disabled={rule !== null}
            onChange={(e) => setDraft((d) => ({ ...d, name: e.target.value }))}
            placeholder="No Telnet or RDP"
            maxLength={64}
            required
          />
          {nameError ? (
            <p className="text-xs text-danger">A name is required.</p>
          ) : null}
        </div>

        <div className="grid gap-3 sm:grid-cols-2">
          <div className="space-y-1.5">
            <Label htmlFor="rule-type">Type</Label>
            <Select
              value={draft.rule_type}
              onValueChange={(v) => setDraft((d) => ({ ...d, rule_type: v as RuleType }))}
            >
              <SelectTrigger id="rule-type">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {RULE_TYPES.map((type) => (
                  <SelectItem key={type} value={type}>
                    {TYPE_META[type].label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="rule-priority">Priority</Label>
            <Input
              id="rule-priority"
              type="number"
              min={1}
              max={10000}
              value={draft.priority}
              onChange={(e) => setDraft((d) => ({ ...d, priority: e.target.value }))}
            />
            <p className="text-xs text-muted">Lower runs first. 50 is neutral.</p>
          </div>
        </div>

        <div className="space-y-1.5">
          <Label htmlFor="rule-value">{meta.valueLabel}</Label>
          <Input
            id="rule-value"
            value={draft.value}
            onChange={(e) => setDraft((d) => ({ ...d, value: e.target.value }))}
            placeholder={meta.example}
            className="font-mono"
            required
          />
          <p className="text-xs text-muted">
            {meta.hint} For example <code className="font-mono">{meta.example}</code>.
          </p>
          {valueError ? (
            <p className="text-xs text-danger">This is required.</p>
          ) : null}
        </div>

        <div className="grid gap-3 sm:grid-cols-2">
          <div className="space-y-1.5">
            <Label htmlFor="rule-scope">Applies to</Label>
            <Select
              value={draft.scope}
              onValueChange={(v) => setDraft((d) => ({ ...d, scope: v as RuleScope }))}
            >
              <SelectTrigger id="rule-scope">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {SCOPES.map((scope) => (
                  <SelectItem key={scope.value} value={scope.value}>
                    {scope.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="rule-scope-id">
              {draft.scope === 'user' ? 'Telegram user id' : 'Target name'}
            </Label>
            <Input
              id="rule-scope-id"
              value={draft.scope_id}
              disabled={draft.scope === 'global'}
              onChange={(e) => setDraft((d) => ({ ...d, scope_id: e.target.value }))}
              placeholder={draft.scope === 'user' ? 'any' : 'home'}
            />
            <p className="text-xs text-muted">{scopeMeta?.hint}</p>
          </div>
        </div>

        <div className="space-y-1.5">
          <Label htmlFor="rule-description">Why this exists (optional)</Label>
          <Textarea
            id="rule-description"
            rows={2}
            value={draft.description}
            onChange={(e) => setDraft((d) => ({ ...d, description: e.target.value }))}
            placeholder="RDP was reachable during a pentest; do not probe it again."
            maxLength={2000}
          />
        </div>

        <div className="flex justify-end gap-2 pt-2">
          <Button type="button" variant="outline" onClick={onClose}>
            Cancel
          </Button>
          <Button type="submit" disabled={busy || nameError || valueError}>
            {rule ? 'Save changes' : 'Create rule'}
          </Button>
        </div>
      </form>
    </DialogShell>
  )
}

function PurgeDialog({
  rule,
  pending,
  onClose,
  onConfirm,
}: {
  rule: Rule | null
  pending: boolean
  onClose: () => void
  onConfirm: () => void
}): JSX.Element {
  return (
    <AlertDialog open={rule !== null} onOpenChange={(open) => !open && onClose()}>
      <AlertDialogContent>
        <AlertDialogTitle className="text-danger">Delete {rule?.name}?</AlertDialogTitle>
        <AlertDialogDescription>
          The rule stops applying immediately. Recorded hits are kept as
          history, so past decisions remain explainable.
        </AlertDialogDescription>
        <AlertDialogFooter>
          <AlertDialogCancel>Keep it</AlertDialogCancel>
          <AlertDialogAction asChild>
            <Button
              variant="danger"
              onClick={onConfirm}
              disabled={pending}
              className="bg-danger text-danger-fg hover:bg-danger-strong"
            >
              <Trash2 className="h-4 w-4" aria-hidden />
              {pending ? 'Deleting…' : 'Delete rule'}
            </Button>
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  )
}

function ImportDialog({
  open,
  onClose,
  onDone,
}: {
  open: boolean
  onClose: () => void
  onDone: () => void
}): JSX.Element {
  const [text, setText] = React.useState('')
  const [replace, setReplace] = React.useState(false)
  const [error, setError] = React.useState<string | null>(null)

  const run = useMutation({
    mutationFn: () => {
      let parsed: { rules?: unknown }
      try {
        parsed = JSON.parse(text) as { rules?: unknown }
      } catch (cause) {
        throw new Error('That is not valid JSON.')
      }
      const rules = Array.isArray(parsed.rules) ? parsed.rules : []
      if (rules.length === 0) {
        throw new Error('No "rules" array in that file.')
      }
      return api.importRules(rules as Parameters<typeof api.importRules>[0], replace)
    },
    onSuccess: (result) => {
      toast.success(`Imported ${result.imported} rule(s)`)
      setText('')
      setError(null)
      onDone()
    },
    onError: (cause) => setError(explain(cause)),
  })

  return (
    <DialogShell
      open={open}
      onClose={onClose}
      title="Import rules"
      description="Paste a snapshot exported from this panel."
    >
      <div className="space-y-4">
        <div className="space-y-1.5">
          <Label htmlFor="import-json">Snapshot JSON</Label>
          <Textarea
            id="import-json"
            rows={8}
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder='{ "version": 1, "rules": [ … ] }'
            className="font-mono text-xs"
          />
        </div>
        <label className="flex items-start gap-2 text-sm">
          <Switch checked={replace} onCheckedChange={setReplace} className="mt-0.5" />
          <span>
            Replace what is here
            <span className="block text-xs text-muted">
              Off, rules are merged by name. On, every existing rule is deleted
              first.
            </span>
          </span>
        </label>
        {error ? <p className="text-sm text-danger">{error}</p> : null}
        <div className="flex justify-end gap-2">
          <Button variant="outline" onClick={onClose}>
            Cancel
          </Button>
          <Button
            onClick={() => run.mutate()}
            disabled={run.isPending || text.trim().length === 0}
            // Destructive-looking only when it actually is: "replace" deletes
            // every existing rule, so the button changes with the switch rather
            // than always shouting.
            variant={replace ? 'danger' : 'default'}
          >
            <Upload className="h-4 w-4" aria-hidden />
            {run.isPending ? 'Importing…' : 'Import'}
          </Button>
        </div>
      </div>
    </DialogShell>
  )
}

/** The panel's own modal, so the dialogs match every other surface. */
function DialogShell({
  open,
  onClose,
  title,
  description,
  children,
}: {
  open: boolean
  onClose: () => void
  title: string
  description?: string
  children: React.ReactNode
}): JSX.Element {
  return (
    <Dialog open={open} onOpenChange={(next) => !next && onClose()}>
      <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-xl">
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          {description ? <DialogDescription>{description}</DialogDescription> : null}
        </DialogHeader>
        {children}
      </DialogContent>
    </Dialog>
  )
}
