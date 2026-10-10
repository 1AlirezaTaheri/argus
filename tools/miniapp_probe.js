/**
 * Does the Mini App's pre-paint script actually run, and set the palette before
 * React mounts?
 *
 * Checking `--tg-bg` after load proves nothing on its own: the CSS declares a
 * light `:root` palette, so the computed value reads the same whether the script
 * ran or not. What distinguishes them is *when* the value was set, so this
 * watches the property from document-start and checks it was set before the app
 * mounted.
 *
 * The 404 on /favicon.ico is excluded: the Mini App declares no icon, so the
 * browser requests one by default. That is cosmetic and is asserted separately
 * rather than folded into the theme check.
 */
const puppeteer = require('puppeteer');
const path = require('path');

const BASE = process.env.TARGET_BASE || 'https://127.0.0.1:8443';
const OUT = process.env.SHOT_DIR || '/shots';

const results = [];
function check(name, ok, detail) {
  results.push({ name, ok });
  console.log(`${ok ? 'ok  ' : 'FAIL'} ${name.padEnd(46)} ${detail}`);
}

(async () => {
  const browser = await puppeteer.launch({
    headless: 'new',
    args: ['--no-sandbox', '--disable-setuid-sandbox', '--disable-dev-shm-usage',
           '--ignore-certificate-errors', '--force-color-profile=srgb'],
  });
  const ctx = await browser.createBrowserContext();
  const page = await ctx.newPage();
  await page.setViewport({ width: 390, height: 844, deviceScaleFactor: 2,
                           isMobile: true, hasTouch: true });

  // Telegram is not present in a plain browser, so the script takes its fallback
  // path -- which is still the path that has to work, and the one whose absence
  // produced a white flash for real users.
  //
  // `document.documentElement` is null at document-start, so the first version of
  // this called getComputedStyle on null and threw. It threw on *every* page
  // load, which the console check then reported as the app's error, and the
  // trace came back empty so the "set before mount" assertion failed for the
  // wrong reason. Guarded now, and `mounted` is the thing that actually matters:
  // it is false before React commits, which is the window being tested.
  await page.evaluateOnNewDocument(`
    window.__themeTrace = [];
    const read = () => {
      const el = document.documentElement;
      if (!el) return '';
      return getComputedStyle(el).getPropertyValue('--tg-bg').trim();
    };
    const record = (why) => {
      const el = document.documentElement;
      window.__themeTrace.push({
        value: read(),
        why,
        mounted: !!(document.querySelector('#root > *')),
        inline: el ? (el.getAttribute('style') || '') : '',
      });
    };
    record('document-start');
    try {
      new MutationObserver(() => record('mutation')).observe(document, {
        subtree: true, attributes: true, attributeFilter: ['style'],
      });
    } catch (e) { /* nothing to observe yet */ }
    document.addEventListener('DOMContentLoaded', () => record('domcontentloaded'));
    window.addEventListener('load', () => record('load'));
  `);

  const csp = [];
  const other = [];
  const failed = [];
  page.on('console', (m) => {
    if (m.type() !== 'error' && m.type() !== 'warning') return;
    const t = m.text();
    if (/Content Security Policy/.test(t)) csp.push(t);
    else if (!/DevTools|Download the React|favicon/.test(t)) other.push(`[${m.type()}] ${t}`);
  });
  page.on('pageerror', (e) => other.push(`[pageerror] ${e.message}`));
  page.on('response', (r) => {
    if (r.status() >= 400) failed.push(`${r.status()} ${r.url().replace(BASE, '')}`);
  });

  const resp = await page.goto(`${BASE}/app`, { waitUntil: 'domcontentloaded' });
  check('GET /app responds', resp.status() === 200, `HTTP ${resp.status()}`);
  await new Promise((r) => setTimeout(r, 2500));

  const state = await page.evaluate(() => {
    const inline = [...document.querySelectorAll('script')].filter((s) => !s.src);
    return {
      inlineCount: inline.length,
      external: [...document.querySelectorAll('script[src]')].map((s) => s.src),
      rootChildren: document.querySelector('#root')
        ? document.querySelector('#root').children.length
        : -1,
      textLen: (document.body.innerText || '').trim().length,
      trace: window.__themeTrace || [],
      inlineStyleOnHtml: document.documentElement.getAttribute('style') || '',
    };
  });

  check('no inline script remains', state.inlineCount === 0,
        `${state.inlineCount} inline, external: ${JSON.stringify(state.external)}`);
  check('the theme script is loaded as a file',
        state.external.some((s) => s.includes('/app/theme.js')),
        state.external.map((s) => s.replace(BASE, '')).join(', '));
  check('the app renders', state.rootChildren > 0 && state.textLen > 20,
        `children=${state.rootChildren} text=${state.textLen}`);

  // The decisive assertion. The value alone proves nothing -- the stylesheet
  // declares the same light default -- so what is checked is that the *inline*
  // style was written while the app was still unmounted. That is only possible
  // from the pre-paint script, since the module bundle and React both run after
  // the document is parsed.
  const setEarly = state.trace.find(
    (t) => !t.mounted && typeof t.inline === 'string' && t.inline.includes('--tg-bg'),
  );
  check('the palette is written before the app mounts', !!setEarly,
        setEarly
          ? `html style gained --tg-bg at "${setEarly.why}" (mounted=${setEarly.mounted})`
          : `trace: ${JSON.stringify(state.trace.map((t) => ({ w: t.why, m: t.mounted, i: (t.inline || '').slice(0, 30) })))}`);
  check('the script wrote to the element (not just the stylesheet)',
        state.inlineStyleOnHtml.includes('--tg-bg'),
        `html style: ${JSON.stringify(state.inlineStyleOnHtml.slice(0, 80))}`);

  check('no CSP violations', csp.length === 0,
        csp.length ? csp[0].slice(0, 140) : 'none');
  // "Failed to load resource" is Chrome announcing a non-2xx response, which the
  // dedicated request check below already accounts for -- counting it twice
  // reported the favicon 404 as a console error too, and the check could not
  // distinguish a missing icon from a real failure.
  const realErrors = other.filter((e) => !/Failed to load resource/.test(e));
  check('no other console errors', realErrors.length === 0,
        realErrors.length ? realErrors[0].slice(0, 140) : 'none');

  const realFailed = [...new Set(failed)].filter((f) => !/favicon/.test(f));
  check('no failed requests', realFailed.length === 0,
        realFailed.length ? realFailed.join(', ') : `none (${failed.length} favicon)`);

  try {
    await page.screenshot({ path: path.join(OUT, 'miniapp.png') });
    console.log('\ncaptured miniapp.png');
  } catch (e) {
    console.log(`\nscreenshot failed: ${e.message}`);
  }

  await browser.close();
  const bad = results.filter((r) => !r.ok);
  console.log(`\n${results.length - bad.length}/${results.length} OK`);
  if (bad.length) process.exit(1);
})().catch((e) => {
  console.error('FATAL', e.message);
  process.exit(1);
});