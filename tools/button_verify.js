/**
 * Are the buttons the sweep called "dead" actually broken?
 *
 * The sweep's verdict is unreliable for three specific buttons, and all three
 * are ones a human clicks constantly. Guessing from "no observable effect" would
 * be wrong in both directions, so each is checked against what it is *supposed*
 * to do.
 *
 *  1. "Open command palette" (Ctrl+K search). The sweep reported it dead on
 *     every page with "Node is either not clickable or not an Element" -- which
 *     is a click failure, not a dead button. Almost certainly a stale element
 *     handle: the DOM re-renders between enumerating and clicking.
 *  2. "Switch to light theme". Changing the theme rewrites a CSS custom
 *     property, so the DOM text barely changes. My detector measured text and
 *     markup, not the attribute that actually matters.
 *  3. "Account menu for operator". A dropdown that closes when the next click
 *     lands could be a sequencing problem in the sweep, not a broken trigger.
 */
const puppeteer = require('puppeteer');

const BASE = process.env.TARGET_BASE || 'https://127.0.0.1:8443';
const results = [];

function check(name, ok, detail) {
  results.push({ name, ok });
  console.log(`${ok ? 'ok  ' : 'DEAD'} ${name.padEnd(44)} ${detail}`);
}

(async () => {
  const browser = await puppeteer.launch({
    headless: 'new',
    protocolTimeout: 120000,
    args: ['--no-sandbox', '--disable-setuid-sandbox', '--disable-dev-shm-usage',
           '--ignore-certificate-errors'],
  });
  const ctx = await browser.createBrowserContext();
  const page = await ctx.newPage();
  await page.setViewport({ width: 1440, height: 1000 });
  await page.emulateMediaFeatures([{ name: 'prefers-color-scheme', value: 'dark' }]);

  const themeOf = () => page.evaluate(() =>
    document.documentElement.getAttribute('data-theme'));

  await page.goto(`${BASE}/admin/login`, { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('#username');
  await page.type('#username', process.env.ADMIN_USERNAME, { delay: 5 });
  await page.type('#password', process.env.ADMIN_PASSWORD, { delay: 5 });
  await Promise.all([
    page.click('button[type="submit"]'),
    page.waitForNavigation({ waitUntil: 'domcontentloaded' }).catch(() => {}),
  ]);
  await page.evaluate(() => localStorage.setItem('ns-theme', 'dark'));

  for (const route of ['/admin', '/admin/audit', '/admin/settings']) {
    await page.goto(BASE + route, { waitUntil: 'domcontentloaded' });
    await new Promise((r) => setTimeout(r, 2200));

    // --- 1. command palette, by its real trigger ------------------------
    const trigger = await page.$('[aria-label="Open command palette"]');
    if (!trigger) {
      check(`${route} command palette trigger exists`, false, 'not found');
    } else {
      await trigger.click();
      await new Promise((r) => setTimeout(r, 700));
      const open = await page.evaluate(() => ({
        cmdk: document.querySelectorAll('[cmdk-root]').length,
        dialog: document.querySelectorAll('[role="dialog"]').length,
        items: document.querySelectorAll('[cmdk-item]').length,
      }));
      check(`${route} command palette opens`,
            open.cmdk > 0 && open.items > 0,
            `cmdk=${open.cmdk} dialog=${open.dialog} items=${open.items}`);
      // And it filters, which is the part that would be broken unnoticed.
      if (open.cmdk > 0) {
        const before = open.items;
        await page.keyboard.type('target', { delay: 20 });
        await new Promise((r) => setTimeout(r, 500));
        const after = await page.evaluate(() =>
          document.querySelectorAll('[cmdk-item]').length);
        check(`${route} command palette filters`, after > 0 && after < before,
              `${before} -> ${after} items`);
        await page.keyboard.press('Escape');
        await new Promise((r) => setTimeout(r, 400));
      }
    }

    // --- 2. theme toggle: does data-theme actually change? ---------------
    await page.goto(BASE + route, { waitUntil: 'domcontentloaded' });
    await new Promise((r) => setTimeout(r, 2000));
    const before = await themeOf();
    const toggle = await page.$('[aria-label^="Switch to"]');
    if (!toggle) {
      check(`${route} theme toggle exists`, false, 'not found');
    } else {
      await toggle.click();
      await new Promise((r) => setTimeout(r, 600));
      const after = await themeOf();
      const stored = await page.evaluate(() => localStorage.getItem('ns-theme'));
      check(`${route} theme toggle flips the theme`, after !== before,
            `${before} -> ${after} (stored ${stored})`);
      // Put it back.
      const back = await page.$('[aria-label^="Switch to"]');
      if (back) {
        await back.click();
        await new Promise((r) => setTimeout(r, 500));
      }
    }

    // --- 3. account menu -------------------------------------------------
    await page.goto(BASE + route, { waitUntil: 'domcontentloaded' });
    await new Promise((r) => setTimeout(r, 2000));
    const account = await page.$('[aria-label^="Account menu"]');
    if (!account) {
      check(`${route} account trigger exists`, false, 'not found');
    } else {
      const expandedBefore = await account.evaluate((el) => el.getAttribute('aria-expanded'));
      await account.click();
      await new Promise((r) => setTimeout(r, 700));
      const state = await page.evaluate(() => {
        const trig = document.querySelector('[aria-label^="Account menu"]');
        const menu = document.querySelector('[role="menu"]');
        return {
          expanded: trig ? trig.getAttribute('aria-expanded') : null,
          menuText: menu ? (menu.textContent || '').trim().slice(0, 60) : null,
        };
      });
      check(`${route} account menu opens`,
            state.expanded === 'true' && !!state.menuText,
            `aria-expanded ${expandedBefore} -> ${state.expanded}, menu "${state.menuText}"`);
      await page.keyboard.press('Escape');
      await new Promise((r) => setTimeout(r, 400));
    }
  }

  await browser.close();
  const failed = results.filter((r) => !r.ok);
  console.log(`\n${results.length - failed.length}/${results.length} OK`);
  if (failed.length) {
    failed.forEach((f) => console.log(`  DEAD: ${f.name}`));
    process.exit(1);
  }
})().catch((e) => {
  console.error('FATAL', e.message);
  process.exit(1);
});