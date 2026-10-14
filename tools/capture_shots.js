/**
 * Capture the panel's key screens so they can actually be looked at.
 *
 * Every other check here asks a question a screenshot cannot answer -- contrast
 * ratios, computed styles, DOM structure, keyboard reachability. None of them
 * ask whether the thing looks right, and this session's mandate includes
 * designing it. Designing without looking is guesswork with a build step.
 *
 * Shots are taken in both themes, because the two are separate designs that
 * share a token file, and a page that is right in dark and wrong in light is
 * the failure this is most likely to catch.
 *
 * The viewport is 1440x1000 for the desktop shots and 390x844 for the mobile
 * ones; the drawer work this session touched is only reachable at the narrow
 * width, so both are needed.
 */
const puppeteer = require('puppeteer');
const fs = require('fs');

const BASE = process.env.TARGET_BASE || 'https://127.0.0.1:8443';
const OUT = process.env.SHOT_DIR || '/tmp/shots';

const DESKTOP = [
  ['login', '/admin/login', null],
  ['dashboard', '/admin', null],
  ['targets', '/admin/targets', null],
  ['rules', '/admin/rules', null],
  ['audit', '/admin/audit', null],
  ['settings', '/admin/settings', null],
  ['style-guide', '/admin/style-guide', null],
];

const MOBILE = [
  ['login-mobile', '/admin/login', null],
  ['dashboard-mobile', '/admin', null],
  ['targets-mobile', '/admin/targets', null],
];

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function shoot(page, name, path, theme, w, h) {
  await page.setViewport({ width: w, height: h, deviceScaleFactor: 1 });
  await page.evaluate((t) => {
    localStorage.setItem('argus-theme', t);
    document.documentElement.setAttribute('data-theme', t);
  }, theme);
  await page.goto(`${BASE}${path}`, { waitUntil: 'domcontentloaded' });
  await page.evaluate((t) => {
    document.documentElement.setAttribute('data-theme', t);
  }, theme);
  // Let charts, tables and the first paint settle.
  await sleep(1400);
  const file = `${OUT}/${theme}-${name}.png`;
  await page.screenshot({ path: file, fullPage: false });
  console.log(`  ${file}`);
}

(async () => {
  fs.mkdirSync(OUT, { recursive: true });
  const browser = await puppeteer.launch({
    headless: 'new',
    protocolTimeout: 180000,
    args: [
      '--no-sandbox', '--disable-setuid-sandbox', '--disable-dev-shm-usage',
      '--ignore-certificate-errors', '--force-color-profile=srgb',
    ],
  });
  const ctx = await browser.createBrowserContext();
  const page = await ctx.newPage();

  await page.goto(`${BASE}/admin/login`, { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('#username');
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

  for (const theme of ['dark', 'light']) {
    for (const [name, path] of DESKTOP) {
      await shoot(page, name, path, theme, 1440, 1000);
    }
    for (const [name, path] of MOBILE) {
      await shoot(page, name, path, theme, 390, 844);
    }
  }

  // One shot of the mobile navigation drawer, which is the surface the depth
  // fix actually changed and which no other shot can see.
  await page.setViewport({ width: 390, height: 844, deviceScaleFactor: 1 });
  for (const theme of ['dark', 'light']) {
    await page.goto(`${BASE}/admin`, { waitUntil: 'domcontentloaded' });
    await page.evaluate((t) => {
      document.documentElement.setAttribute('data-theme', t);
    }, theme);
    await sleep(900);
    const btn = await page.$('button[aria-label*="menu" i], button[aria-controls*="mobile" i], header button');
    if (btn) {
      await btn.click();
      await sleep(800);
      const file = `${OUT}/${theme}-drawer-mobile.png`;
      await page.screenshot({ path: file });
      console.log(`  ${file}`);
    }
  }

  await browser.close();
  console.log('done');
})().catch((e) => {
  console.error('screenshot run failed:', e && e.message ? e.message : e);
  process.exit(1);
});
