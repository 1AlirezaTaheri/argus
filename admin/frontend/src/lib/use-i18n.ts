import * as React from 'react'
import { useUIStore } from '@/store/ui'
import { t, type MessageKey } from './i18n'

/**
 * The panel's translation hook.
 *
 * Returns `t` already bound to the current language, so a component writes
 * `const tr = useI18n()` and then `tr('nav.targets')` rather than threading the
 * language through every call.
 *
 * The language comes from the UI store, so a change re-renders every subscriber.
 * That is what makes "changing the language changes the strings" a property of
 * the mechanism rather than of each page remembering to re-read something: a
 * page that forgets to call the hook keeps showing English, and the parity and
 * coverage checks are what catch that, not a comment asking nicely.
 *
 * The optional `values` are substituted into `{placeholder}` slots.
 */
export function useI18n(): (key: MessageKey, values?: Record<string, string | number>) => string {
  const language = useUIStore((s) => s.language)
  return React.useCallback(
    (key: MessageKey, values?: Record<string, string | number>) => t(key, language, values),
    [language],
  )
}