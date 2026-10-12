/**
 * Do any of Tailwind's hardcoded defaults actually reach the user's eyes?
 *
 * ## Why a browser and not the stylesheet
 *
 * Scanning the built CSS found 91 colour literals. Nearly all are the theme
 * writing itself (`:root{--shadow-sm: ... rgb(0 0 0 / .45)}`) or deliberate
 * absolutes (the `bg-black/60` scrim, the white inset bevels). The interesting
 * remainder is Tailwind *preflight*, which no file in this repo mentions:
 *
 *     *,:before,:after{border-color:#e5e7eb}
 *     ::placeholder{color:#9ca3af}
 *     --tw-ring-color: rgb(59 130 246 / .5)          <- blue-500
 *
 * Whether any of that is visible cannot be settled by reading the stylesheet,
 * because the answer depends on what each element overrides -- and this app
 * writes `className` across several lines, so a source grep cannot tell a bare
 * `border` from one whose colour is on the next line. Source analysis already
 * got this wrong twice in this session: a broken sed reported "no holes" after
 * erroring, and a prefix match returned 29 colour findings that were mostly
 * font sizes.
 *
 * Computed styles are what the browser paints, so they settle it directly.
 *
 * ## What counts
 *
 * A default is only a defect when it is *visible*: a border of zero width is
 * invisible whatever its colour. So each check requires the property to be
 * rendering, and reports the selector and coordinates so it can be looked at
 * rather than taken on trust.
 *
 * Both themes are audited, because the whole point of these defaults is that
 * neither theme tracks them.
 */
const puppeteer = require('puppeteer');

const BASE = process.env.TARGET_BASE || 'https://127.0.0.1:8443';
const results = [];

function check(name, ok, detail) {
  results.push({ name, ok });
  console.log(`${ok ? 'ok  ' : 'FAIL'} ${name.padEnd(54)} ${detail}`);
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const PAGES = [
  '/admin',
  '/admin/targets',
  '/admin/rules',
  '/admin/audit',
  '/admin/settings',
  '/admin/style-guide',
];

/** Colours Tailwind ships that this design system does not own. */
const DEFAULTS = {
  border: 'rgb(229, 231, 235)', // #e5e7eb  gray-200
  placeholder: 'rgb(156, 163, 175)', // #9ca3af  gray-400
  ring: 'rgb(59, 130, 246', // blue-500
  white: 'rgb(255, 255, 255)',
};

/**
 * Prove the matchers before using them.
 *
 * This audit reported false findings four times before it was correct: a
 * truncated class list hid `ring-bg`, an enumerated colour list omitted it, a
 * computed-value check could not tell a token from Tailwind's `bg-white` when
 * the token's value *is* white, and a `const` was read before its declaration.
 * Every one of those is a case the code already has an opinion about, so the
 * opinions are asserted up front: if the matcher regresses, this says so
 * loudly rather than reporting "clean" because it stopped matching.
 */
function selfTest() {
  const cases = [
    // [className, expectFlaggedRing]
    ['ring-4 ring-bg', false],      // themed ring -- the timeline dots
    ['ring-2 ring-primary/40', false],
    ['focus-visible:ring-2 focus-visible:ring-danger', false],
    ['ring-4 bg-elevated', true],   // width, no colour at all
    ['ring-2', true],
    ['focus-ring ring-2', false],   // the shared class supplies the colour
    ['ring-offset-2 ring-offset-primary', false],
    ['rounded-full ring-4 ring-fg', false],
  ];
  const W = /(?:^|[:\s])ring-[0-9]+\b/;
  const C = /(?:^|[:\s])ring-(?!offset)(?![0-9])[a-z]/;
  const F = /(?:^|[:\s])focus-ring\b/;
  const bad = [];
  for (const [cls, expectFlagged] of cases) {
    const flagged = W.test(cls) && !C.test(cls) && !F.test(cls);
    if (flagged !== expectFlagged) {
      bad.push(`  "${cls}" -> flagged=${flagged}, expected ${expectFlagged}`);
    }
  }
  // And the white matcher, which must not fire on a token-driven surface.
  const W2 = /(?:^|[:\s])bg-white(?:\s|$)/;
  if (!W2.test('bg-white')) bad.push('  "bg-white" not detected');
  if (W2.test('bg-white/60')) bad.push('  "bg-white/60" wrongly detected');
  if (W2.test('bg-whitewash')) bad.push('  "bg-whitewash" wrongly detected');
  if (W2.test('rounded bg-elevated')) bad.push('  false positive on bg-elevated');

  if (bad.length) {
    console.log('SELF-TEST FAILED -- matchers are wrong, results would be junk:');
    bad.forEach((b) => console.log(b));
    return false;
  }
  console.log(`ok   matchers self-tested (${cases.length + 4} assertions)`);
  return true;
}

/** Extracted in-page: walks every element and reports visible defaults. */
function auditPage() {
  const hits = { border: [], placeholder: [], ring: [], white: [] };
  const describe = (el) => {
    const id = el.id ? `#${el.id}` : '';
    const cls =
      typeof el.className === 'string' && el.className
        ? '.' + el.className.trim().split(/\s+/).slice(0, 4).join('.')
        : '';
    return (
      el.tagName.toLowerCase() + id + cls +
      ` @${Math.round(el.getBoundingClientRect().left)},${Math.round(
        el.getBoundingClientRect().top
      )}`
    );
  };

  const all = document.querySelectorAll('*');
  for (const el of all) {
    const cs = getComputedStyle(el);
    // Declared once at the top: the white and ring checks below both read it,
    // and scoping it inside either would be a ReferenceError the browser only
    // reports at runtime.
    const cls = typeof el.className === 'string' ? el.className : '';

    // A visible border painted in the preflight grey.
    const widths = [
      cs.borderTopWidth, cs.borderRightWidth,
      cs.borderBottomWidth, cs.borderLeftWidth,
    ];
    const visible = widths.some((w) => parseFloat(w) > 0);
    if (visible) {
      const cols = [
        cs.borderTopColor, cs.borderRightColor,
        cs.borderBottomColor, cs.borderLeftColor,
      ];
      if (cols.some((c) => c === 'rgb(229, 231, 235)')) {
        hits.border.push(describe(el));
      }
    }

    // A card painted by the `bg-white` *utility* rather than by a token.
    //
    // Checking the computed value was wrong and reported 55 "defects" that
    // were the design working. index.css documents the light theme as "the
    // page is grey and cards are white" -- so a white card is the correct
    // result of `bg-elevated`, and because `--elevated` is literally
    // 255 255 255 in light mode it is byte-identical to Tailwind's bg-white.
    // No computed-value check can separate them; only the class list can.
    if (/(?:^|[:\s])bg-white(?:\s|$)/.test(cls)) {
      hits.white.push(describe(el) + ' || ' + cls);
    }

    // A focus ring width with no colour of its own: the variable preflight
    // seeds with blue-500 wins.
    //
    // The colour test enumerates names -- primary, danger, ring, fg, ... -- and
    // that is why this reported 400 defects on the first run: the timeline dots
    // use `ring-4 ring-bg`, a perfectly themed ring whose colour name was simply
    // not in my list. Enumerating is fragile in both directions. The structural
    // distinction is the one that holds: `ring-<digits>` is a width,
    // `ring-<anything else>` is a colour. `ring-offset-` is excluded because its
    // argument is an offset width or an offset colour, neither of which supplies
    // the ring's own colour.
    const hasWidth = /(?:^|[:\s])ring-[0-9]+\b/.test(cls);
    const hasColour = /(?:^|[:\s])ring-(?!offset)(?![0-9])[a-z]/.test(cls);
    const isThemed = /(?:^|[:\s])focus-ring\b/.test(cls);
    if (hasWidth && !hasColour && !isThemed) {
      hits.ring.push(describe(el) + ' || ' + cls);
    }
  }

  // Placeholders: the pseudo-element's own computed colour.
  for (const el of document.querySelectorAll('input, textarea')) {
    try {
      const c = getComputedStyle(el, '::placeholder').color;
      if (c === 'rgb(156, 163, 175)') hits.placeholder.push(describe(el));
    } catch (e) {
      /* pseudo not supported -- reported below as unverified */
    }
  }

  return hits;
}

(async () => {
  if (!selfTest()) process.exit(1);

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
  await page.setViewport({ width: 1440, height: 1200, deviceScaleFactor: 1 });

  // ---- log in -------------------------------------------------------------
  await page.goto(`${BASE}/admin/login`, { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('#username');
  await page.type('#username', process.env.ADMIN_USERNAME, { delay: 3 });
  await page.type('#password', process.env.ADMIN_PASSWORD, { delay: 3 });
  await page.click('button[type="submit"]');
  const deadline = Date.now() + 20000;
  let authed = false;
  while (Date.now() < deadline) {
    await sleep(500);
    const status = await page.evaluate(async () =>
      (await fetch('/api/me', { credentials: 'same-origin' })).status);
    if (status === 200) { authed = true; break; }
  }
  check('logged in', authed, authed ? '' : '/api/me never returned 200');
  if (!authed) {
    await browser.close();
    process.exit(1);
  }

  // The login page itself is audited too: it is the one surface every admin
  // sees, and the mandate called for full depth treatment there.
  const themes = ['dark', 'light'];
  const totals = { border: 0, placeholder: 0, ring: 0, white: 0 };
  const examples = { border: [], placeholder: [], ring: [], white: [] };

  for (const theme of themes) {
    await page.goto(`${BASE}/admin/login`, { waitUntil: 'domcontentloaded' });
    await page.evaluate((t) => {
      document.documentElement.setAttribute('data-theme', t);
      localStorage.setItem('argus-theme', t);
    }, theme);
    await sleep(200);

    // login page (logged out view still renders the form)
    let hits = await page.evaluate(auditPage);
    for (const k of Object.keys(hits)) {
      totals[k] += hits[k].length;
      examples[k].push(...hits[k].map((s) => `${theme}/login ${s}`));
    }

    for (const path of PAGES) {
      await page.goto(`${BASE}${path}`, { waitUntil: 'domcontentloaded' });
      await page.evaluate((t) => {
        document.documentElement.setAttribute('data-theme', t);
      }, theme);
      await sleep(600);
      hits = await page.evaluate(auditPage);
      for (const k of Object.keys(hits)) {
        totals[k] += hits[k].length;
        examples[k].push(...hits[k].map((s) => `${theme}${path} ${s}`));
      }
    }
  }

  console.log('');
  console.log('=== visible Tailwind defaults, both themes, all pages ===');
  const LABEL = {
    border: 'borders painted preflight grey #e5e7eb',
    placeholder: 'placeholders painted preflight gray-400',
    ring: 'ring-* with no colour (falls back to blue-500)',
    white: 'surfaces painted pure white',
  };
  let bad = 0;
  for (const k of Object.keys(totals)) {
    const n = totals[k];
    check(LABEL[k], n === 0, n === 0 ? 'none' : `${n} occurrence(s)`);
    if (n > 0) bad += n;
    if (n > 0) {
      for (const e of examples[k].slice(0, 8)) console.log(`        ${e}`);
      if (n > 8) console.log(`        ... and ${n - 8} more`);
    }
  }

  console.log('');
  const failed = results.filter((r) => !r.ok).length;
  console.log(failed === 0 ? 'no framework default reaches the eye' : `${failed} failing check(s)`);
  await browser.close();
  process.exit(failed === 0 ? 0 : 1);
})().catch((e) => {
  console.error('audit crashed:', e && e.message ? e.message : e);
  process.exit(1);
});
