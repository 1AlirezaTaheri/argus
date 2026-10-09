/** @type {import('tailwindcss').Config} */
export default {
  // No `darkMode` entry on purpose.
  //
  // Theming is done with CSS custom properties on [data-theme], not with
  // Tailwind's dark: variant, because every component reads semantic tokens
  // (bg-surface, text-muted) that are redefined per theme -- so a theme flip is
  // one attribute change, with no re-render and no duplicated class strings.
  //
  // This config previously declared darkMode: ['class'] while the app actually
  // set data-theme. Nothing used a dark: variant so nothing broke, but it was a
  // trap: the first person to write `dark:bg-zinc-900` would get no styling and
  // no warning. If a real need for a dark: variant appears it must be keyed to
  // the data-theme attribute, not `class`.
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        // Every colour is a CSS variable so the theme toggle only has to
        // flip attributes on <html>; Tailwind never hardcodes a hex.
        bg: 'rgb(var(--bg) / <alpha-value>)',
        surface: 'rgb(var(--surface) / <alpha-value>)',
        elevated: 'rgb(var(--elevated) / <alpha-value>)',
        border: 'rgb(var(--border) / <alpha-value>)',
        // A second border step. --border alone was too quiet to mark hover,
        // focus and active edges, which is why every interactive element used
        // to reach for an arbitrary opacity instead.
        'border-strong': 'rgb(var(--border-strong) / <alpha-value>)',
        input: 'rgb(var(--input) / <alpha-value>)',
        fg: 'rgb(var(--fg) / <alpha-value>)',
        muted: 'rgb(var(--muted) / <alpha-value>)',
        faint: 'rgb(var(--faint) / <alpha-value>)',
        // Dialog and drawer backdrop.
        overlay: 'rgb(var(--overlay) / <alpha-value>)',
        primary: {
          DEFAULT: 'rgb(var(--primary) / <alpha-value>)',
          fg: 'rgb(var(--primary-fg) / <alpha-value>)',
          soft: 'rgb(var(--primary-soft) / <alpha-value>)',
          // The hover step for a filled primary button.
          strong: 'rgb(var(--primary-strong) / <alpha-value>)',
        },
        danger: {
          DEFAULT: 'rgb(var(--danger) / <alpha-value>)',
          fg: 'rgb(var(--danger-fg) / <alpha-value>)',
          soft: 'rgb(var(--danger-soft) / <alpha-value>)',
          strong: 'rgb(var(--danger-strong) / <alpha-value>)',
        },
        warning: {
          DEFAULT: 'rgb(var(--warning) / <alpha-value>)',
          soft: 'rgb(var(--warning-soft) / <alpha-value>)',
        },
        info: {
          DEFAULT: 'rgb(var(--info) / <alpha-value>)',
          soft: 'rgb(var(--info-soft) / <alpha-value>)',
        },
        success: {
          DEFAULT: 'rgb(var(--success) / <alpha-value>)',
          soft: 'rgb(var(--success-soft) / <alpha-value>)',
        },
        // Chart series palette. Exposed as `chart-1`..`chart-5` so a chart can
        // be restyled from one place instead of each series naming a colour
        // inline. These are distinct from the status colours above on purpose:
        // the status hues each *mean* something, and a chart needs five that are
        // merely distinguishable from one another.
        chart: {
          1: 'rgb(var(--chart-1) / <alpha-value>)',
          2: 'rgb(var(--chart-2) / <alpha-value>)',
          3: 'rgb(var(--chart-3) / <alpha-value>)',
          4: 'rgb(var(--chart-4) / <alpha-value>)',
          5: 'rgb(var(--chart-5) / <alpha-value>)',
        },
      },
      fontFamily: {
        // 'Inter Variable' is the family name @fontsource-variable/inter
        // actually declares in its @font-face. Asking for 'Inter' matches no
        // face, so the browser leaves every one unloaded and never fetches the
        // woff2 -- the panel silently renders in the fallback stack.
        sans: [
          'Inter Variable',
          'Inter',
          'system-ui',
          '-apple-system',
          'Segoe UI',
          'sans-serif',
        ],
        mono: ['ui-monospace', 'SFMono-Regular', 'Menlo', 'monospace'],
      },
      borderRadius: {
        // 2xl added for the large panels (dialogs, the mobile drawer) so they
        // can read as a distinct surface layer from the cards inside them.
        '2xl': '1rem',
        xl: '0.75rem',
        lg: '0.5rem',
        md: '0.375rem',
        sm: '0.25rem',
      },
      // Named so a shadow can express depth rather than an arbitrary
      // Tailwind step. Sized and coloured per theme in index.css, because a
      // shadow that works on an opaque dark surface is wrong on a
      // translucent light one.
      boxShadow: {
        sm: 'var(--shadow-sm)',
        md: 'var(--shadow-md)',
        lg: 'var(--shadow-lg)',
        // For focus rings and the active nav item. Tinted with the theme's
        // own accent rather than pure black, so it reads as glow.
        glow: 'var(--shadow-glow)',
      },
      // Only durations the app actually uses are declared: Tailwind emits a
      // utility only if the scanned source references it, so an unreferenced
      // entry is dead config that reads as coverage but provides none.
      transitionDuration: {
        fast: '150ms',
        normal: '250ms',
        // Matches the accordion keyframes exactly. Shared with them rather
        // than reusing `normal`, so a motion change cannot desync the panel
        // from its animation.
        accordion: '200ms',
      },
      transitionTimingFunction: {
        standard: 'cubic-bezier(0.2, 0, 0.2, 1)',
        entrance: 'cubic-bezier(0, 0, 0.2, 1)',
        exit: 'cubic-bezier(0.4, 0, 1, 1)',
      },
      keyframes: {
        'fade-in': { from: { opacity: '0' }, to: { opacity: '1' } },
        'slide-up': {
          from: { opacity: '0', transform: 'translateY(6px)' },
          to: { opacity: '1', transform: 'translateY(0)' },
        },
        'slide-in-right': {
          from: { transform: 'translateX(100%)' },
          to: { transform: 'translateX(0)' },
        },
        shake: {
          '0%, 100%': { transform: 'translateX(0)' },
          '15%, 45%, 75%': { transform: 'translateX(-6px)' },
          '30%, 60%, 90%': { transform: 'translateX(6px)' },
        },
        shimmer: {
          '100%': { transform: 'translateX(100%)' },
        },
        // Grid-template-rows disclosure, used by Accordion. Animating a
        // measured pixel height would need a ref and a ResizeObserver per
        // panel; 1fr/0fr needs neither and handles a resize for free.
        'accordion-down': {
          from: { gridTemplateRows: '0fr' },
          to: { gridTemplateRows: '1fr' },
        },
        'accordion-up': {
          from: { gridTemplateRows: '1fr' },
          to: { gridTemplateRows: '0fr' },
        },
      },
      animation: {
        'fade-in': 'fade-in 150ms ease-out',
        'slide-up': 'slide-up 180ms ease-out',
        'slide-in-right': 'slide-in-right 200ms ease-out',
        shake: 'shake 400ms ease-in-out',
        shimmer: 'shimmer 1.6s infinite',
        'accordion-down': 'accordion-down 200ms cubic-bezier(0, 0, 0.2, 1)',
        'accordion-up': 'accordion-up 200ms cubic-bezier(0.4, 0, 1, 1)',
      },
    },
  },
  plugins: [],
}