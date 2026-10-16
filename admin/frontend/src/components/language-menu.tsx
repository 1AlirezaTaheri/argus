import * as DropdownMenu from '@radix-ui/react-dropdown-menu'
import { Check, Globe } from 'lucide-react'
import { useUIStore } from '@/store/ui'
import { LANGUAGES, type Lang } from '@/lib/i18n'
import { useI18n } from '@/lib/use-i18n'
import { cn } from '@/lib/utils'

/**
 * Language selector.
 *
 * A Radix dropdown rather than a `<select>`, to match the theme and account
 * menus beside it -- two different-looking pickers in one header is worse than
 * either on its own.
 *
 * The choice is applied immediately on click rather than behind a Save button:
 * a language selector that needs confirming is a language selector that gets
 * left alone. `setLanguage` persists it, so the choice survives a reload.
 *
 * The trigger carries the current language's own name rather than a globe icon
 * alone. A globe is ambiguous to a reader who does not already know which
 * language it refers to; "English" tells them what they will get.
 */
export function LanguageMenu({ className }: { className?: string }): JSX.Element {
  const tr = useI18n()
  const language = useUIStore((s) => s.language)
  const setLanguage = useUIStore((s) => s.setLanguage)
  // LANGUAGES[0] is typed as possibly-undefined under noUncheckedIndexedAccess,
  // so the fallback is written to satisfy the type rather than to paper over it.
  const fallback = LANGUAGES[0]
  const current = LANGUAGES.find((l) => l.value === language) ?? fallback
  if (!current) return <></>

  return (
    <DropdownMenu.Root>
      <DropdownMenu.Trigger
        aria-label={tr('chrome.changeLanguage')}
        className={cn(
          'inline-flex h-9 items-center gap-1.5 rounded-md px-2.5 text-xs',
          'text-muted transition-colors hover:bg-surface-raised hover:text-fg',
          'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary',
          className,
        )}
      >
        <Globe className="h-3.5 w-3.5" aria-hidden />
        <span>{current.label}</span>
      </DropdownMenu.Trigger>

      <DropdownMenu.Portal>
        <DropdownMenu.Content
          align="end"
          sideOffset={6}
          className="z-50 min-w-[10rem] overflow-hidden rounded-lg border border-border bg-surface-raised p-1 shadow-lg"
        >
          <DropdownMenu.Label className="px-2 py-1.5 text-[0.6875rem] font-semibold uppercase tracking-wide text-faint">
            {tr('chrome.language')}
          </DropdownMenu.Label>
          {LANGUAGES.map((option) => (
            <DropdownMenu.Item
              key={option.value}
              onSelect={() => setLanguage(option.value as Lang)}
              aria-current={option.value === language ? 'true' : undefined}
              className={cn(
                'flex cursor-pointer items-center justify-between gap-3 rounded px-2 py-1.5 text-sm',
                'text-fg outline-none data-[highlighted]:bg-surface-hover',
              )}
            >
              <span>{option.label}</span>
              {option.value === language ? (
                <Check className="h-3.5 w-3.5 text-primary" aria-hidden />
              ) : null}
            </DropdownMenu.Item>
          ))}
        </DropdownMenu.Content>
      </DropdownMenu.Portal>
    </DropdownMenu.Root>
  )
}