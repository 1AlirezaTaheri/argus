import { clsx, type ClassValue } from 'clsx'
import { twMerge } from 'tailwind-merge'

/** Merge conditional class names, with later Tailwind utilities winning. */
export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs))
}

/** Initials for the avatar circle: at most two letters. */
export function initials(value: string | null | undefined): string {
  if (!value) return '?'
  const cleaned = value.replace(/[^a-zA-Z0-9]/g, '')
  return cleaned.slice(0, 2).toUpperCase() || '?'
}

/**
 * Deterministic avatar colour, so a given user gets the same tint across
 * sessions and pages.
 *
 * Reads the `--avatar-N` / `--avatar-N-fg` tokens rather than hardcoding
 * Tailwind palette steps. The previous list was `text-{hue}-400` on
 * `{hue}-500/15`, which was tuned for the dark surface: on white, rose-400
 * sits at about 2.5:1, and an avatar's initials are text. There was no
 * light-theme variant at all -- the palette was simply dark-only and nothing
 * checked it.
 *
 * The hash is unchanged, so a user keeps the same hue they had before.
 */
const AVATAR_COLORS = [
  'bg-avatar-1 text-avatar-1-fg',
  'bg-avatar-2 text-avatar-2-fg',
  'bg-avatar-3 text-avatar-3-fg',
  'bg-avatar-4 text-avatar-4-fg',
  'bg-avatar-5 text-avatar-5-fg',
  'bg-avatar-6 text-avatar-6-fg',
]


export function avatarColor(seed: string | number): string {
  const key = String(seed)
  let hash = 0
  for (let i = 0; i < key.length; i += 1) {
    hash = (hash * 31 + key.charCodeAt(i)) >>> 0
  }
  return AVATAR_COLORS[hash % AVATAR_COLORS.length] ?? AVATAR_COLORS[0]!
}

export function formatNumber(value: number): string {
  return new Intl.NumberFormat().format(value)
}

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  const units = ['KB', 'MB', 'GB']
  let n = bytes / 1024
  let i = 0
  while (n >= 1024 && i < units.length - 1) {
    n /= 1024
    i += 1
  }
  return `${n.toFixed(1)} ${units[i]}`
}

export function truncate(value: string, max: number): string {
  return value.length > max ? `${value.slice(0, max - 1)}…` : value
}