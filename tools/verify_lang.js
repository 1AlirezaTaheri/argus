/**
 * Does the panel's language selector actually work?
 *
 * Three separate claims, kept separate because they fail differently:
 *
 *   1. **Parity.** Every key exists in both languages, with matching
 *      placeholders and no blank values. Checked by calling the module's own
 *      `checkParity()` inside the page, so it runs against the shipped bundle
 *      rather than the source on disk.
 *   2. **Effect.** Switching the language changes the strings that are actually
 *      rendered. Measured by reading the sidebar's text before and after, not
 *      by trusting that a dropdown updated a store.
 *   3. **Persistence and direction.** The choice survives a reload, and Persian
 *      sets `dir="rtl"` -- a Persian sentence laid out left-to-right is misread,
 *      not merely ugly.
 *
 * The store writes to localStorage under `argus-lang`, so the reload test sets
 * the language, reloads, and reads it back.
 */
const puppeteer = require('puppeteer');

const BASE = process.env.TARGET_BASE || 'https://127.0.0.1:8443';
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const checks = [];
function check(name, ok, detail) {
  checks.push({ name, ok: !!ok, detail });
  console.log(`  ${ok ? 'ok  ' : 'FAIL'} ${name}${detail ? '  -- ' + detail : ''}`);
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
 * The sidebar's link labels, in order.
 *
 * Bound to `page` at definition time. The first version returned
 * `page => page.evaluate(...)` and was then called as `labels()`, so the
 * parameter arrived undefined and the probe threw before it measured anything.
 */
const navLabels = (page) => async () => page.evaluate(() => {
  const links = Array.from(document.querySelectorAll('nav a[href]'));
  return links.map((a) => (a.innerText || '').trim().replace(/\s+/g, ' ')).filter(Boolean);
});

(async () => {
  const browser = await puppeteer.launch({
    headless: 'new',
    protocolTimeout: 600000,
    args: ['--no-sandbox', '--disable-setuid-sandbox', '--disable-dev-shm-usage',
      '--ignore-certificate-errors', '--force-color-profile=srgb'],
  });
  const page = await (await browser.createBrowserContext()).newPage();
  page.on('dialog', async (d) => { try { await d.dismiss(); } catch (e) {} });
  if (!(await login(page))) { console.error('login failed'); process.exit(2); }

  // -------------------------------------------------------------------
  // 1. Parity, from the module running in the page
  // -------------------------------------------------------------------
  console.log('\n=== dictionary parity ===');
  await page.goto(`${BASE}/admin`, { waitUntil: 'domcontentloaded' });
  await sleep(2500);

  const parity = await page.evaluate(async () => {
    const fn = window.__argusLangCheck;
    if (typeof fn !== 'function') return { present: false };
    return { present: true, problems: await fn() };
  });

  if (!parity.present) {
    check('the panel exposes its parity check', false,
      'window.__argusLangCheck is not defined -- the check module is not in the bundle');
  } else {
    check('every key exists in both languages with the same placeholders',
      parity.problems.length === 0,
      parity.problems.length === 0
        ? 'no problems reported by the module'
        : `${parity.problems.length} problem(s): ${parity.problems.slice(0, 5).join(' | ')}`);
  }

  // -------------------------------------------------------------------
  // 2. The selector exists and switching changes the rendered strings
  // -------------------------------------------------------------------
  console.log('\n=== switching the language changes the page ===');
  const labels = navLabels(page);

  await page.evaluate(() => {
    try { window.localStorage.removeItem('argus-lang'); } catch (e) {}
  });
  await page.reload({ waitUntil: 'domcontentloaded' });
  await sleep(2000);

  const before = await labels();
  check('the sidebar renders labels at all', before.length > 0,
    `${before.length} links: ${JSON.stringify(before)}`);

  const trigger = await page.$('[aria-label="Change language"], [aria-label="تغییر زبان"]');
  if (!trigger) {
    check('a language selector is present', false,
      'no control labelled "Change language" / "تغییر زبان" was found');
  } else {
    check('a language selector is present', true, 'found in the topbar');

    await trigger.click();
    await sleep(600);
    const options = await page.evaluate(() =>
      Array.from(document.querySelectorAll('[role="menuitem"]'))
        .map((o) => (o.innerText || '').trim()));
    check('the selector offers both languages', options.length >= 2,
      `options: ${JSON.stringify(options)}`);

    // Choose Persian by its label, whichever language the menu is currently in.
    const clicked = await page.evaluate(() => {
      const item = Array.from(document.querySelectorAll('[role="menuitem"]'))
        .find((o) => (o.innerText || '').includes('فارسی'));
      if (!item) return false;
      item.dispatchEvent(new MouseEvent('click', { bubbles: true }));
      return true;
    });
    if (!clicked) {
      check('Persian can be selected', false, 'no menu item containing فارسی');
    } else {
      await sleep(1200);
      const after = await labels();
      const changed = before.join('|') !== after.join('|');
      check('selecting Persian changes the rendered navigation', changed,
        changed
          ? `before ${JSON.stringify(before)} -> after ${JSON.stringify(after)}`
          : `the sidebar text is identical in both languages: ${JSON.stringify(after)}`);

      const isPersian = after.some((l) => /[؀-ۿ]/.test(l));
      check('the navigation is actually in Persian script', isPersian,
        `sidebar: ${JSON.stringify(after)}`);

      const doc = await page.evaluate(() => ({
        lang: document.documentElement.getAttribute('lang'),
        dir: document.documentElement.getAttribute('dir'),
      }));
      check('the document declares lang and dir', doc.lang === 'fa' && doc.dir === 'rtl',
        `lang="${doc.lang}" dir="${doc.dir}"`);

      // -----------------------------------------------------------------
      // 3. Persistence across a reload
      // -----------------------------------------------------------------
      const stored = await page.evaluate(() => {
        try { return window.localStorage.getItem('argus-lang'); } catch (e) { return 'ERR'; }
      });
      check('the choice is persisted', stored === 'fa', `localStorage argus-lang = ${JSON.stringify(stored)}`);

      await page.reload({ waitUntil: 'domcontentloaded' });
      await sleep(2000);
      const afterReload = await labels();
      const stillPersian = afterReload.some((l) => /[؀-ۿ]/.test(l));
      check('the language survives a reload', stillPersian,
        `sidebar after reload: ${JSON.stringify(afterReload)}`);

      // And back to English, so the check does not leave the panel in Persian.
      await page.evaluate(() => {
        try { window.localStorage.setItem('argus-lang', 'en'); } catch (e) {}
      });
      await page.reload({ waitUntil: 'domcontentloaded' });
      await sleep(1500);
      const backToEnglish = await labels();
      check('switching back to English restores English labels',
        backToEnglish.join('|') === before.join('|'),
        `now: ${JSON.stringify(backToEnglish)}`);
    }
  }

  await browser.close();
  const failed = checks.filter((c) => !c.ok);
  console.log(`\n  ${checks.length - failed.length} passed, ${failed.length} failed`);
  if (failed.length) {
    console.log('\nFAILING:');
    failed.forEach((f) => console.log(`  ${f.name}  --  ${f.detail}`));
  }
  process.exit(failed.length ? 1 : 0);
})().catch((e) => { console.error('probe failed:', e && e.message); process.exit(2); });