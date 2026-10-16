import { MESSAGES, keysOf, placeholdersOf, t, type Lang, type MessageKey } from './i18n'

/**
 * Parity and integrity checks for the panel dictionaries.
 *
 * ## What can go wrong that TypeScript cannot catch
 *
 * `fa` is typed as `Record<keyof typeof en, string>`, so a key present in `en`
 * and missing from `fa` is a compile error. That covers the common case.
 *
 * It does not cover:
 *   * a value that is an empty string -- `Record<..., string>` accepts `''`, and
 *     an empty label renders as a blank button that reads as a layout bug;
 *   * a value identical to the key, which means the lookup silently failed;
 *   * a `{placeholder}` present in one language and not the other, which throws
 *     at render time or, worse, silently drops the substitution;
 *   * whitespace-only values.
 *
 * This runs in the browser against the shipped bundle, so it tests what is
 * actually deployed rather than what is on disk.
 */
export function checkParity(): string[] {
  const problems: string[] = []
  const langs: Lang[] = ['en', 'fa']

  for (const key of keysOf('en')) {
    if (!keysOf('fa').includes(key)) problems.push(`${key}: present in en, missing in fa`)
  }
  for (const key of keysOf('fa')) {
    if (!keysOf('en').includes(key)) problems.push(`${key}: present in fa, missing in en`)
  }

  for (const lang of langs) {
    const other: Lang = lang === 'en' ? 'fa' : 'en'
    for (const key of keysOf(lang)) {
      const value = MESSAGES[lang][key as MessageKey]
      if (typeof value !== 'string') {
        problems.push(`${lang}.${key}: value is ${typeof value}, expected a string`)
        continue
      }
      if (value.trim() === '') {
        problems.push(`${lang}.${key}: empty or whitespace-only -- renders as a blank control`)
      }
      if (value === key) {
        problems.push(`${lang}.${key}: value equals the key, so the lookup did not resolve`)
      }
      const mine = placeholdersOf(value).join(',')
      const theirs = placeholdersOf(t(key as MessageKey, other)).join(',')
      if (mine !== theirs) {
        problems.push(
          `${key}: placeholders differ -- ${lang} has [${mine}], ${other} has [${theirs}]`,
        )
      }
    }
  }
  return problems
}

/** Exposed so a browser probe can run the check against the deployed bundle. */
if (typeof window !== 'undefined') {
  ;(window as unknown as Record<string, unknown>).__argusLangCheck = checkParity
}