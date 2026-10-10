/**
 * Is the theme applied before first paint, or only after React mounts?
 *
 * This cannot be read off a settled screenshot. index.html hardcodes
 * data-theme="dark", so a light-theme user whose pre-paint script failed sees the
 * dark background first and then a jump to light. Nothing throws, nothing fails
 * to build, and no test that hashed the script text could tell -- which is how a
 * broken CSP hash went unnoticed while the header looked correct.
 *
 * So this asserts the behaviour directly: seed a preference, load the page, and
 * read data-theme at the earliest moment the document exists, before React has
 * had a chance to set anything.
 */
const puppeteer = require('puppeteer');

const BASE = process.env.TARGET_BASE || 'https://127.0.0.1:8443';

let failed = false;

function report(label, expected, observed, extra = '') {
  const ok = observed === expected;
  if (!ok) failed = true;
  console.log(
    `${ok ? 'ok  ' : 'FAIL'} ${label.padEnd(34)} ` +
    `data-theme = ${JSON.stringify(observed)} (expected ${JSON.stringify(expected)})${extra}`,
  );
}

(async () => {
  const browser = await puppeteer.launch({
    headless: 'new',
    args: ['--no-sandbox', '--disable-setuid-sandbox', '--disable-dev-shm-usage',
           '--ignore-certificate-errors'],
  });

  // The trace has to be installed before any script in the document runs, which
  // is what evaluateOnNewDocument does. It records the value of data-theme and
  // whether React had mounted at the moment of the change, so a value set by the
  // pre-paint script is distinguishable from one set later by React.
  //
  // Observes `document`, not `document.documentElement`: at document-start the
  // root element does not exist yet, so observing it silently attaches nothing
  // and the trace comes back empty -- which reads as "the script never ran"
  // rather than "the observer was never installed".
  const TRACER = `
    window.__themeTrace = [];
    new MutationObserver(function (records) {
      for (const r of records) {
        window.__themeTrace.push({
          value: document.documentElement
            ? document.documentElement.getAttribute('data-theme')
            : null,
          reactMounted: !!document.querySelector('#root > *'),
        });
      }
    }).observe(document, {
      subtree: true,
      attributes: true,
      attributeFilter: ['data-theme'],
    });
  `;

  const readTheme = (page) =>
    page.evaluate(() => ({
      theme: document.documentElement.getAttribute('data-theme'),
      reactMounted: !!document.querySelector('#root > *'),
      trace: window.__themeTrace || [],
    }));

  // --- the script must actually be served as JavaScript ---------------------
  //
  // This came first because the data-theme assertions below cannot tell you
  // *who* set the attribute. The theme script was once served as index.html with
  // Content-Type text/html, the browser refused to execute it, and the probe
  // still passed: index.html hardcodes data-theme="dark" and React sets it again
  // on mount, so a light-theme user saw the right value in the end and only
  // noticed the flash on the way. Asserting the file is really JavaScript is
  // what makes the rest of this meaningful.
  console.log('--- the theme script is really served ---');
  {
    const ctx = await browser.createBrowserContext();
    const page = await ctx.newPage();
    // Navigate first: the fetch uses a relative URL, and an about:blank page has
    // no origin to resolve it against.
    await page.goto(`${BASE}/admin/login`, { waitUntil: 'domcontentloaded' });
    const r = await page.evaluate(async () => {
      try {
        const res = await fetch('/admin/theme.js', { credentials: 'same-origin' });
        const body = await res.text();
        return {
          status: res.status,
          type: res.headers.get('content-type') || '',
          length: body.length,
          mentions: body.includes('ns-theme') && body.includes('data-theme'),
          head: body.trim().slice(0, 60),
        };
      } catch (e) {
        return { error: e.message };
      }
    });
    const isJs = /\bjavascript\b|\becmascript\b/i.test(r.type);
    const ok = r.status === 200 && isJs && r.mentions;
    if (!ok) failed = true;
    console.log(
      `${ok ? 'ok  ' : 'FAIL'} /admin/theme.js  ` +
      `status=${r.status} content-type=${JSON.stringify(r.type)} ` +
      `bytes=${r.length} looks-like-the-script=${r.mentions}`,
    );
    if (!ok) {
      console.log(`       body starts: ${JSON.stringify(r.head || r.error)}`);
      console.log(
        '       if content-type is text/html the SPA catch-all answered with ' +
        'index.html and the browser will refuse to run the script',
      );
    }
    await ctx.close();
  }
  console.log('');

  // --- a stored preference must win, and must win before React --------------
  for (const stored of ['light', 'dark']) {
    const ctx = await browser.createBrowserContext();
    const page = await ctx.newPage();
    await page.setViewport({ width: 1440, height: 900 });

    // Seed storage on the origin first. localStorage is origin-scoped, so the
    // page has to be on the origin before it can be written -- which is why the
    // seeding is a separate navigation rather than part of the init script.
    await page.goto(`${BASE}/admin/login`, { waitUntil: 'domcontentloaded' });
    await page.evaluate((v) => localStorage.setItem('ns-theme', v), stored);

    const csp = [];
    page.on('console', (m) => {
      if (m.type() === 'error' && /Content Security Policy/.test(m.text())) csp.push(m.text());
    });

    await page.evaluateOnNewDocument(TRACER);
    await page.goto(`${BASE}/admin/login`, { waitUntil: 'domcontentloaded' });
    const early = await readTheme(page);

    report(
      `stored preference "${stored}"`,
      stored,
      early.theme,
      early.reactMounted ? ' [react had already mounted]' : ' [pre-paint]',
    );

    // The decisive one. data-theme being correct is not evidence the script ran:
    // the HTML carries data-theme="dark" and React sets it again on mount. What
    // matters is that the attribute was set *before* React existed, which only
    // the pre-paint script can do.
    const prePaint = early.trace.filter((t) => !t.reactMounted);
    if (prePaint.length) {
      console.log(
        `       set by the pre-paint script ${prePaint.length}x before React ` +
        `mounted (values: ${prePaint.map((t) => JSON.stringify(t.value)).join(', ')})`,
      );
    } else {
      console.log(
        '       NOT set before React mounted, so the value came from the HTML ' +
        'attribute or from React -- the flash is not being prevented',
      );
      failed = true;
    }

    if (csp.length) {
      console.log(`       CSP VIOLATION: ${csp[0].slice(0, 110)}...`);
      failed = true;
    }
    await ctx.close();
  }

  // --- with no stored preference the OS setting decides, also pre-paint ------
  for (const scheme of ['light', 'dark']) {
    const ctx = await browser.createBrowserContext();
    const page = await ctx.newPage();
    await page.setViewport({ width: 1440, height: 900 });
    await page.emulateMediaFeatures([
      { name: 'prefers-color-scheme', value: scheme },
    ]);

    await page.goto(`${BASE}/admin/login`, { waitUntil: 'domcontentloaded' });
    await page.evaluate(() => localStorage.removeItem('ns-theme'));

    await page.evaluateOnNewDocument(TRACER);
    await page.goto(`${BASE}/admin/login`, { waitUntil: 'domcontentloaded' });
    const early = await readTheme(page);

    report(
      `prefers-color-scheme: ${scheme}`,
      scheme,
      early.theme,
      early.reactMounted ? ' [react had already mounted]' : ' [pre-paint]',
    );
    const prePaint = early.trace.filter((t) => !t.reactMounted);
    if (prePaint.length) {
      console.log(
        `       set by the pre-paint script ${prePaint.length}x before React ` +
        `mounted (values: ${prePaint.map((t) => JSON.stringify(t.value)).join(', ')})`,
      );
    } else {
      failed = true;
      console.log('       NOT set before React mounted');
    }
    await ctx.close();
  }

  await browser.close();

  if (failed) {
    console.log('\nFAILED: the theme is not reliably applied before first paint');
    process.exit(1);
  }
  console.log('\nall cases OK: the theme is set before React mounts, with no CSP violation');
})().catch((e) => {
  console.error('FATAL', e.message);
  process.exit(1);
});