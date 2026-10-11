/**
 * Capture the style guide and the rules page, in both themes, plus a mobile
 * width for the rules screen.
 *
 * The mobile shot is here because the rules layout is a two-column grid at `lg`
 * and the row has five controls on the right; a row that fits at 1440px can
 * still overflow at 390px, and nothing in the type checker knows that.
 */
const puppeteer = require('puppeteer');
const path = require('path');

const BASE = process.env.TARGET_BASE || 'https://127.0.0.1:8443';
const OUT = process.env.SHOT_DIR || '/shots';
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function signIn(page) {
  await page.goto(`${BASE}/admin/login`, { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('#username');
  await page.type('#username', process.env.ADMIN_USERNAME, { delay: 3 });
  await page.type('#password', process.env.ADMIN_PASSWORD, { delay: 3 });
  await page.click('button[type="submit"]');
  const deadline = Date.now() + 20000;
  while (Date.now() < deadline) {
    await sleep(500);
    const ok = await page.evaluate(async () => {
      const r = await fetch('/api/me', { credentials: 'same-origin' });
      return r.status;
    });
    if (ok === 200) return;
  }
  throw new Error('sign-in failed');
}

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
  await signIn(page);

  const targets = [
    { name: 'guide-depth', route: '/admin/style-guide', theme: 'dark' },
    { name: 'guide-depth-light', route: '/admin/style-guide', theme: 'light' },
    { name: 'rules', route: '/admin/rules', theme: 'dark' },
    { name: 'rules-light', route: '/admin/rules', theme: 'light' },
  ];

  for (const spec of targets) {
    await page.evaluate((t) => localStorage.setItem('ns-theme', t), spec.theme);
    await page.goto(BASE + spec.route, { waitUntil: 'domcontentloaded' });
    await sleep(2400);
    await page.screenshot({ path: path.join(OUT, `${spec.name}.png`) });
    console.log(`${spec.name}.png`);
  }

  // The depth section, cropped so it can actually be judged.
  await page.evaluate(() => localStorage.setItem('ns-theme', 'dark'));
  await page.goto(`${BASE}/admin/style-guide`, { waitUntil: 'domcontentloaded' });
  await sleep(2400);
  const box = await page.evaluate(() => {
    const headings = [...document.querySelectorAll('h2, h3')];
    const target = headings.find((h) => /^Depth$/i.test((h.textContent || '').trim()));
    if (!target) return null;
    const section = target.closest('section') || target.parentElement;
    const r = section.getBoundingClientRect();
    return { x: r.x + window.scrollX, y: r.y + window.scrollY,
             width: r.width, height: r.height };
  });
  if (box) {
    await page.screenshot({
      path: path.join(OUT, 'depth-section.png'),
      clip: { x: box.x, y: box.y, width: box.width,
              height: Math.min(box.height, 900) },
    });
    console.log('depth-section.png (cropped)');
  } else {
    console.log('!! could not find a "Depth" section');
  }

  // --- mobile ---------------------------------------------------------------
  const mobile = await ctx.newPage();
  await mobile.setViewport({ width: 390, height: 844, deviceScaleFactor: 2,
                             isMobile: true, hasTouch: true });
  await signIn(mobile);
  await mobile.goto(`${BASE}/admin/rules`, { waitUntil: 'domcontentloaded' });
  await sleep(2400);
  await mobile.screenshot({ path: path.join(OUT, 'rules-mobile.png'),
                            fullPage: true });
  console.log('rules-mobile.png');

  // Horizontal overflow is the thing a mobile shot hides, because the page
  // just gets clipped. Measured instead of eyeballed.
  const overflow = await mobile.evaluate(() => ({
    doc: document.documentElement.scrollWidth,
    win: window.innerWidth,
    offenders: [...document.querySelectorAll('body *')]
      .filter((el) => el.getBoundingClientRect().right > window.innerWidth + 1)
      .slice(0, 6)
      .map((el) => `${el.tagName.toLowerCase()}.${(el.className || '').toString().slice(0, 60)}`),
  }));
  console.log(`mobile overflow: scrollWidth=${overflow.doc} innerWidth=${overflow.win}`);
  if (overflow.doc > overflow.win + 1) {
    console.log('  OVERFLOW, offenders:');
    overflow.offenders.forEach((o) => console.log(`    ${o}`));
  } else {
    console.log('  no horizontal overflow');
  }

  await browser.close();
})().catch((e) => {
  console.error('FATAL', e.message);
  process.exit(1);
});