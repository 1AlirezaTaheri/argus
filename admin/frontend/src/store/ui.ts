import { create } from 'zustand'

export type Theme = 'dark' | 'light'

interface UIState {
  theme: Theme
  sidebarCollapsed: boolean
  setTheme: (theme: Theme) => void
  toggleTheme: () => void
  toggleSidebar: () => void
}

const STORAGE_KEY = 'argus-theme'
/**
 * The key this app used before the rename. Still read, because a
 * browser cannot know a preference stored under the old name belongs
 * to the new one -- and silently resetting everyone's chosen theme to
 * their OS default would be invisible.
 */
/**
 * The key this app used before the rename.
 *
 * Still read, because a browser cannot know that a preference stored under the
 * old name belongs to the new one. Renaming the key outright would silently
 * reset every existing user's chosen theme to their OS default, and it would be
 * invisible: the page still renders, just in the wrong theme.
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

function applyTheme(theme: Theme, animate = true): void {
  if (typeof document === 'undefined') return
  const root = document.documentElement
  if (animate) {
    // Only animate a deliberate switch, never the initial mount.
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

  sidebarCollapsed: readSidebar(),

  setTheme: (theme) => {
    applyTheme(theme)
    persist(STORAGE_KEY, theme)
    set({ theme })
  },

  toggleTheme: () => {
    get().setTheme(get().theme === 'dark' ? 'light' : 'dark')
  },

  toggleSidebar: () => {
    const next = !get().sidebarCollapsed
    persist(SIDEBAR_KEY, String(next))
    set({ sidebarCollapsed: next })
  },
}))

// Apply the stored theme on first module evaluation.
applyTheme(useUIStore.getState().theme, false)