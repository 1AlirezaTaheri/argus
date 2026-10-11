/**
 * Drive the Rules screen the way an operator would, and prove it against the
 * server rather than against the DOM.
 *
 * A screen can render perfectly and still be broken -- the list loads, the row
 * shows, the toggle animates -- while the thing the user asked for does not
 * happen. The only honest check for "can I configure the scope of a scan here"
 * is to configure one and read it back.
 *
 * So this:
 *   1. creates a `deny_port` rule through the form, with real typing;
 *   2. reads it back from `GET /api/rules` and asserts it is there;
 *   3. asks `/api/rules/test` for a port on that rule and asserts a refusal;
 *   4. toggles it off and asserts the same request is now allowed;
 *   5. edits the value and asserts the change persisted;
 *   6. deletes it through the dialog and asserts it is gone.
 *
 * Everything is cleaned up, and the final state is asserted so a failure
 * cannot leave a rule behind.
 */
const puppeteer = require('puppeteer');
const crypto = require('crypto');

const BASE = process.env.TARGET_BASE || 'https://127.0.0.1:8443';
const results = [];

function check(name, ok, detail) {
  results.push({ name, ok });
  console.log(`${ok ? 'ok  ' : 'FAIL'} ${name.padEnd(48)} ${detail}`);
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function api(page, path, method, body) {
  return page.evaluate(async (p, m, b) => {
    const res = await fetch(p, {
      method: m,
      credentials: 'same-origin',
      headers: b ? { 'Content-Type': 'application/json' } : undefined,
      body: b ? JSON.stringify(b) : undefined,
    });
    let parsed = null;
    try { parsed = await res.json(); } catch (e) { /* not json */ }
    return { status: res.status, body: parsed };
  }, path, method, body || null);
}

/** The id of the rule we made, found by its unique name. */
async function findRule(page, name) {
  const listed = await api(page, '/api/rules', 'GET');
  const rules = (listed.body && listed.body.rules) || [];
  return rules.find((r) => r.name === name) || null;
}

/** Type into a labelled field the way a person does: focus, then keystrokes. */
async function fill(page, selector, value) {
  const handle = await page.$(selector);
  if (!handle) throw new Error(`no field ${selector}`);
  await handle.click();
  await handle.evaluate((el) => { el.value = ''; });
  await handle.type(value, { delay: 8 });
}

/** Pick an option from the design-system Select by its visible text. */
async function pickOption(page, triggerSel, optionText) {
  const trigger = await page.$(triggerSel);
  if (!trigger) throw new Error(`no select trigger ${triggerSel}`);
  await trigger.click();
  await sleep(500);
  const found = await page.evaluate((text) => {
    const option = [...document.querySelectorAll('[role="option"]')]
      .find((o) => (o.textContent || '').trim().toLowerCase()
        .includes(text.toLowerCase()));
    if (!option) return false;
    option.click();
    return true;
  }, optionText);
  await sleep(500);
  if (!found) {
    const have = await page.$$eval('[role="option"]', (els) =>
      els.map((e) => (e.textContent || '').trim()));
    throw new Error(`no option matching "${optionText}"; had ${JSON.stringify(have)}`);
  }
}

/**
 * Log in, and confirm it by asking the server rather than by watching the URL.
 *
 * The first version did `waitForNavigation` and then checked that the URL was
 * no longer /admin/login. Two things go wrong with that. The login is handled
 * client-side, so there may be no navigation event to wait for and the timeout
 * gets swallowed by a `.catch`; and when it fails, the reason is a 429 from the
 * login limiter, which says nothing at all in that check.
 *
 * So the session is confirmed against `/api/me` and the limiter's answer is
 * reported if there is one. A verification script that cannot say why it failed
 * gets "fixed" by being rerun.
 */
async function login(page) {
  await page.goto(`${BASE}/admin/login`, { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('#username');
  await page.type('#username', process.env.ADMIN_USERNAME, { delay: 3 });
  await page.type('#password', process.env.ADMIN_PASSWORD, { delay: 3 });
  await page.click('button[type="submit"]');

  // Poll the endpoint the app itself uses, rather than inferring from a route.
  const deadline = Date.now() + 20000;
  let last = null;
  while (Date.now() < deadline) {
    await sleep(600);
    last = await page.evaluate(async () => {
      const res = await fetch('/api/me', { credentials: 'same-origin' });
      let body = null;
      try { body = await res.json(); } catch (e) { /* not json */ }
      return {
        status: res.status,
        username: body && body.username ? body.username : null,
        detail: body && body.detail ? String(body.detail) : null,
        onLogin: location.pathname.endsWith('/login'),
        // The banner the login form shows for a refused attempt.
        banner: (document.querySelector('[class*="danger"]') || {}).textContent || '',
      };
    });
    if (last.status === 200) {
      return last;
    }
  }
  throw new Error(
    `login did not establish a session: ${JSON.stringify(last)} ` +
    '(429 means the per-IP limiter is counting failed attempts from an ' +
    'earlier probe; wait a minute and re-run)',
  );
}

(async () => {
  const browser = await puppeteer.launch({
    headless: 'new',
    protocolTimeout: 180000,
    args: ['--no-sandbox', '--disable-setuid-sandbox', '--disable-dev-shm-usage',
           '--ignore-certificate-errors'],
  });
  const ctx = await browser.createBrowserContext();
  const page = await ctx.newPage();
  await page.setViewport({ width: 1440, height: 1100 });
  await page.emulateMediaFeatures([{ name: 'prefers-color-scheme', value: 'dark' }]);

  const csp = [];
  const consoleErrors = [];
  page.on('console', (m) => {
    if (m.type() !== 'error') return;
    const t = m.text();
    if (/Content Security Policy/.test(t)) csp.push(t);
    else if (!/favicon|Failed to load resource/.test(t)) consoleErrors.push(t);
  });
  page.on('pageerror', (e) => consoleErrors.push(`[pageerror] ${e.message}`));

  // --- log in --------------------------------------------------------------
  const session = await login(page);
  check('logged in', session.status === 200,
        `${session.status} as ${session.username}`);

  const name = `ui-probe-${crypto.randomBytes(4).toString('hex')}`;
  let createdId = null;

  try {
    // --- the page renders ---------------------------------------------------
    await page.goto(`${BASE}/admin/rules`, { waitUntil: 'domcontentloaded' });
    await sleep(2500);

    check('/admin/rules is not a redirect to login', !page.url().includes('login'),
          page.url().replace(BASE, ''));

    const heading = await page.evaluate(() =>
      (document.querySelector('h1') || {}).textContent || '');
    check('the page has a heading', heading.trim() === 'Rules', heading.trim());

    const hasTester = await page.evaluate(() =>
      (document.body.innerText || '').includes('Ask the engine'));
    check('the tester is present', hasTester, hasTester ? '' : 'not found');

    // --- create ------------------------------------------------------------
    const newRuleButton = await page.$$eval('button', (els) => {
      const index = els.findIndex(
        (e) => (e.textContent || '').trim() === 'New rule');
      return index;
    });
    check('a "New rule" button exists', newRuleButton >= 0, `index ${newRuleButton}`);

    await page.evaluate(() => {
      const button = [...document.querySelectorAll('button')]
        .find((b) => (b.textContent || '').trim() === 'New rule');
      if (button) button.click();
    });
    await sleep(900);

    const dialogOpen = await page.evaluate(() =>
      !!document.querySelector('[role="dialog"]'));
    check('the dialog opens', dialogOpen, dialogOpen ? '' : 'no [role="dialog"]');

    await fill(page, '#rule-name', name);
    await pickOption(page, '#rule-type', 'Deny port');
    await fill(page, '#rule-value', '23,3389,5900');

    await page.evaluate(() => {
      const submit = [...document.querySelectorAll('[role="dialog"] button')]
        .find((b) => (b.textContent || '').trim() === 'Create rule');
      if (submit) submit.click();
    });
    await sleep(1800);

    const created = await findRule(page, name);
    createdId = created && created.id;
    check('the form created a rule', !!created,
          created ? `id=${created.id} value=${created.value}` : 'not found');

    if (created) {
      check('the value round-tripped intact', created.value === '23,3389,5900',
            created.value);
      check('it defaults to enabled', created.enabled === true,
            `enabled=${created.enabled}`);

      // --- the point of the screen: does the rule take effect? --------------
      const denied = await api(page, '/api/rules/test', 'POST', {
        target: '192.0.2.1', ports: [23, 3389, 5900, 443],
      });
      check('the engine refuses a denied port',
            denied.body && denied.body.allowed === false,
            `${denied.body && denied.body.allowed} — ${denied.body && denied.body.reason}`);

      // --- the tester, driven from the UI ----------------------------------
      await fill(page, '#test-target', '192.0.2.1');
      await fill(page, '#test-ports', '23,3389,5900,443');
      await page.evaluate(() => {
        const button = [...document.querySelectorAll('button')]
          .find((b) => (b.textContent || '').trim() === 'Ask the engine');
        if (button) button.click();
      });
      await sleep(1500);

      const verdict = await page.evaluate(() => {
        const region = document.querySelector('[role="status"]');
        return region ? (region.innerText || '').replace(/\s+/g, ' ').trim() : null;
      });
      check('the tester reports a refusal', !!verdict && /refused/i.test(verdict),
            (verdict || 'no verdict').slice(0, 90));

      const mentionsRule = !!verdict && verdict.includes(name);
      check('the tester names the deciding rule', mentionsRule,
            mentionsRule ? 'yes' : (verdict || '').slice(0, 90));

      // --- toggle ------------------------------------------------------------
      await page.evaluate((ruleName) => {
        const row = [...document.querySelectorAll('li')]
          .find((li) => (li.textContent || '').includes(ruleName));
        const toggle = row && row.querySelector('[role="switch"]');
        if (toggle) toggle.click();
      }, name);
      await sleep(1500);

      const afterToggle = await findRule(page, name);
      check('the toggle disabled it', afterToggle && afterToggle.enabled === false,
            `enabled=${afterToggle && afterToggle.enabled}`);

      const allowedNow = await api(page, '/api/rules/test', 'POST', {
        target: '192.0.2.1', ports: [23, 3389, 5900, 443],
      });
      check('a disabled rule no longer refuses',
            allowedNow.body && allowedNow.body.allowed === true,
            `${allowedNow.body && allowedNow.body.allowed}`);

      // Back on, so the edit below starts from a live rule.
      await page.evaluate((ruleName) => {
        const row = [...document.querySelectorAll('li')]
          .find((li) => (li.textContent || '').includes(ruleName));
        const toggle = row && row.querySelector('[role="switch"]');
        if (toggle) toggle.click();
      }, name);
      await sleep(1200);

      // --- edit --------------------------------------------------------------
      await page.evaluate((ruleName) => {
        const row = [...document.querySelectorAll('li')]
          .find((li) => (li.textContent || '').includes(ruleName));
        const edit = row && row.querySelector('[aria-label^="Edit"]');
        if (edit) edit.click();
      }, name);
      await sleep(900);

      await fill(page, '#rule-value', '23,3389,5900,8080');
      await page.evaluate(() => {
        const submit = [...document.querySelectorAll('[role="dialog"] button')]
          .find((b) => (b.textContent || '').trim() === 'Save changes');
        if (submit) submit.click();
      });
      await sleep(1800);

      const edited = await findRule(page, name);
      check('the edit persisted',
            edited && edited.value === '23,3389,5900,8080',
            edited ? edited.value : 'missing');

      // --- delete ------------------------------------------------------------
      await page.evaluate((ruleName) => {
        const row = [...document.querySelectorAll('li')]
          .find((li) => (li.textContent || '').includes(ruleName));
        const del = row && row.querySelector('[aria-label^="Delete"]');
        if (del) del.click();
      }, name);
      await sleep(900);

      const confirmDialog = await page.evaluate(() =>
        !!document.querySelector('[role="alertdialog"]'));
      check('delete asks for confirmation first', confirmDialog,
            confirmDialog ? '' : 'no [role="alertdialog"]');

      await page.evaluate(() => {
        const button = [...document.querySelectorAll('[role="alertdialog"] button')]
          .find((b) => /Delete rule/.test(b.textContent || ''));
        if (button) button.click();
      });
      await sleep(1800);

      const gone = await findRule(page, name);
      check('the rule was deleted', gone === null,
            gone ? `still present as id=${gone.id}` : 'absent');
      createdId = null;
    }

    // --- nav and palette ----------------------------------------------------
    await page.goto(`${BASE}/admin`, { waitUntil: 'domcontentloaded' });
    await sleep(2000);
    const navHasRules = await page.evaluate(() =>
      [...document.querySelectorAll('a')].some(
        (a) => (a.getAttribute('href') || '').endsWith('/rules')));
    check('Rules is in the navigation', navHasRules, navHasRules ? '' : 'no link');

    await page.keyboard.down('Control');
    await page.keyboard.press('KeyK');
    await page.keyboard.up('Control');
    await sleep(900);
    await page.keyboard.type('rules', { delay: 25 });
    await sleep(700);
    const paletteHasRules = await page.evaluate(() =>
      [...document.querySelectorAll('[cmdk-item]')].some(
        (i) => /Rules/.test(i.textContent || '')));
    check('Rules is in the command palette', paletteHasRules,
          paletteHasRules ? '' : 'not offered');

    check('no CSP violations', csp.length === 0,
          csp.length ? csp[0].slice(0, 120) : 'none');
    check('no console errors', consoleErrors.length === 0,
          consoleErrors.length ? consoleErrors[0].slice(0, 120) : 'none');

    try {
      await page.screenshot({
        path: '/shots/rules.png', fullPage: true,
      });
      console.log('\ncaptured rules.png');
    } catch (e) {
      console.log(`\nscreenshot failed: ${e.message}`);
    }
  } finally {
    // Never leave a probe behind, whatever happened above.
    if (createdId) {
      await api(page, `/api/rules/${createdId}?confirm=true`, 'DELETE')
        .catch(() => {});
      console.log(`\ncleaned up rule id=${createdId}`);
    }
    await browser.close();
  }

  const failed = results.filter((r) => !r.ok);
  console.log(`\n${results.length - failed.length}/${results.length} OK`);
  if (failed.length) {
    console.log('\nfailures:');
    failed.forEach((f) => console.log(`  - ${f.name}`));
    process.exit(1);
  }
})().catch((e) => {
  console.error('FATAL', e.message);
  process.exit(1);
});