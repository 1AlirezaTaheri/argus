import { create } from 'zustand'
import { DEFAULT_LANG, dirOf, type Lang } from '@/lib/i18n'

export type Theme = 'dark' | 'light'

interface UIState {
  theme: Theme
  language: Lang
  sidebarCollapsed: boolean
  setTheme: (theme: Theme) => void
  toggleTheme: () => void
  setLanguage: (language: Lang) => void
  toggleSidebar: () => void
}

const STORAGE_KEY = 'argus-theme'
const LANG_KEY = 'argus-lang'

/**
 * The key this app used before the rename to rgus-theme. Still read, because a
 * browser cannot know that a preference stored under the old name belongs to the
 * new one -- silently resetting everyone's chosen theme to their OS default would
 * be invisible: the page still renders, just in the wrong theme.
 *
 * Removed once the value has been written forward, so the fallback does not
 * outlive the migration and start overriding a later deliberate choice.
 */
const LEGACY_STORAGE_KEY = 'ns-theme'
const SIDEBAR_KEY = 'ns-sidebar-collapsed'

/**
 * Resolve the initial theme.
 *
 * Order of preference: an explicit previous choice -- current key first, then
 * the pre-rename one -- then the OS `prefers-color-scheme`, then dark.
 *
 * The fallback has to exist in public/theme.js too. That script runs before
 * React mounts and sets the attribute directly, so if it ignored the old key the
 * first paint would use the OS default and the store would then disagree with
 * it, producing a visible theme flip on every load for exactly the users the
 * migration exists to protect.
 */
function initialTheme(): Theme {
  if (typeof window === 'undefined') return 'dark'

  const accept = (value: string | null): Theme | null =>
    value === 'light' || value === 'dark' ? value : null

  try {
    const stored = accept(window.localStorage.getItem(STORAGE_KEY))
    if (stored) return stored
  } catch {
    // Private-mode browsers can throw on localStorage access.
  }

  try {
    const legacy = accept(window.localStorage.getItem(LEGACY_STORAGE_KEY))
    if (legacy) {
      // Write forward and drop the old key, so this is a one-time migration
      // rather than a second lookup on every load -- and so a later deliberate
      // choice cannot be overridden by a stale value left behind.
      try {
        window.localStorage.setItem(STORAGE_KEY, legacy)
        window.localStorage.removeItem(LEGACY_STORAGE_KEY)
      } catch {
        // A write failure is not worth failing the read over.
      }
      return legacy
    }
  } catch {
    // Same: storage may be entirely unavailable.
  }

  try {
    if (window.matchMedia('(prefers-color-scheme: light)').matches) return 'light'
  } catch {
    // matchMedia is unavailable in some embedded webviews.
  }
  return 'dark'
}

/**
 * Resolve the initial panel language.
 *
 * Order: an explicit stored choice, then the browser's own preference, then
 * English.
 *
 * The browser preference is read from `navigator.language`, not from a
 * `--lang` custom property, because the two disagree often enough that guessing
 * between them is worse than taking the first and letting the selector correct
 * it. A wrong initial language is one click to fix; an unreadable one is not.
 */
function initialLanguage(): Lang {
  if (typeof window === 'undefined') return DEFAULT_LANG

  try {
    const stored = window.localStorage.getItem(LANG_KEY)
    if (stored === 'en' || stored === 'fa') return stored
  } catch {
    // Private-mode browsers can throw on localStorage access.
  }

  try {
    const nav = window.navigator.language || ''
    // Persian is served under several tags; anything fa-* is Persian.
    if (/^fa\b|-fa\b|^fa-/i.test(nav)) return 'fa'
  } catch {
    // navigator.language is not present in every webview.
  }

  return DEFAULT_LANG
}

/**
 * Apply the language to the document.
 *
 * `lang` matters for font selection and for anything assistive technology uses
 * to pick a pronunciation; `dir` matters because Persian is right-to-left and
 * a Persian sentence laid out left-to-right is misread, not merely ugly.
 *
 * The attribute is set on the document rather than on a subtree so that any
 * portalled content and the browser's own direction handling agree.
 */
function applyLanguage(language: Lang): void {
  if (typeof document === 'undefined') return
  const root = document.documentElement
  root.setAttribute('lang', language)
  root.setAttribute('dir', dirOf(language))
}

/**
 * Apply the theme to the document.
 *
 * On first mount the transition class is skipped: the page has just painted, so
 * animating it would look like a flash rather than a deliberate switch.
 */
function applyTheme(theme: Theme, animate = true): void {
  if (typeof document === 'undefined') return
  const root = document.documentElement
  if (animate) {
    root.classList.add('theme-transition')
    window.setTimeout(() => root.classList.remove('theme-transition'), 200)
  }
  root.setAttribute('data-theme', theme)
}

function readSidebar(): boolean {
  if (typeof window === 'undefined') return false
  try {
    return window.localStorage.getItem(SIDEBAR_KEY) === 'true'
  } catch {
    return false
  }
}

function persist(key: string, value: string): void {
  try {
    window.localStorage.setItem(key, value)
  } catch {
    // Non-fatal: the theme still applies for this session.
  }
}

export const useUIStore = create<UIState>((set, get) => ({
  theme: initialTheme(),
  language: initialLanguage(),

  sidebarCollapsed: readSidebar(),

  setTheme: (theme) => {
    applyTheme(theme)
    persist(STORAGE_KEY, theme)
    set({ theme })
  },

  toggleTheme: () => {
    get().setTheme(get().theme === 'dark' ? 'light' : 'dark')
  },

  setLanguage: (language) => {
    applyLanguage(language)
    persist(LANG_KEY, language)
    set({ language })
  },

  toggleSidebar: () => {
    const next = !get().sidebarCollapsed
    persist(SIDEBAR_KEY, String(next))
    set({ sidebarCollapsed: next })
  },
}))

// Apply on first module evaluation, so the very first paint is already in the
// right language and direction rather than flipping after hydration.
applyTheme(useUIStore.getState().theme, false)
applyLanguage(useUIStore.getState().language)