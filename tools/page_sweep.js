/**
 * Visit every page in the nav, then click every interactive element on each.
 *
 * ## Why this exists
 *
 * The gate has `verify-buttons.sh`, which covers three chrome controls: the
 * command palette, the theme toggle and the account menu. There is no check that
 * a button rendered on the targets page, the rules page or the style guide
 * actually does something. "The page loads" and "every control works" are
 * different claims and only the first is covered.
 *
 * ## The false positives this is written to avoid
 *
 * `verify-buttons.sh` documents the mistakes it made:
 *
 *   1. It clicked "Sign out", destroyed its own session, and every later page
 *      was the login form -- so the login form's buttons were reported as
 *      failures.
 *      This first made the SAME mistake. Session-ending controls were deferred
 *      to the end of each page, but the Dashboard is the *first* page, so
 *      signing out there meant pages 2-9 were all measured on the login form
 *      and not one of them was really tested -- 9 pages, 45 clicks, all of them
 *      measuring the same login form. Session-ending controls are now a separate
 *      pass that runs after every other page has been swept, and the session is
 *      asserted before each page so a lost session is loud rather than silent.
 *   2. It read a trigger from a stale element handle, the click threw "not
 *      clickable", and a working button was recorded as dead. Here every click
 *      starts from a fresh page load and re-queries by index, so a handle can
 *      never be stale.
 *   3. It judged the theme toggle by page text, but a theme change is an
 *      attribute on <html> and barely touches the text. Here nothing is judged
 *      by text; an outcome is observed state change.
 *
 * ## ERR_ABORTED is not an error
 *
 * The first run reported the Dashboard's Refresh button as broken:
 * `request failed: /api/stats?days=7 net::ERR_ABORTED`. An aborted request is
 * what a cancelled fetch looks like -- React unmounting, or a navigation racing
 * an in-flight refetch. Counting that as a failure reports a working button as
 * dead, the same class of mistake as the three above. It gets its own outcome so
 * it can be judged rather than assumed either way.
 *
 * ## What counts as "did something"
 *
 * Clicking is not the claim -- *something changed* is.
 *
 *   navigate  the URL changed
 *   dialog    a role=dialog became visible, or aria-expanded flipped false->true
 *   network   the page issued an XHR/fetch, which is what a save or delete does
 *   theme     data-theme on <html> changed
 *   dom       the page's text content changed
 *   aborted   a request was cancelled (see above) -- neither proof nor failure
 *   noop      nothing observable changed
 *   error     the click threw, or logged a console error, or a request failed
 *             for a reason that is not cancellation
 *
 * `noop` is reported, not failed: some controls are legitimately inert in a given
 * state (a "next page" button on the last page). The point is to produce the list
 * for a human to judge, and to be honest that a click was a no-op rather than
 * dressing it up.
 *
 * ## Output
 *
 * A JSON file (SWEEP_JSON_PATH, default /shots/sweep.json) and a human summary on
 * stdout. The first run printed the JSON inline and buried the summary under
 * ~40k characters; it now goes to a file.
 */
const puppeteer = require('puppeteer');
const fs = require('fs');

const BASE = process.env.TARGET_BASE || 'https://127.0.0.1:8443';
const OUT = process.env.SHOT_DIR || '/shots';
const ONLY = process.env.SWEEP_PAGES || '';
const JSON_PATH = process.env.SWEEP_JSON_PATH || `${OUT}/sweep.json`;

// Pages reachable from the nav, plus two that are reachable but not linked from
// the sidebar. A page nobody links to is still a page a URL can reach.
const EXTRA_PAGES = [
  ['telegram-users', '/admin/telegram-users'],
  ['audit', '/admin/audit'],
];

const SEL = [
  'button',
  'a[href]',
  '[role="button"]',
  '[role="tab"]',
  '[role="switch"]',
  '[role="checkbox"]',
  'input:not([type="hidden"]):not([type="submit"])',
  'select',
  'textarea',
].join(',');

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

/**
 * Human label for a descriptor.
 *
 * Takes the plain object describe() returns, not a DOM element: the descriptor
 * has to survive a page reload to be re-found, and the first version called
 * getAttribute() on it and threw on the very first element.
 */
function label(el) {
  const parts = [];
  const push = (s) => { if (s && !parts.includes(s)) parts.push(s); };
  push(el.text);
  push(el.ariaLabel && `[${el.ariaLabel}]`);
  push(el.title && `[${el.title}]`);
  push(el.placeholder && `{${el.placeholder}}`);
  push(el.type && `<${el.type}>`);
  push(el.href && `href=${el.href}`);
  push(el.role && `role=${el.role}`);
  if (!parts.length) push(`<${el.tag}>`);
  return parts.join(' ').slice(0, 80);
}

/** True for anything that would end the session; swept last, and only once. */
function endsSession(el) {
  const t = `${el.text} ${el.ariaLabel} ${el.title}`.toLowerCase();
  return /sign out|log ?out|exit/.test(t);
}

async function login(page) {
  await page.goto(`${BASE}/admin/login`, { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('#username', { timeout: 15000 });
  await page.type('#username', process.env.ADMIN_USERNAME, { delay: 2 });
  await page.type('#password', process.env.ADMIN_PASSWORD, { delay: 2 });
  await page.click('button[type="submit"]');
  const deadline = Date.now() + 20000;
  while (Date.now() < deadline) {
    await sleep(400);
    const s = await page.evaluate(async () =>
      (await fetch('/api/me', { credentials: 'same-origin' })).status);
    if (s === 200) return true;
  }
  return false;
}

/** Is the session still good? Asserted before every page. */
async function sessionOk(page) {
  const s = await page.evaluate(async () => {
    try {
      return (await fetch('/api/me', { credentials: 'same-origin' })).status;
    } catch (e) {
      return 0;
    }
  });
  return s === 200;
}

async function discoverNav(page) {
  await page.goto(`${BASE}/admin`, { waitUntil: 'domcontentloaded' });
  await sleep(1200);
  return page.evaluate(() => {
    const out = [];
    document.querySelectorAll('nav a[href], aside a[href], [role="navigation"] a[href]')
      .forEach((a) => {
        const href = a.getAttribute('href');
        if (href && href.startsWith('/admin')) {
          const text = (a.textContent || '').trim().replace(/\s+/g, ' ');
          if (!out.some((x) => x.href === href)) out.push({ href, text });
        }
      });
    return out;
  });
}

async function describe(page) {
  return page.evaluate((sel) => {
    return Array.from(document.querySelectorAll(sel)).map((el, i) => {
      const r = el.getBoundingClientRect();
      const style = getComputedStyle(el);
      const disabled = el.disabled === true || el.getAttribute('aria-disabled') === 'true';
      // No box means not clickable and not worth clicking: a hidden control
      // that "does nothing" is not a dead button.
      const visible = r.width > 0 && r.height > 0 &&
        style.visibility !== 'hidden' && style.display !== 'none';
      return {
        i,
        tag: el.tagName.toLowerCase(),
        role: el.getAttribute('role') || '',
        type: el.getAttribute('type') || '',
        href: el.getAttribute('href') || '',
        name: el.getAttribute('name') || '',
        disabled,
        visible,
        text: (el.textContent || '').trim().replace(/\s+/g, ' ').slice(0, 60),
        ariaLabel: el.getAttribute('aria-label') || '',
        title: el.getAttribute('title') || '',
        placeholder: el.getAttribute('placeholder') || '',
      };
    });
  }, SEL);
}

/** Fingerprint of everything a click could observably change. */
async function snapshot(page) {
  return page.evaluate(() => {
    const body = document.body.innerText || '';
    return {
      url: location.pathname + location.search,
      theme: document.documentElement.getAttribute('data-theme') || '',
      text: body.replace(/\s+/g, ' ').length,
      textHash: body.replace(/\s+/g, ' ').slice(0, 4000),
      dialogs: Array.from(document.querySelectorAll('[role="dialog"], [role="alertdialog"]'))
        .filter((d) => d.getBoundingClientRect().width > 0).length,
      expanded: Array.from(document.querySelectorAll('[aria-expanded]'))
        .map((d) => d.getAttribute('aria-expanded')).join(','),
    };
  });
}

/**
 * Click one element on a freshly loaded page and record what changed.
 * `deferSessionEnders` is false for the final pass that tests sign-out itself.
 */
async function clickAndObserve(page, href, el, attach) {
  const row = { page: el.__page, href, index: el.i, ...el, outcome: null, detail: '' };
  const consoleErrors = [];
  const failedRequests = [];
  const aborted = [];
  const requests = [];

  const onConsole = (m) => { if (m.type() === 'error') consoleErrors.push(m.text().slice(0, 200)); };
  const onRequest = (r) => {
    if (['xhr', 'fetch'].includes(r.resourceType())) {
      requests.push(`${r.method()} ${r.url().replace(BASE, '')}`);
    }
  };
  const onFailed = (r) => {
    const f = r.failure();
    const why = f ? f.errorText : '?';
    const where = r.url().replace(BASE, '');
    // net::ERR_ABORTED is a cancelled request, not a broken one.
    if (String(why).includes('ERR_ABORTED')) aborted.push(where);
    else failedRequests.push(`${where} ${why}`);
  };

  await page.goto(`${BASE}${href}`, { waitUntil: 'domcontentloaded' });
  await sleep(1100);
  attach(onConsole, onRequest, onFailed);

  try {
    const handles = await page.$$(SEL);
    const target = handles[el.i];
    if (!target) {
      row.outcome = 'error';
      row.detail = 'element vanished after reload';
    } else {
      const prev = await snapshot(page);
      await target.click();
      await sleep(1000);
      const now = await snapshot(page);

      if (consoleErrors.length) {
        row.outcome = 'error';
        row.detail = `console: ${consoleErrors[0]}`;
      } else if (failedRequests.length) {
        row.outcome = 'error';
        row.detail = `request failed: ${failedRequests[0]}`;
      } else if (now.url !== prev.url) {
        row.outcome = 'navigate';
        row.detail = `${prev.url} -> ${now.url}`;
      } else if (now.dialogs > prev.dialogs) {
        row.outcome = 'dialog';
        row.detail = `${prev.dialogs} -> ${now.dialogs} dialog(s)`;
      } else if (now.expanded !== prev.expanded) {
        row.outcome = 'dialog';
        row.detail = `aria-expanded ${prev.expanded || '-'} -> ${now.expanded || '-'}`;
      } else if (now.theme !== prev.theme) {
        row.outcome = 'theme';
        row.detail = `${prev.theme || '-'} -> ${now.theme || '-'}`;
      } else if (requests.length) {
        row.outcome = 'network';
        row.detail = requests.slice(0, 2).join(', ');
      } else if (now.textHash !== prev.textHash) {
        row.outcome = 'dom';
        row.detail = `${prev.text} -> ${now.text} chars of text`;
      } else if (aborted.length) {
        row.outcome = 'aborted';
        row.detail = `cancelled: ${aborted.slice(0, 2).join(', ')}`;
      } else {
        row.outcome = 'noop';
        row.detail = 'no observable change';
      }
    }
  } catch (err) {
    row.outcome = 'error';
    row.detail = (err && err.message ? err.message : String(err)).slice(0, 160);
  }

  // Close anything the click left open. A confirm dialog that stays up
  // intercepts every later click, so without this the first modal button on a
  // page turned every control after it into a timeout -- which reads as "the
  // page is broken" when the page is fine and the harness forgot to dismiss.
  await dismissOverlays(page);

  return row;
}

/** Escape out of a modal or a menu, so the next click lands on the page. */
async function dismissOverlays(page) {
  try {
    await page.keyboard.press('Escape');
    await sleep(150);
    // Some menus close on a click-away rather than Escape.
    const open = await page.evaluate(() =>
      Array.from(document.querySelectorAll('[role="dialog"], [role="menu"], [role="listbox"]'))
        .filter((d) => d.getBoundingClientRect().width > 0).length);
    if (open) {
      await page.mouse.click(4, 4);
      await sleep(150);
    }
  } catch (e) {
    /* the page may be mid-navigation; the next click reloads anyway */
  }
}

(async () => {
  fs.mkdirSync(OUT, { recursive: true });
  const browser = await puppeteer.launch({
    headless: 'new',
    protocolTimeout: 600000,
    args: ['--no-sandbox', '--disable-setuid-sandbox', '--disable-dev-shm-usage',
      '--ignore-certificate-errors', '--force-color-profile=srgb'],
  });
  const ctx = await browser.createBrowserContext();
  const page = await ctx.newPage();

  // A native confirm/alert would block the run indefinitely. Dismiss it and say
  // so, rather than hanging until the protocol timeout.
  page.on('dialog', async (d) => {
    console.log(`   (native dialog: ${d.type()} "${String(d.message()).slice(0, 60)}" -- dismissed)`);
    try { await d.dismiss(); } catch (e) { /* already handled */ }
  });

  let onConsole = null; let onRequest = null; let onFailed = null;
  const attach = (c, r, f) => {
    onConsole = c; onRequest = r; onFailed = f;
    page.on('console', c); page.on('request', r); page.on('requestfailed', f);
  };
  const detach = () => {
    if (onConsole) page.off('console', onConsole);
    if (onRequest) page.off('request', onRequest);
    if (onFailed) page.off('requestfailed', onFailed);
    onConsole = onRequest = onFailed = null;
  };

  if (!(await login(page))) {
    console.error('SWEEP_FATAL: could not log in');
    process.exit(2);
  }

  const nav = await discoverNav(page);
  const pages = [...nav.map((n) => [n.text || n.href, n.href]), ...EXTRA_PAGES];
  const wanted = ONLY ? ONLY.split(',').map((s) => s.trim()) : null;
  const targets = wanted
    ? pages.filter(([name, href]) => wanted.some((w) => name.includes(w) || href.includes(w)))
    : pages;

  console.log(`discovered ${pages.length} pages from the nav; sweeping ${targets.length}`);
  targets.forEach(([n, h]) => console.log(`  ${n}  ->  ${h}`));

  const results = [];
  const sessionEnders = [];

  for (const [name, href] of targets) {
    // A lost session must be loud. If this ever fires, every click below would
    // be measuring the login form and the run would look green while testing
    // nothing -- which is exactly what the first run did.
    if (!(await sessionOk(page))) {
      console.log(`\n!! session lost before ${name}; re-authenticating`);
      if (!(await login(page))) {
        console.error('SWEEP_FATAL: session lost and re-login failed');
        process.exit(2);
      }
    }

    await page.goto(`${BASE}${href}`, { waitUntil: 'domcontentloaded' });
    await sleep(1500);
    await page.screenshot({ path: `${OUT}/sweep-${name.replace(/[^a-z0-9-]/gi, '_')}.png` });

    const before = await snapshot(page);
    const elements = await describe(page);
    const usable = elements.filter((e) => e.visible && !e.disabled);
    const deferred = usable.filter((e) => endsSession(e));
    const rows = [];

    console.log(`\n[${name}] ${href}`);
    console.log(`  ${elements.length} elements, ${usable.length} clickable, ` +
      `${deferred.length} session-ending (deferred)`);

    for (const el of usable.filter((e) => !endsSession(e))) {
      el.__page = name;
      const row = await clickAndObserve(page, href, el, attach);
      detach();
      rows.push(row);
      const mark = { navigate: '->', dialog: '+', network: '~', theme: 'T', dom: 'd', aborted: 'x', noop: '.', error: 'X' };
      console.log(`   ${mark[row.outcome]} [${row.index}] ${label(row)}  -- ${row.detail}`);
    }

    for (const el of deferred) sessionEnders.push({ page: name, href, el });
    results.push({ page: name, href, before, elements, rows });
  }

  // Last pass: the session-ending controls, once, after everything else.
  if (sessionEnders.length) {
    console.log(`\n=== session-ending controls (${sessionEnders.length}), tested last ===`);
    for (const { page: name, href, el } of sessionEnders) {
      el.__page = name;
      const row = await clickAndObserve(page, href, el, attach);
      detach();
      results.push({ page: name, href, before: {}, elements: [el], rows: [row] });
      console.log(`   ${row.outcome} [${row.index}] ${label(row)}  -- ${row.detail}`);
      // Put the session back for anything that follows.
      if (!(await sessionOk(page))) await login(page);
    }
  }

  await browser.close();

  const all = results.flatMap((r) => r.rows);
  const by = (o) => all.filter((r) => r.outcome === o);
  const ORDER = ['navigate', 'dialog', 'network', 'theme', 'dom', 'aborted', 'noop', 'error'];
  console.log('\n================ SUMMARY ================');
  console.log(`pages swept        ${results.length}`);
  console.log(`controls clicked   ${all.length}`);
  for (const o of ORDER) console.log(`  ${o.padEnd(10)} ${by(o).length}`);

  for (const o of ['noop', 'aborted', 'error']) {
    if (!by(o).length) continue;
    console.log(`\n--- ${o} ---`);
    by(o).forEach((r) => console.log(`  ${r.page} [${r.index}] ${label(r)}  -- ${r.detail}`));
  }

  fs.writeFileSync(JSON_PATH, JSON.stringify(results));
  console.log(`\njson: ${JSON_PATH}`);
})().catch((e) => {
  console.error('sweep failed:', e && e.message ? e.message : e);
  process.exit(1);
});