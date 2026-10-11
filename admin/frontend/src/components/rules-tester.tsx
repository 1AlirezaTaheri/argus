import * as React from 'react'
import { useMutation } from '@tanstack/react-query'
import { AlertTriangle, Ban, CheckCircle2, FlaskConical } from 'lucide-react'
import { api, type RuleTestResult } from '@/lib/api'
import { cn } from '@/lib/utils'
import { Badge, Button, Card, CardContent, CardHeader, CardTitle, Input, Label } from './ui'

/**
 * Ask the engine what it would decide, and say so without changing anything.
 *
 * This exists because the rule engine has a failure mode that is otherwise
 * invisible. Port rules are skipped when the scan profile declares no port list,
 * and the shipped profiles all use nmap's own `-F` / `--top-ports`, so their port
 * set is unknown. A `deny_port` rule then looks configured, appears in the list,
 * and does nothing -- with no error anywhere.
 *
 * `/api/rules/test` answers "what would happen" and reports its own warnings,
 * so this turns an invisible rule into a visible one: type a target, supply the
 * ports a scan would probe, and the answer says which rule decided it.
 *
 * The endpoint's `side_effects` field is "none" by construction; it is
 * displayed so that stays checkable from the UI.
 */
export function RulesTester(): JSX.Element {
  const [target, setTarget] = React.useState('192.168.1.10')
  const [ports, setPorts] = React.useState('22,80,443')

  const test = useMutation({
    mutationFn: () =>
      api.testRules({
        target: target.trim(),
        // Parsed leniently, and an unparseable entry is dropped rather than sent:
        // the point is to explore, and the server will still validate the ports
        // that do arrive.
        ports: parsePorts(ports),
      }),
  })

  const result = test.data

  return (
    <Card>
      <CardHeader>
        <div>
          <CardTitle>Try a request</CardTitle>
          <p className="mt-0.5 text-xs text-muted">
            Asks the engine what it would do. Nothing is scanned and nothing
            changes.
          </p>
        </div>
      </CardHeader>
      <CardContent className="space-y-3 pt-0">
        <div className="space-y-1.5">
          <Label htmlFor="test-target">Target</Label>
          <Input
            id="test-target"
            value={target}
            onChange={(e) => setTarget(e.target.value)}
            placeholder="an address, a CIDR, or a hostname"
            className="font-mono"
          />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="test-ports">Ports the scan would probe</Label>
          <Input
            id="test-ports"
            value={ports}
            onChange={(e) => setPorts(e.target.value)}
            placeholder="22,80,443,8000-8100"
            className="font-mono"
          />
          <p className="text-xs text-muted">
            Port rules only apply when this list is supplied and the profile
            declares the same ports. Leave it empty to see that difference.
          </p>
        </div>

        <Button
          onClick={() => test.mutate()}
          disabled={test.isPending || target.trim().length === 0}
          className="w-full"
        >
          <FlaskConical className="h-4 w-4" aria-hidden />
          {test.isPending ? 'Asking…' : 'Ask the engine'}
        </Button>

        {test.isError ? (
          <p className="text-sm text-danger">
            {test.error instanceof Error ? test.error.message : 'Request failed'}
          </p>
        ) : null}

        {result ? <Verdict result={result} /> : null}
      </CardContent>
    </Card>
  )
}

function Verdict({ result }: { result: RuleTestResult }): JSX.Element {
  return (
    <div
      className={cn(
        'space-y-3 rounded-lg border p-3',
        result.allowed ? 'border-success/40 bg-success-soft/40' : 'border-danger/40 bg-danger-soft/40',
      )}
      // The verdict is a live region: it changes without a navigation, and a
      // screen reader user asked a question and is owed an answer.
      role="status"
      aria-live="polite"
    >
      <div className="flex items-center gap-2">
        {result.allowed ? (
          <CheckCircle2 className="h-4 w-4 text-success" aria-hidden />
        ) : (
          <Ban className="h-4 w-4 text-danger" aria-hidden />
        )}
        <span
          className={cn(
            'text-sm font-semibold',
            result.allowed ? 'text-success' : 'text-danger',
          )}
        >
          {result.allowed ? 'Allowed' : 'Refused'}
        </span>
        <span className="truncate text-xs text-muted" title={result.reason}>
          {result.reason}
        </span>
      </div>

      {result.rule_name ? (
        <p className="text-xs text-muted">
          Decided by <span className="text-fg">{result.rule_name}</span>
        </p>
      ) : null}

      {result.warnings.length > 0 ? (
        <ul className="space-y-1.5">
          {result.warnings.map((warning) => (
            <li
              key={warning}
              className="flex items-start gap-2 rounded-md bg-warning-soft/50 px-2 py-1.5 text-xs text-warning"
            >
              <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden />
              <span>{warning}</span>
            </li>
          ))}
        </ul>
      ) : null}

      <div className="flex flex-wrap gap-1.5">
        <Badge variant="muted">{result.rules_considered} rule(s) loaded</Badge>
        <Badge variant={result.rules_enabled ? 'info' : 'warning'}>
          engine {result.rules_enabled ? 'on' : 'off'}
        </Badge>
        {result.effective_rate_limit_seconds !== null ? (
          <Badge variant="info">
            min {result.effective_rate_limit_seconds}s between scans
          </Badge>
        ) : null}
        {result.effective_scan_timeout !== null ? (
          <Badge variant="info">
            {result.effective_scan_timeout}s scan cap
          </Badge>
        ) : null}
        <Badge variant="muted">side effects: {result.side_effects}</Badge>
      </div>
    </div>
  )
}

/** `22,80,8000-8100` -> [22, 80, 8000..8100]. Junk entries are dropped. */
function parsePorts(spec: string): number[] {
  const out: number[] = []
  for (const part of spec.split(/[,\s]+/)) {
    if (!part) continue
    const range = /^(\d+)-(\d+)$/.exec(part)
    if (range) {
      const lo = Number(range[1])
      const hi = Number(range[2])
      // Bounded: a reversed or enormous range would otherwise build a list big
      // enough to hang the tab, and this is a convenience field.
      if (lo >= 1 && hi >= lo && hi - lo <= 4096) {
        for (let p = lo; p <= hi; p++) out.push(p)
      }
      continue
    }
    const n = Number(part)
    if (Number.isInteger(n) && n >= 1 && n <= 65535) out.push(n)
  }
  return out
}
