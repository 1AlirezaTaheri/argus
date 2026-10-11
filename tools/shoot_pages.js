/**
 * Capture the pages a design change has to be judged on, in both themes.
 *
 * A depth treatment is a claim about light and shadow, and it cannot be judged
 * from a screenshot of a populated data table: the interesting surfaces are the
 * login page (the one the user called out), the rules page (new), and a page
 * with real elevation stacking.
 *
 * Both themes are captured because a shadow tuned on a near-black background is
 * nearly invisible -- the failure mode of every "3D" pass that is only checked
 * in dark mode.
 */
const puppeteer = require('puppeteer');
const path = require('path');

const BASE = process.env.TARGET_BASE || 'https://127.0.0.1:8443';
const OUT = process.env.SHOT_DIR || '/shots';

const PAGES = [
  { name: 'login', route: '/admin/login', auth: false },
  { name: 'dashboard', route: '/admin', auth: true },
  { name: 'rules', route: '/admin/rules', auth: true },
  { name: 'rules-empty', route: '/admin/rules', auth: true },
  { name: 'settings', route: '/admin/settings', auth: true },
  { name: 'targets', route: '/admin/targets', auth: true },
];

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

(async () => {
  const browser = await puppeteer.launch({
    headless: 'new',
    protocolTimeout: 120000,
    args: ['--no-sandbox', '--disable-setuid-sandbox', '--disable-dev-shm-usage',
           '--ignore-certificate-errors', '--force-color-profile=srgb',
           '--hide-scrollbars'],
  });
  const ctx = await browser.createBrowserContext();
  const page = await ctx.newPage();
  await page.setViewport({ width: 1440, height: 1000, deviceScaleFactor: 2 });

  for (const theme of ['dark', 'light']) {
    await page.goto(`${BASE}/admin/login`, { waitUntil: 'domcontentloaded' });
    await page.evaluate((t) => localStorage.setItem('ns-theme', t), theme);

    for (const spec of PAGES) {
      if (spec.auth) {
        await page.goto(`${BASE}/admin/login`, { waitUntil: 'domcontentloaded' });
        await page.waitForSelector('#username');
        await page.type('#username', process.env.ADMIN_USERNAME, { delay: 3 });
        await page.type('#password', process.env.ADMIN_PASSWORD, { delay: 3 });
        await Promise.all([
          page.click('button[type="submit"]'),
          page.waitForNavigation({ waitUntil: 'domcontentloaded' }).catch(() => {}),
        ]);
      }
      await page.evaluate((t) => localStorage.setItem('ns-theme', t), theme);

      await page.goto(BASE + spec.route, { waitUntil: 'domcontentloaded' });
      await sleep(spec.auth ? 2600 : 1200);

      const file = path.join(OUT, `${spec.name}-${theme}.png`);
      await page.screenshot({ path: file });
      const applied = await page.evaluate(() =>
        document.documentElement.getAttribute('data-theme'));
      console.log(`${spec.name}-${theme}.png  (data-theme=${applied})`);
    }
  }

  // A tight crop of the login card, where the depth treatment is most visible.
  await page.goto(`${BASE}/admin/login`, { waitUntil: 'domcontentloaded' });
  for (const theme of ['dark', 'light']) {
    await page.evaluate((t) => localStorage.setItem('ns-theme', t), theme);
    await page.reload({ waitUntil: 'domcontentloaded' });
    await sleep(1000);
    await page.screenshot({ path: path.join(OUT, `login-card-${theme}.png`) });
    console.log(`login-card-${theme}.png`);
  }

  await browser.close();
})().catch((e) => {
  console.error('FATAL', e.message);
  process.exit(1);
});