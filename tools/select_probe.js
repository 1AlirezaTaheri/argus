/**
 * Do the five converted dropdowns still work?
 *
 * They went from native <select> to the design-system Radix Select, which is not
 * a cosmetic swap: Radix fires onValueChange with a *string*, has no
 * `event.target.value`, treats the empty string as a legitimate item rather
 * than "no value", and renders its popup in a portal.
 *
 * The one that could actually break is the audit page's Action filter, whose
 * "all actions" option has the value "". If Radix rejects an empty-string item
 * the filter stops working; if the state type is not a string the change handler
 * stops compiling. Both would be caught here, and neither would be caught by a
 * screenshot.
 */
const puppeteer = require('puppeteer');

const BASE = process.env.TARGET_BASE || 'https://127.0.0.1:8443';
const results = [];

function check(name, ok, detail) {
  results.push({ name, ok });
  console.log(`${ok ? 'ok  ' : 'FAIL'} ${name.padEnd(48)} ${detail}`);
}

(async () => {
  const browser = await puppeteer.launch({
    headless: 'new',
    args: ['--no-sandbox', '--disable-setuid-sandbox', '--disable-dev-shm-usage',
           '--ignore-certificate-errors'],
  });
  const ctx = await browser.createBrowserContext();
  const page = await ctx.newPage();
  await page.setViewport({ width: 1440, height: 1000 });
  await page.emulateMediaFeatures([{ name: 'prefers-color-scheme', value: 'dark' }]);

  const errors = [];
  page.on('pageerror', (e) => errors.push(`pageerror: ${e.message}`));
  page.on('console', (m) => {
    if (m.type() === 'error' && !/DevTools|Failed to load resource/.test(m.text())) {
      errors.push(`console: ${m.text()}`);
    }
  });

  await page.goto(`${BASE}/admin/login`, { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('#username');
  await page.type('#username', process.env.ADMIN_USERNAME, { delay: 5 });
  await page.type('#password', process.env.ADMIN_PASSWORD, { delay: 5 });
  await Promise.all([
    page.click('button[type="submit"]'),
    page.waitForNavigation({ waitUntil: 'domcontentloaded' }).catch(() => {}),
  ]);

  // A helper: open a Radix select by its trigger's id/label, pick an option by
  // text, and report the value that ends up selected.
  async function pickOption(triggerLabel, optionText) {
    const trigger = await page.evaluateHandle((l) => {
      const t = document.querySelector(`[aria-label="${l}"]`) ||
        [...document.querySelectorAll('button,[role="combobox"]')]
          .find((b) => (b.textContent || '').trim().toLowerCase().includes(l.toLowerCase()));
      return t;
    }, triggerLabel);
    const el = trigger.asElement();
    if (!el) return { error: `no trigger for ${triggerLabel}` };
    await el.click();
    await new Promise((r) => setTimeout(r, 500));
    const opened = await page.$$eval('[role="option"]', (els) => els.map((e) => (e.textContent || '').trim()));
    const opt = await page.evaluateHandle((t) => {
      const o = [...document.querySelectorAll('[role="option"]')]
        .find((x) => (x.textContent || '').trim() === t);
      return o;
    }, optionText);
    const optEl = opt.asElement();
    if (!optEl) {
      await page.keyboard.press('Escape');
      return { error: `no option "${optionText}" (had: ${JSON.stringify(opened)})` };
    }
    await optEl.click();
    await new Promise((r) => setTimeout(r, 500));
    const value = await el.evaluate((n) => (n.textContent || '').trim());
    return { opened, value };
  }

  // --- audit page: the two filters, one of which uses "" ------------------
  console.log('\n--- audit filters ---');
  await page.goto(`${BASE}/admin/audit`, { waitUntil: 'domcontentloaded' });
  await new Promise((r) => setTimeout(r, 2500));

  // The audit log is a timeline list, not a <table>, so `tbody tr` finds nothing.
  // Count the entries the page actually renders. The number has to be captured,
  // not just the words: matching the phrase alone returned the same string
  // before and after filtering, which read as "the filter did nothing".
  const entries = () =>
    page.evaluate(() => {
      const m = document.body.innerText.match(/(\d[\d,]*)\s+entries\s+matched/);
      return m ? m[1] : null;
    });
  const rowsBefore = await entries();
  const a = await pickOption('Filter by action', 'auth.login');
  check('audit action filter opens and selects',
        !a.error && !!a.opened?.includes('auth.login'),
        a.error || `opened ${a.opened?.length} options, now "${a.value}"`);

  const rowsAfter = await entries();
  check('audit filter changes the result count', rowsBefore !== rowsAfter,
        `${rowsBefore} entries -> ${rowsAfter} entries`);

  // The regression this guards: an item whose value is the empty string cannot
  // be chosen in Radix, and the trigger renders blank. So the label must come
  // back, not just the value.
  const back = await pickOption('Filter by action', 'all actions');
  check('"all actions" shows its label again, not a blank trigger',
        !back.error && back.value === 'all actions',
        back.error || `trigger reads ${JSON.stringify(back.value)}`);

  const r = await pickOption('Filter by result', 'success only');
  check('audit result filter opens and selects', !r.error, r.error || `now "${r.value}"`);

  const rBack = await pickOption('Filter by result', 'all results');
  check('"all results" shows its label again',
        !rBack.error && rBack.value === 'all results',
        rBack.error || `trigger reads ${JSON.stringify(rBack.value)}`);

  // --- the table's page-size picker ---------------------------------------
  console.log('\n--- table page size ---');
  await page.goto(`${BASE}/admin/style-guide`, { waitUntil: 'domcontentloaded' });
  await new Promise((r) => setTimeout(r, 2500));
  // "Showing 1-2 of 2". The separator is an en dash, so an ASCII-hyphen pattern
  // finds nothing; and the pattern has to be written inside the page function
  // because a RegExp cannot be serialised across the evaluate boundary.
  const showing = () =>
    page.evaluate(() => {
      const m = document.body.innerText.match(/Showing\s+[\d-–—]+\s+of\s+[\d-–—]+/);
      return m ? m[0] : '';
    });
  const before = await showing();
  const ps = await pickOption('Rows per page', '10');
  check('page-size picker opens and selects', !ps.error, ps.error || `now "${ps.value}"`);
  const after = await showing();
  check('page size still reports its range', !!after, `${before} -> ${after}`);

  // --- the new style-guide Select demo ------------------------------------
  console.log('\n--- style guide Select demo ---');
  // The demo trigger is located by id, not label.
  const sgTrigger = await page.$('#sg-select');
  check('the Select demo trigger exists', !!sgTrigger, sgTrigger ? 'found' : 'missing');
  if (sgTrigger) {
    await sgTrigger.click();
    await new Promise((r) => setTimeout(r, 500));
    const opts = await page.$$eval('[role="option"]', (els) =>
      els.map((e) => (e.textContent || '').trim()));
    check('the Select demo opens with its options', opts.length >= 3,
          `options: ${JSON.stringify(opts)}`);
    const disabledOpt = await page.evaluate(() => {
      const o = [...document.querySelectorAll('[role="option"]')]
        .find((x) => (x.textContent || '').includes('disabled'));
      if (!o) return null;
      return {
        ariaDisabled: o.getAttribute('aria-disabled'),
        dataDisabled: o.getAttribute('data-disabled'),
        opacity: getComputedStyle(o).opacity,
      };
    });
    check('a disabled option is marked and dimmed',
          !!disabledOpt && disabledOpt.ariaDisabled === 'true' && Number(disabledOpt.opacity) < 0.9,
          JSON.stringify(disabledOpt));
    await page.keyboard.press('Escape');
    await new Promise((r) => setTimeout(r, 300));
  }

  const disabled = await page.evaluate(() => {
    const t = document.querySelector('[aria-label="A disabled select"]');
    if (!t) return null;
    return { disabled: t.hasAttribute('disabled') || t.getAttribute('data-disabled') !== null,
             opacity: getComputedStyle(t).opacity };
  });
  check('the disabled Select demo is marked disabled', !!disabled?.disabled,
        JSON.stringify(disabled));

  // --- settings controls --------------------------------------------------
  console.log('\n--- settings controls ---');
  await page.goto(`${BASE}/admin/settings`, { waitUntil: 'domcontentloaded' });
  await new Promise((r) => setTimeout(r, 2500));
  const settingsSelects = await page.$$eval('[role="combobox"]', (els) => els.length);
  const nativeLeft = await page.$$eval('select', (els) => els.length);
  check('settings uses the design-system Select', settingsSelects > 0 && nativeLeft === 0,
        `${settingsSelects} combobox, ${nativeLeft} native select`);

  console.log('\n--- errors ---');
  if (errors.length) errors.forEach((e) => console.log(`  ${e}`));
  else console.log('  none');
  if (errors.length) results.push({ name: 'console errors', ok: false });

  await browser.close();
  const failed = results.filter((r) => !r.ok);
  console.log(`\n${results.length - failed.length}/${results.length} checks OK`);
  if (failed.length) {
    failed.forEach((f) => console.log(`  - ${f.name}`));
    process.exit(1);
  }
})().catch((e) => {
  console.error('FATAL', e.message);
  process.exit(1);
});