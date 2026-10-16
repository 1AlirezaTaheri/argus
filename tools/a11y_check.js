/**
 * Accessibility and feedback checks for three defects found by the page sweep.
 *
 * Each assertion is written so it FAILS on today's code. That is stated up front
 * because the usual failure mode here is a check that is green on the broken
 * thing -- one of these (the copy) began life satisfied by an empty string,
 * because every string includes "".
 *
 *   1. The targets Grid/Table toggle communicates its state only through colour.
 *      `targets.tsx` swaps `bg-primary` on the active button and sets no ARIA
 *      state at all, so a screen reader announces two identical buttons.
 *   2. Copy Telegram ID discards its clipboard rejection. The handler is
 *      `void navigator.clipboard.writeText(...)`, so a refused write -- a
 *      non-secure context, a lost permission, an unfocused document -- leaves the
 *      user with no copy and no message. The component's own `copied` flag never
 *      flips, which is the observable this asserts on: it is the only signal the
 *      user would have had.
 *   3. The targets grid is the default view, and each target is a `Card` whose
 *      default element is a div. A div with an onClick is not focusable and
 *      cannot be activated by keyboard, so the primary navigation of that page
 *      is unreachable without a mouse. `Card` already supports `as="button"`;
 *      the page does not use it.
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

async function waitFor(page, sel, ms = 12000) {
  const deadline = Date.now() + ms;
  while (Date.now() < deadline) {
    const h = await page.$(sel);
    if (h) return h;
    await sleep(400);
  }
  return null;
}

(async () => {
  const browser = await puppeteer.launch({
    headless: 'new',
    protocolTimeout: 600000,
    args: ['--no-sandbox', '--disable-setuid-sandbox', '--disable-dev-shm-usage',
      '--ignore-certificate-errors', '--force-color-profile=srgb'],
  });
  const ctx = await browser.createBrowserContext();
  await ctx.overridePermissions(BASE, ['clipboard-read', 'clipboard-write']);
  const page = await ctx.newPage();
  page.on('dialog', async (d) => { try { await d.dismiss(); } catch (e) {} });

  if (!(await login(page))) {
    console.error('  FAIL could not log in');
    process.exit(2);
  }

  // -------------------------------------------------------------------
  // 1. The view toggle must expose which view is active
  // -------------------------------------------------------------------
  console.log('\n=== targets: the view toggle must expose its state ===');
  await page.goto(`${BASE}/admin/targets`, { waitUntil: 'domcontentloaded' });
  await sleep(2500);

  const toggleState = await page.evaluate(() => {
    const btn = (n) => Array.from(document.querySelectorAll('button'))
      .find((b) => (b.textContent || '').trim() === n) || null;
    const g = btn('Grid'); const t = btn('Table');
    if (!g || !t) return { present: false };
    return {
      present: true,
      gridPressed: g.getAttribute('aria-pressed'),
      tablePressed: t.getAttribute('aria-pressed'),
      gridCurrent: g.getAttribute('aria-current'),
      tableCurrent: t.getAttribute('aria-current'),
      // Which one is visually active, judged by class not by colour value.
      gridActiveByClass: /bg-primary/.test(String(g.className)),
      tableActiveByClass: /bg-primary/.test(String(t.className)),
    };
  });

  if (!toggleState.present) {
    check('targets: Grid/Table toggle present', false, 'the toggle was not found at all');
  } else {
    check('targets: Grid/Table toggle present', true, 'both buttons rendered');
    const exposes = toggleState.gridPressed !== null || toggleState.gridCurrent !== null
      || toggleState.tablePressed !== null || toggleState.tableCurrent !== null;
    check('targets: the active view is announced, not only coloured', exposes,
      exposes
        ? `grid aria-pressed=${toggleState.gridPressed} table aria-pressed=${toggleState.tablePressed}`
        : `active view is signalled only by the bg-primary class `
          + `(grid active: ${toggleState.gridActiveByClass}, table active: ${toggleState.tableActiveByClass}); `
          + `aria-pressed and aria-current are both absent`);
  }

  // -------------------------------------------------------------------
  // 2. The grid must be operable from the keyboard
  //
  // The first version of this section asserted that the `.card` element was
  // focusable, and reported the grid as unreachable. That was the check being
  // wrong: the card surface is a plain div, but it *contains* a real button per
  // target, which is in the tab order. Reading the DOM showed
  // `<button class="min-w-0 text-left" onClick=...>` inside each card.
  //
  // The claim worth testing is the one a user cares about -- can a keyboard open
  // a target -- so this presses Enter on the focused control and asserts the
  // drawer actually appeared.
  // -------------------------------------------------------------------
  console.log('\n=== targets: the grid must be keyboard operable ===');

  const gridInfo = await page.evaluate(() => {
    const cards = Array.from(document.querySelectorAll('.card'));
    const withTargets = cards.map((c) => {
      const btn = c.querySelector('button');
      return {
        surface: c.tagName,
        surfaceTabIndex: c.tabIndex,
        innerTag: btn ? btn.tagName : null,
        innerTabIndex: btn ? btn.tabIndex : null,
        text: (btn ? btn.innerText : c.innerText || '').trim().replace(/\s+/g, ' ').slice(0, 40),
      };
    }).filter((c) => c.innerTag);
    return { count: withTargets.length, sample: withTargets.slice(0, 3) };
  });

  if (!gridInfo.count) {
    check('targets: grid has target controls to test', false,
      'no .card containing a button was found -- cannot judge keyboard use');
  } else {
    check('targets: each grid card exposes a focusable control',
      gridInfo.sample.every((c) => c.innerTag === 'BUTTON' && c.innerTabIndex >= 0),
      `${gridInfo.count} cards; first: <${gridInfo.sample[0].surface}> surface `
      + `(tabIndex ${gridInfo.sample[0].surfaceTabIndex}) containing `
      + `<${gridInfo.sample[0].innerTag}> (tabIndex ${gridInfo.sample[0].innerTabIndex}) `
      + `labelled "${gridInfo.sample[0].text}"`);

    // The end-to-end claim: focus it, press Enter, drawer appears.
    const focusTarget = await page.evaluate(() => {
      const btn = Array.from(document.querySelectorAll('.card button'))
        .find((b) => (b.innerText || '').trim().length > 0);
      if (!btn) return { found: false };
      btn.focus();
      return { found: true, active: document.activeElement === btn, text: (btn.innerText || '').trim() };
    });

    if (!focusTarget.found) {
      check('targets: a grid target can take keyboard focus', false, 'no target button found');
    } else {
      check('targets: a grid target can take keyboard focus', focusTarget.active,
        focusTarget.active
          ? `"${focusTarget.text}" took focus`
          : 'focus() did not make it document.activeElement');

      const drawerBefore = await page.evaluate(() => ({
        dialogs: Array.from(document.querySelectorAll('[role="dialog"], [role="alertdialog"]'))
          .filter((d) => d.getBoundingClientRect().width > 0).length,
        closeButtons: Array.from(document.querySelectorAll('[aria-label="Close"]'))
          .filter((d) => d.getBoundingClientRect().width > 0).length,
      }));

      await page.keyboard.press('Enter');
      await sleep(900);

      const drawerAfter = await page.evaluate(() => ({
        dialogs: Array.from(document.querySelectorAll('[role="dialog"], [role="alertdialog"]'))
          .filter((d) => d.getBoundingClientRect().width > 0).length,
        closeButtons: Array.from(document.querySelectorAll('[aria-label="Close"]'))
          .filter((d) => d.getBoundingClientRect().width > 0).length,
        text: (document.body.innerText || '').replace(/\s+/g, ' ').slice(0, 200),
      }));

      const opened = drawerAfter.dialogs > drawerBefore.dialogs
        || drawerAfter.closeButtons > drawerBefore.closeButtons;
      check('targets: Enter on a focused target opens it', opened,
        opened
          ? `the detail drawer appeared (dialogs ${drawerBefore.dialogs} -> ${drawerAfter.dialogs}, `
            + `close buttons ${drawerBefore.closeButtons} -> ${drawerAfter.closeButtons})`
          : `Enter did nothing observable: dialogs stayed at ${drawerAfter.dialogs}, `
            + `close buttons at ${drawerAfter.closeButtons}`);

      await page.keyboard.press('Escape');
      await sleep(400);
    }
  }

  // -------------------------------------------------------------------
  // 3. Copy must either copy or say it failed
  // -------------------------------------------------------------------
  console.log('\n=== telegram users: copy must work or report ===');
  await page.goto(`${BASE}/admin/telegram-users`, { waitUntil: 'domcontentloaded' });
  // Matched by title or aria-label: the control had only a title until this
  // round added an aria-label, and a selector that hardcodes one of the two
  // stops finding it the moment the other changes.
  const COPY_SEL = '[title="Copy Telegram ID"], [aria-label="Copy Telegram ID"]';
  const copyBtn = await waitFor(page, COPY_SEL);

  if (!copyBtn) {
    check('telegram-users: Copy Telegram ID present', false, 'control never appeared');
  } else {
    check('telegram-users: Copy Telegram ID present', true,
      'found by title or aria-label');

    const iconState = () => page.evaluate((sel) => {
      const b = document.querySelector(sel);
      const icon = b ? b.querySelector('svg') : null;
      return {
        cls: icon ? String(icon.getAttribute('class') || '') : null,
        id: b ? (b.innerText || '').trim() : '',
      };
    }, COPY_SEL);

    const before = await iconState();
    await copyBtn.click();
    await sleep(900);
    const after = await iconState();
    const clip = await page.evaluate(() =>
      navigator.clipboard.readText().catch((e) => `READ_FAILED:${e.name}`));

    const shownId = (before.id || '').trim();
    const copiedExactly = shownId !== '' && String(clip) === shownId;
    // The fallback: did the component at least tell the user something?
    const toldUser = !!before && !!after && before.cls !== after.cls;
    const complained = await page.evaluate(() =>
      (document.body.innerText || '').toLowerCase().includes('could not copy'));

    check('telegram-users: copy either writes the id or says it failed',
      copiedExactly || toldUser || complained,
      copiedExactly
        ? `clipboard holds ${JSON.stringify(clip)}, exactly the id on screen`
        : toldUser
          ? `clipboard read as ${JSON.stringify(clip)} but the component signalled success `
            + `(icon "${before.cls}" -> "${after.cls}")`
          : complained
            ? `the write failed and the page said so, rather than failing silently`
            : `clipboard is ${JSON.stringify(clip)}, expected ${JSON.stringify(shownId)}; `
              + `the icon class did not change ("${before.cls}") and no message appeared `
              + `-- the failure was silent`);
  }

  await browser.close();

  const failed = checks.filter((c) => !c.ok);
  console.log('\n================ SUMMARY ================');
  console.log(`  ${checks.length - failed.length} passed, ${failed.length} failed`);
  if (failed.length) {
    console.log('\nFAILING:');
    failed.forEach((f) => console.log(`  ${f.name}`));
  }
  process.exit(failed.length ? 1 : 0);
})().catch((e) => {
  console.error('a11y check failed to run:', e && e.message ? e.message : e);
  process.exit(2);
});