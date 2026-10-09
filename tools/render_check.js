/**
 * Visit every panel route in a real browser and require that each one renders.
 *
 * The target detail page shipped a completely blank screen for the whole of
 * Phase 3. Its cause was React error #310 -- `useMemo` and `useEffect` called
 * after three early returns, so the loading render ran zero hooks there and the
 * loaded render ran two.
 *
 * Nothing static caught it:
 *   - `tsc --noEmit` passed, because hook order is not a type error;
 *   - every API test passed, because the endpoints were always correct;
 *   - a source heuristic for hook order passed the buggy file too, which is
 *     how I know a heuristic is the wrong tool here.
 *
 * Only React's own invariant catches it, and only at render time. So this checks
 * it where it actually happens: load the route, let React render, and require
 * zero page errors plus a non-empty DOM.
 *
 * Credentials are read from the environment, which the wrapper script populates
 * from .env inside the container. Nothing prints them.
 */
const puppeteer = require('puppeteer');

const BASE = process.env.TARGET_BASE || 'https://127.0.0.1:8443';
const USER = process.env.ADMIN_USERNAME;
const PW = process.env.ADMIN_PASSWORD;

if (!USER || !PW) {
  console.error('ADMIN_USERNAME / ADMIN_PASSWORD not set');
  process.exit(1);
}

// Covers every route in the panel, including the two that exercise the
// error/empty paths rather than the happy one.
const ROUTES = [
  '/admin',
  '/admin/targets',
  '/admin/targets/3',
  '/admin/audit',
  '/admin/settings',
  '/admin/telegram-users',
  '/admin/style-guide',
  '/admin/targets/999999',  // exists in the nav, no such row -> error state
  '/admin/targets/not-a-number', // malformed id -> invalid state
  '/admin/nope',            // no such route
];

// React error #310 and the blank-page family all surface as an uncaught
// exception, which puppeteer reports as `pageerror`.
function describe(text) {
  if (/Minified React error #310/.test(text)) return 'hooks order (#310)';
  if (/Minified React error #185/.test(text)) return 'max update depth';
  if (/Minified React error #418/.test(text)) return 'hydration/text mismatch';
  return text.slice(0, 160);
}

(async () => {
  const browser = await puppeteer.launch({
    headless: 'new',
    args: ['--no-sandbox', '--disable-setuid-sandbox', '--disable-dev-shm-usage',
           '--ignore-certificate-errors'],
  });

  const page = await browser.newPage();
  await page.setViewport({ width: 1440, height: 900 });

  const failures = [];

  // Collect errors per navigation, so one route's error is not attributed to
  // the next.
  let bucket = [];
  page.on('pageerror', (e) => bucket.push(`pageerror: ${e.message}`));
  page.on('console', (m) => {
    if (m.type() === 'error') {
      const t = m.text();
      if (/Content Security Policy|fonts\.googleapis|net::ERR/.test(t)) return;
      // Chrome logs every non-2xx response as a console error. On
      // /admin/targets/999999 that 404 is the correct answer -- the route
      // deliberately asks for a target that does not exist so the error state
      // renders. An HTTP status is not a JavaScript error, and counting it as
      // one would make this check fail on correct behaviour. Non-2xx responses
      // are tracked separately below so a missing asset is still visible.
      if (/Failed to load resource/.test(t)) return;
      bucket.push(`console.error: ${t}`);
    }
  });
  // Separate channel for HTTP status, so it can be reported without being
  // confused with a render failure.
  let http = [];
  page.on('response', (r) => {
    if (r.status() >= 400) http.push(`${r.status()} ${r.url().replace(BASE, '')}`);
  });

  await page.goto(`${BASE}/admin/login`, { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('#username', { timeout: 20000 });
  await page.type('#username', USER, { delay: 5 });
  await page.type('#password', PW, { delay: 5 });
  await Promise.all([
    page.click('button[type="submit"]'),
    page.waitForNavigation({ waitUntil: 'domcontentloaded' }).catch(() => {}),
  ]);

  if (page.url().includes('login')) {
    console.error('FATAL: could not log in');
    await browser.close();
    process.exit(1);
  }

  console.log(`logged in, now visiting ${ROUTES.length} routes\n`);

  for (const route of ROUTES) {
    bucket = [];
    http = [];
    try {
      await page.goto(BASE + route, { waitUntil: 'domcontentloaded', timeout: 45000 });
    } catch (e) {
      failures.push(`${route}: navigation failed (${e.message})`);
      continue;
    }

    // Long enough for react-query to settle and charts to mount.
    await new Promise((r) => setTimeout(r, 2500));

    const probe = await page.evaluate(() => {
      const root = document.getElementById('root');
      return {
        children: root ? root.children.length : -1,
        textLen: root ? (root.textContent || '').trim().length : -1,
        htmlLen: root ? root.innerHTML.length : -1,
      };
    });

    const real = bucket.filter((b) => !/Download the React DevTools/.test(b));

    if (real.length) {
      failures.push(`${route}: ${real.map(describe).join(' | ')}`);
    }
    if (probe.children <= 0 || probe.textLen < 40) {
      failures.push(
        `${route}: rendered nothing (children=${probe.children}, ` +
        `textLen=${probe.textLen}, htmlLen=${probe.htmlLen}) -- blank page`
      );
    }

    const bad = real.length > 0 || probe.textLen < 40;
    console.log(
      `${bad ? 'FAIL' : 'ok  '} ${route.padEnd(28)} ` +
      `text=${String(probe.textLen).padStart(5)}  html=${String(probe.htmlLen).padStart(6)}` +
      (real.length ? `  errors: ${real.length}` : '') +
      (http.length ? `  http>=400: ${http.join(', ')}` : '')
    );
  }

  // The font check. index.html used to link Inter from fonts.googleapis.com,
  // which `style-src 'self'` blocks and which is unreachable from Iran, so the
  // font silently never loaded anywhere. `document.fonts.check` is the only
  // assertion that would have noticed, because the fallback stack renders
  // perfectly and nothing looks wrong.
  const fonts = await page.evaluate(async () => {
    await document.fonts.ready;
    const family = getComputedStyle(document.body).fontFamily;
    // `document.fonts.check()` is not sufficient: it returns true when no face
    // in the set matches the request at all, so it passes even when the font
    // never loaded. The bytes on the network are the real evidence.
    const fonts = performance.getEntriesByType('resource')
      .map((e) => e.name)
      .filter((n) => /\.(woff2?|ttf|otf)(\?|$)/i.test(n));
    return {
      family,
      fontFiles: fonts,
      faceCount: document.fonts.size,
      remoteStylesheets: [...document.querySelectorAll('link[rel="stylesheet"]')]
        .map((l) => l.href)
        .filter((h) => !h.startsWith(location.origin)),
    };
  });

  const woff = fonts.fontFiles.filter((f) => /\.woff2?(\?|$)/i.test(f));
  if (!woff.length) {
    failures.push(
      'font: no woff2 was fetched, so the panel is rendering in the fallback ' +
      `stack (body family ${fonts.family})`
    );
  }
  if (fonts.remoteStylesheets.length) {
    failures.push(
      `font: external stylesheet(s) still referenced: ${fonts.remoteStylesheets.join(', ')}`
    );
  }
  if (!/Inter/.test(fonts.family)) {
    failures.push(`font: Inter is not first in the body font stack (${fonts.family})`);
  }
  console.log(
    `\nfont: ${woff.length ? 'ok' : 'MISSING'} -- ` +
    `${woff.length} woff2 fetched from this origin, ` +
    `${fonts.remoteStylesheets.length} remote stylesheet(s)`
  );

  await browser.close();

  console.log('');
  if (failures.length) {
    console.log(`FAILED -- ${failures.length} problem(s):`);
    for (const f of failures) console.log(`  - ${f}`);
    process.exit(1);
  }
  console.log(`all ${ROUTES.length} routes rendered with no uncaught errors`);
})().catch((e) => {
  console.error('FATAL:', e.message);
  process.exit(1);
});