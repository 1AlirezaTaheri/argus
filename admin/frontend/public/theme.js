// Migrated from 'ns-theme' when the project was renamed. The old key is still
// read, because a browser cannot know that a user's stored preference under the
// old name belongs to the new one -- renaming it outright would silently reset
// every existing user's chosen theme to their OS default.
// Applied before React mounts, so a light-theme user never sees a flash of the
// dark background.
//
// This is an external file rather than an inline <script> for one specific
// reason. The panel's CSP is `script-src 'self' https://telegram.org` with no
// 'unsafe-inline', so an inline script needs a sha256 of its own text in the
// header -- and the text the browser hashes is the script element's *complete*
// child text, including the newline after <script> and the indentation before
// </script>. Hash the "clean" body instead and the browser silently refuses to
// run the script, with no error anywhere except the console, and the page simply
// renders in whatever theme the HTML attribute says.
//
// That is not hypothetical: it is exactly what happened. An earlier version of
// this file was inline and the CSP carried the hash of the stripped body, so the
// anti-flash script was blocked on every page for the whole time the header
// looked correct. A hash that depends on whitespace a build tool may rewrite is
// a hash that will rot again.
//
// As a separate file under script-src 'self' there is no hash to keep in sync,
// nothing to regenerate, and the invariant becomes checkable: index.html
// contains no inline script at all.
//
// Deliberately a classic script, not a module: a module is deferred, so it would
// run after the document is parsed and after first paint -- which is the flash
// this exists to prevent.
;(function () {
  try {
    var stored = localStorage.getItem('argus-theme')
    if (stored === null) {
      // Pre-rename key. Read once, then written forward so the
      // migration happens on load rather than on every load.
      stored = localStorage.getItem('ns-theme')
      if (stored !== null) {
        try { localStorage.setItem('argus-theme', stored) } catch (e) {}
      }
    }
    var prefersLight = window.matchMedia('(prefers-color-scheme: light)').matches
    var theme =
      stored === 'light' || stored === 'dark'
        ? stored
        : prefersLight
          ? 'light'
          : 'dark'
    document.documentElement.setAttribute('data-theme', theme)
  } catch (e) {
    // Private mode can throw on localStorage. The HTML already carries
    // data-theme="dark", so doing nothing is the correct fallback.
  }
})()
