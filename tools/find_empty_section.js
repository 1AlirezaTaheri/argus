/**
 * Dump the empty `<section>` the guide audit keeps reporting.
 *
 * The audit says section #13 has no heading, no text and no children, but
 * `style-guide.tsx` renders every one of its sections with an `<h2>`, and no
 * other component in the frontend emits a `<section>` at all. So it is coming
 * from a dependency and guessing which one is slower than looking.
 */
const puppeteer = require('puppeteer');

const BASE = process.env.TARGET_BASE || 'https://127.0.0.1:8443';
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

(async () => {
  const browser = await puppeteer.launch({
    headless: 'new',
    protocolTimeout: 120000,
    args: ['--no-sandbox', '--disable-setuid-sandbox', '--disable-dev-shm-usage',
           '--ignore-certificate-errors'],
  });
  const page = await (await browser.createBrowserContext()).newPage();
  await page.setViewport({ width: 1440, height: 1200 });

  await page.goto(`${BASE}/admin/login`, { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('#username');
  await page.type('#username', process.env.ADMIN_USERNAME, { delay: 3 });
  await page.type('#password', process.env.ADMIN_PASSWORD, { delay: 3 });
  await page.click('button[type="submit"]');
  const deadline = Date.now() + 20000;
  while (Date.now() < deadline) {
    await sleep(500);
    if (await page.evaluate(async () =>
      (await fetch('/api/me', { credentials: 'same-origin' })).status) === 200) break;
  }

  await page.goto(`${BASE}/admin/style-guide`, { waitUntil: 'domcontentloaded' });
  await sleep(3000);

  const found = await page.evaluate(() => {
    const out = [];
    document.querySelectorAll('section').forEach((section, index) => {
      if (section.querySelector('h2, h3')) return;
      if (section.children.length > 0) return;
      const parent = section.parentElement;
      out.push({
        index,
        html: section.outerHTML.slice(0, 200),
        parentTag: parent ? parent.tagName : null,
        parentClass: parent ? (parent.className || '').toString().slice(0, 90) : null,
        parentHtml: parent ? parent.outerHTML.slice(0, 260) : null,
        rect: section.getBoundingClientRect().height,
      });
    });
    return out;
  });

  if (!found.length) {
    console.log('no empty section found');
  }
  for (const item of found) {
    console.log(`--- section #${item.index} (height ${item.rect}) ---`);
    console.log('  self   :', item.html);
    console.log('  parent :', item.parentTag, item.parentClass);
    console.log('  parent html:', item.parentHtml);
  }

  await browser.close();
})().catch((e) => {
  console.error('FATAL', e.message);
  process.exit(1);
});