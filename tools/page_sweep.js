/**
 * Visit every page in the nav, then click every interactive element on each.
 *
 * ## Read-only by default, and that is the whole point of this file's design
 *
 * The first version of this sweep clicked *everything*, and on the Telegram Users
 * page that includes the enable/disable switch. Each run flipped all three users
 * off in the production database:
 *
 *   23:33:17 / 23:37:33 / 23:41:49   -> enabled false   (run 1)
 *   23:59:28 / 23:59:45 / 00:00:02   -> enabled true    (run 2, back on)
 *   00:10:14 / 00:10:32 / 00:10:56   -> enabled false   (run 3, left off)
 *
 * The operator's Telegram bot answered "your account has been disabled". The
 * damage was only diagnosable because every one of those writes went through the
 * panel's audit trail, which named the action, the target and the new value --
 * worth noting, because it is the same trail the audit tests were added to
 * protect.
 *
 * A sweep that clicks destructive controls against production is not a test, it
 * is a way of breaking the deployment and finding out afterwards. So:
 *
 *   * controls that change server state are **never clicked**. They are listed
 *     as `skipped-mutating` with the reason, so they are still accounted for and
 *     a human can decide to exercise them deliberately;
 *   * the classifier is a deny-list of roles and verbs, not a list of the ones
 *     that happened to be clicked -- a new "Deactivate" button must be caught by
 *     the verb, not remembered;
 *   * a second guard watches the network. If a click is classified as safe but
 *     issues anything other than a GET, the row is reported as `mutating` and
 *     says so loudly. The classifier is not trusted to be complete; it is
 *     checked.
 *
 * `--allow-mutating` re-enables the clicks, and is opt-in because the safe
 * default is the one worth keeping.
 *
 * ## The other false positive this avoids
 *
 * `verify-buttons.sh` documented three mistakes, and this made the first of them
 * again on its first run: clicking "Sign out" destroyed the session, so pages 2-9
 * were all the login form and the run looked healthy. Session-ending controls are
 * now a separate final pass, and the session is asserted before every page.
 *
 * Clicks also start from a fresh page load and re-query by index, so no element
 * handle is ever stale, and nothing is judged by page text -- a theme change is
 * an attribute on <html> and barely touches the text.
 */
const puppeteer = require('puppeteer');
const fs = require('fs');

const BASE = process.env.TARGET_BASE || 'https://127.0.0.1:8443';
const OUT = process.env.SHOT_DIR || '/shots';
const ONLY = process.env.SWEEP_PAGES || '';
const JSON_PATH = process.env.SWEEP_JSON_PATH || `${OUT}/sweep.json`;

/**
 * Set only by an operator who has decided the risk is worth taking. It exists so
 * that exercising a destructive control is a choice someone makes on purpose,
 * not the default behaviour of a tool that was pointed at production.
 */
const ALLOW_MUTATING = process.env.SWEEP_ALLOW_MUTATING === '1';

// A control whose purpose is to change stored state. Matched on verbs rather
// than on specific labels so a control this list has never seen is still caught.
const MUTATING_VERBS = /\b(remove|delete|purge|erase|destroy|drop|trash|revoke|reset|clear|disable|deactivate|deprovision|suspend|terminate|wipe)\b/i;
const MUTATING_ROLES = new Set(['switch', 'checkbox']);
// Verbs that mean "this changes something", so they are the ones a sweep must not
// press by surprise.
const MUTATING_LABELS = /\b(toggle|enable|disable|activate|deactivate|save|submit|apply|commit|publish|rotate|regenerate|start|stop|restart|scan|import|upload|revoke|ban|mute|unban|add|remove|create|update|edit|rename)\b/i;

function mutatingReason(el) {
  const text = `${el.text || ''} ${el.ariaLabel || ''} ${el.title || ''} ${el.name || ''}`;
  if (MUTATING_ROLES.has(el.role)) return `role=${el.role} is a state toggle`;
  if (MUTATING_VERBS.test(text)) return 'the label names a destructive verb';
  if (MUTATING_LABELS.test(text)) return 'the label names a state-changing verb';
  if (el.type === 'submit') return 'it is a submit button';
  return null;
}

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
  return /sign out|log ?out|exit/i.test(`${el.text} ${el.ariaLabel} ${el.title}`);
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

/**
 * Is the session still good? Asserted before every page.
 *
 * Without this a lost session is silent, and every click below measures the login
 * form -- which is what the first run did, and it looked green.
 */
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
      // No box means not clickable and not worth clicking: a hidden control that
      // "does nothing" is not a dead button.
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

/** Escape out of a modal or menu so the next click lands on the page. */
async function dismissOverlays(page) {
  try {
    await page.keyboard.press('Escape');
    await sleep(150);
    const open = await page.evaluate(() =>
      Array.from(document.querySelectorAll('[role="dialog"], [role="menu"], [role="listbox"]'))
        .filter((d) => d.getBoundingClientRect().width > 0).length);
    if (open) {
      await page.mouse.click(4, 4);
      await sleep(150);
    }
  } catch (e) {
    /* mid-navigation; the next click reloads anyway */
  }
}

/** Close anything a click left open, or it intercepts every later click. */
async function clickAndObserve(page, href, el, attach) {
  const row = { page: el.__page, href, index: el.i, ...el, outcome: null, detail: '' };
  const consoleErrors = [];
  const failedRequests = [];
  const aborted = [];
  const requests = [];
  const writes = [];

  const onConsole = (m) => { if (m.type() === 'error') consoleErrors.push(m.text().slice(0, 200)); };
  const onRequest = (r) => {
    if (!['xhr', 'fetch'].includes(r.resourceType())) return;
    const verb = r.method().toUpperCase();
    const where = r.url().replace(BASE, '');
    requests.push(`${verb} ${where}`);
    // The guard that does not trust the classifier: anything that is not a GET
    // or a HEAD changed stored state.
    if (!['GET', 'HEAD'].includes(verb)) writes.push(`${verb} ${where}`);
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

      if (writes.length) {
        // Reported first, and loudly: whatever else happened, this changed
        // stored state and the operator needs to know.
        row.outcome = 'mutating';
        row.detail = `CHANGED SERVER STATE: ${writes.slice(0, 2).join(', ')}`;
      } else if (consoleErrors.length) {
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

  await dismissOverlays(page);
  return row;
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
  page.on('dialog', async (d) => {
    console.log(`   (native dialog: ${d.type()} -- dismissed)`);
    try { await d.dismiss(); } catch (e) { /* handled */ }
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

  if (!(await login(page))) { console.error('SWEEP_FATAL: could not log in'); process.exit(2); }

  console.log(`ALLOW_MUTATING=${ALLOW_MUTATING ? 'yes -- this run WILL change server state' : 'no -- destructive controls are listed, not clicked'}`);

  const nav = await discoverNav(page);
  const pages = [...nav.map((n) => [n.text || n.href, n.href]), ...EXTRA_PAGES];
  const wanted = ONLY ? ONLY.split(',').map((s) => s.trim()) : null;
  const targets = wanted
    ? pages.filter(([name, href]) => wanted.some((w) => name.includes(w) || href.includes(w)))
    : pages;

  console.log(`discovered ${pages.length} pages from the nav; sweeping ${targets.length}`);

  const results = [];
  const sessionEnders = [];
  const skippedMutating = [];

  for (const [name, href] of targets) {
    if (!(await sessionOk(page))) {
      console.log(`\n!! session lost before ${name}; re-authenticating`);
      if (!(await login(page))) { console.error('SWEEP_FATAL: cannot re-authenticate'); process.exit(2); }
    }

    await page.goto(`${BASE}${href}`, { waitUntil: 'domcontentloaded' });
    await sleep(1500);
    await page.screenshot({ path: `${OUT}/sweep-${name.replace(/[^a-z0-9-]/gi, '_')}.png` });

    const before = await snapshot(page);
    const elements = await describe(page);
    const usable = elements.filter((e) => e.visible && !e.disabled);
    const rows = [];

    const safe = [];
    for (const el of usable) {
      if (endsSession(el)) { sessionEnders.push({ page: name, href, el }); continue; }
      const why = ALLOW_MUTATING ? null : mutatingReason(el);
      if (why) {
        skippedMutating.push({ page: name, index: el.i, label: label(el), reason: why });
        continue;
      }
      safe.push(el);
    }

    console.log(`\n[${name}] ${href}`);
    console.log(`  ${elements.length} elements, ${usable.length} clickable, `
      + `${safe.length} clicked, ${skippedMutating.filter((s) => s.page === name).length} skipped as mutating, `
      + `${usable.length - safe.length - sessionEnders.filter((s) => s.page === name).length} session-ending`);

    for (const el of safe) {
      el.__page = name;
      const row = await clickAndObserve(page, href, el, attach);
      detach();
      rows.push(row);
      const mark = { navigate: '->', dialog: '+', network: '~', theme: 'T', dom: 'd',
        aborted: 'x', noop: '.', error: 'X', mutating: '!!' };
      console.log(`   ${mark[row.outcome]} [${row.index}] ${label(row)}  -- ${row.detail}`);
    }

    results.push({ page: name, href, before, elements, rows });
  }

  if (sessionEnders.length) {
    console.log(`\n=== session-ending controls (${sessionEnders.length}), tested last ===`);
    for (const { page: name, href, el } of sessionEnders) {
      el.__page = name;
      const row = await clickAndObserve(page, href, el, attach);
      detach();
      results.push({ page: name, href, before: {}, elements: [el], rows: [row] });
      console.log(`   ${row.outcome} [${row.index}] ${label(row)}  -- ${row.detail}`);
      if (!(await sessionOk(page))) await login(page);
    }
  }

  await browser.close();

  const all = results.flatMap((r) => r.rows);
  const by = (o) => all.filter((r) => r.outcome === o);
  const ORDER = ['navigate', 'dialog', 'network', 'theme', 'dom', 'aborted', 'noop', 'mutating', 'error'];

  console.log('\n================ SUMMARY ================');
  console.log(`pages swept        ${results.length}`);
  console.log(`controls clicked   ${all.length}`);
  for (const o of ORDER) console.log(`  ${o.padEnd(10)} ${by(o).length}`);

  console.log(`\nnot clicked because they change stored state: ${skippedMutating.length}`);
  skippedMutating.forEach((s) => console.log(`  ${s.page} [${s.index}] ${s.label}  -- ${s.reason}`));

  const wrote = by('mutating');
  if (wrote.length) {
    console.log('\n!!! CONTROLS THAT CHANGED SERVER STATE DESPITE BEING CLASSIFIED SAFE !!!');
    wrote.forEach((r) => console.log(`  ${r.page} [${r.index}] ${label(r)}  -- ${r.detail}`));
    console.log('  The classifier missed these. Treat them as destructive and add them to it.');
  }

  for (const o of ['noop', 'aborted', 'error']) {
    if (!by(o).length) continue;
    console.log(`\n--- ${o} ---`);
    by(o).forEach((r) => console.log(`  ${r.page} [${r.index}] ${label(r)}  -- ${r.detail}`));
  }

  fs.writeFileSync(JSON_PATH, JSON.stringify({ results, skippedMutating }));
  console.log(`\njson: ${JSON_PATH}`);
  // Non-zero if anything wrote, so this cannot be run against production without
  // someone noticing that it now reports what it did.
  process.exit(wrote.length ? 1 : 0);
})().catch((e) => {
  console.error('sweep failed:', e && e.message ? e.message : e);
  process.exit(1);
});