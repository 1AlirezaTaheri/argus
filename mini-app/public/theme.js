// Applied before React mounts so the first paint already uses Telegram's
// palette.
//
// This was an inline <script>, and the panel's CSP is `script-src 'self'
// https://telegram.org` with no 'unsafe-inline' and no hash. So the browser
// refused it on every load, and this file's contents never ran. The symptom was
// narrow but real: the Mini App's own CSS hardcodes a *light* palette in
// :root, and this script is what replaces those with Telegram's actual theme
// colours before the first frame. Blocked, every dark-theme Telegram user got
// a white flash before the app settled into dark.
//
// Moving it out of index.html removes the hash problem entirely -- 'self' already
// permits a same-origin file -- and makes the invariant checkable: the Mini App's
// index.html contains no inline script.
//
// A classic script, not a module: a module is deferred and would run after the
// first paint, which is the flash this prevents.
;(function () {
  try {
    var tg = window.Telegram && window.Telegram.WebApp
    var theme = (tg && tg.themeParams) || {}
    var root = document.documentElement
    var set = function (name, fallback) {
      root.style.setProperty(name, theme[name] || fallback)
    }
    set('--tg-bg', '#ffffff')
    set('--tg-secondary-bg', '#f2f2f7')
    set('--tg-text', '#000000')
    set('--tg-hint', '#707579')
    set('--tg-link', '#2aabee')
    set('--tg-button', '#2aabee')
    set('--tg-button-text', '#ffffff')
    if (tg && tg.colorScheme === 'dark') {
      set('--tg-bg', '#17212b')
      set('--tg-secondary-bg', '#0e1621')
      set('--tg-text', '#f5f5f5')
      set('--tg-hint', '#7f91a4')
      set('--tg-link', '#6ab3f3')
      set('--tg-button', '#2aabee')
      set('--tg-button-text', '#ffffff')
    }
  } catch (e) {
    // index.css already declares a light :root palette, so leaving the
    // defaults in place is the correct fallback.
  }
})()
