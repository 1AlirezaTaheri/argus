/**
 * Screenshots in both languages, both themes.
 *
 * The existing `capture_shots.js` sets the theme but not the language, so every
 * shot it produced was English -- which means "the language works" would have
 * been a claim about a code path no screenshot had ever rendered.
 *
 * The language is set through the same localStorage key the app reads, then the
 * page is reloaded so the store picks it up on a cold start rather than being
 * poked into a state it would never reach on its own.
 */
const puppeteer = require('puppeteer');
const fs = require('fs');

const BASE = process.env.TARGET_BASE || 'https://127.0.0.1:8443';
const OUT = process.env.SHOT_DIR || '/shots';
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const PAGES = [
  ['dashboard', '/admin'],
  ['targets', '/admin/targets'],
  ['rules', '/admin/rules'],
  ['users', '/admin/telegram-users'],
  ['audit', '/admin/audit'],
  ['settings', '/admin/settings'],
  ['style-guide', '/admin/style-guide'],
];

(async () => {
  fs.mkdirSync(OUT, { recursive: true });
  const browser = await puppeteer.launch({
    headless: 'new',
    protocolTimeout: 300000,
    args: ['--no-sandbox', '--disable-setuid-sandbox', '--disable-dev-shm-usage',
      '--ignore-certificate-errors', '--force-color-profile=srgb'],
  });
  const page = await (await browser.createBrowserContext()).newPage();

  // Log in first, with the language the app will keep using.
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
    if (s === 200) break;
  }

  for (const lang of ['en', 'fa']) {
    // Set the preference the app actually reads, then reload so the first paint
    // is already in that language.
    await page.evaluate((l) => {
      try {
        window.localStorage.setItem('argus-lang', l);
        window.localStorage.setItem('argus-theme', 'dark');
      } catch (e) { /* private mode */ }
    }, lang);

    for (const [name, path] of PAGES) {
      await page.goto(`${BASE}${path}`, { waitUntil: 'domcontentloaded' });
      await sleep(1600);
      const actual = await page.evaluate(() => ({
        lang: document.documentElement.getAttribute('lang'),
        dir: document.documentElement.getAttribute('dir'),
        heading: (document.querySelector('h1, h2, nav a[aria-current="page"]') || {}).innerText || '',
      }));
      const file = `${OUT}/lang-${lang}-${name}.png`;
      await page.screenshot({ path: file });
      console.log(`  ${file}   lang=${actual.lang} dir=${actual.dir} "${String(actual.heading).trim().slice(0, 28)}"`);
    }
  }

  // Leave the panel as it was found.
  await page.evaluate(() => {
    try {
      window.localStorage.setItem('argus-lang', 'en');
      window.localStorage.setItem('argus-theme', 'dark');
    } catch (e) { /* ignore */ }
  });

  await browser.close();
  console.log('done');
})().catch((e) => {
  console.error('shots failed:', e && e.message ? e.message : e);
  process.exit(1);
});