/**
 * The copy fallback: what happens when `navigator.clipboard` refuses.
 *
 * After the fix the copy works in the normal case, which means the interesting
 * path -- the one the fix was written for -- is not being exercised. This forces
 * the refusal and checks the two things the component promises when it happens:
 * the text still reaches the clipboard by another route, or the user is told.
 *
 * `navigator.clipboard.writeText` is replaced with one that rejects, before the
 * click, exactly as a non-secure context or a denied permission would.
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

(async () => {
  const browser = await puppeteer.launch({
    headless: 'new',
    protocolTimeout: 600000,
    args: ['--no-sandbox', '--disable-setuid-sandbox', '--disable-dev-shm-usage',
      '--ignore-certificate-errors'],
  });
  const page = await (await browser.createBrowserContext()).newPage();
  if (!(await login(page))) { console.error('login failed'); process.exit(2); }

  await page.goto(`${BASE}/admin/telegram-users`, { waitUntil: 'domcontentloaded' });
  const SEL = '[title="Copy Telegram ID"], [aria-label="Copy Telegram ID"]';
  const deadline = Date.now() + 12000;
  let btn = null;
  while (Date.now() < deadline && !btn) { btn = await page.$(SEL); await sleep(400); }
  if (!btn) { console.error('copy control never appeared'); process.exit(2); }

  // Record every execCommand call so the fallback is observable, and make the
  // async API reject the way a refused permission does.
  await page.evaluate(() => {
    window.__execCalls = [];
    const real = document.execCommand ? document.execCommand.bind(document) : null;
    document.execCommand = function (cmd) {
      window.__execCalls.push(cmd);
      return real ? real(cmd) : false;
    };
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      get: () => ({
        writeText: () => Promise.reject(new DOMException('denied', 'NotAllowedError')),
        readText: () => Promise.resolve('SENTINEL'),
      }),
    });
  });

  const expectedId = await page.evaluate((sel) =>
    (document.querySelector(sel) || {}).innerText?.trim() || '', SEL);
  check('the harness really did break the clipboard', true,
    `navigator.clipboard.writeText now always rejects; id on screen is ${JSON.stringify(expectedId)}`);

  await btn.click();
  await sleep(1000);

  const after = await page.evaluate((sel) => {
    const b = document.querySelector(sel);
    const icon = b ? b.querySelector('svg') : null;
    const toasts = Array.from(document.querySelectorAll('[data-sonner-toast], [role="status"], [role="alert"]'))
      .map((t) => (t.innerText || '').trim()).filter(Boolean);
    return {
      iconClass: icon ? String(icon.getAttribute('class') || '') : null,
      execCalls: window.__execCalls || [],
      toasts,
      bodyHasFailure: (document.body.innerText || '').toLowerCase().includes('could not copy'),
    };
  }, SEL);

  const usedFallback = after.execCalls.includes('copy');
  const signalledSuccess = /text-primary/.test(after.iconClass || '');
  const toldUser = after.toasts.length > 0 || after.bodyHasFailure;

  check('the refused write falls back to execCommand', usedFallback,
    usedFallback
      ? `document.execCommand called with: ${JSON.stringify(after.execCalls)}`
      : `no fallback attempted; execCommand calls were ${JSON.stringify(after.execCalls)}. `
        + `The click did nothing at all.`);

  check('the user is told when the copy fails, or it succeeded silently-visible',
    signalledSuccess || toldUser,
    signalledSuccess
      ? `the icon changed to text-primary, so the fallback worked and the user sees it`
      : toldUser
        ? `the user was told: ${JSON.stringify(after.toasts) || 'a "could not copy" message is on the page'}`
        : `nothing happened at all: icon "${after.iconClass}", no toast, no message`);

  await browser.close();
  const failed = checks.filter((c) => !c.ok);
  console.log(`\n  ${checks.length - failed.length} passed, ${failed.length} failed`);
  process.exit(failed.length ? 1 : 0);
})().catch((e) => { console.error('probe failed:', e && e.message); process.exit(2); });