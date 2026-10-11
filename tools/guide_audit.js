/**
 * Audit every specimen on the style guide.
 *
 * The style guide is the reference the rest of the panel is written against, so
 * a specimen that renders nothing, or renders the wrong thing, is worse than no
 * specimen: it is a lie that looks like documentation. Several sections are
 * documented as available and were never checked.
 *
 * Each check asserts a *computed* value rather than a class name, because the
 * class being present says nothing about the value that came out -- a token can
 * resolve to `transparent`, or to the same value as its neighbour, and the page
 * will still look plausible in a screenshot.
 */
const puppeteer = require('puppeteer');

const BASE = process.env.TARGET_BASE || 'https://127.0.0.1:8443';
const results = [];

function check(name, ok, detail) {
  results.push({ name, ok });
  console.log(`${ok ? 'ok  ' : 'FAIL'} ${name.padEnd(52)} ${detail}`);
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

(async () => {
  const browser = await puppeteer.launch({
    headless: 'new',
    protocolTimeout: 180000,
    args: ['--no-sandbox', '--disable-setuid-sandbox', '--disable-dev-shm-usage',
           '--ignore-certificate-errors', '--force-color-profile=srgb'],
  });
  const ctx = await browser.createBrowserContext();
  const page = await ctx.newPage();
  await page.setViewport({ width: 1440, height: 1200, deviceScaleFactor: 1 });

  const consoleErrors = [];
  page.on('console', (m) => {
    if (m.type() === 'error' && !/favicon|Failed to load resource/.test(m.text())) {
      consoleErrors.push(m.text());
    }
  });
  page.on('pageerror', (e) => consoleErrors.push(`[pageerror] ${e.message}`));

  await page.goto(`${BASE}/admin/login`, { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('#username');
  await page.type('#username', process.env.ADMIN_USERNAME, { delay: 3 });
  await page.type('#password', process.env.ADMIN_PASSWORD, { delay: 3 });
  await page.click('button[type="submit"]');
  const deadline = Date.now() + 20000;
  while (Date.now() < deadline) {
    await sleep(500);
    const status = await page.evaluate(async () =>
      (await fetch('/api/me', { credentials: 'same-origin' })).status);
    if (status === 200) break;
  }

  await page.goto(`${BASE}/admin/style-guide`, { waitUntil: 'domcontentloaded' });
  await sleep(3500);

  // --- what sections exist at all ------------------------------------------
  const sections = await page.evaluate(() => {
    const headings = [...document.querySelectorAll('h2, h3')]
      .map((h) => (h.textContent || '').trim())
      .filter(Boolean);
    return headings;
  });
  console.log(`sections found: ${sections.join(' | ')}\n`);

  check('the page renders', sections.length > 0, `${sections.length} headings`);

  // --- every section must be identifiable -----------------------------------
  // A section with no heading is a section nobody can find or refer to.
  //
  // Scoped to the document's own sections. The first version counted every
  // `<section>` and found one with no heading, no text and no children -- it
  // turned out to be sonner's toast live region
  // (`<section aria-label="Notifications" aria-live="polite">`), which is a
  // widget, not a document section. A check that reports an accessibility
  // feature as a layout bug trains you to ignore it.
  const isWidget = (el) =>
    el.hasAttribute('aria-live') || el.hasAttribute('role');

  const sectionInfo = await page.evaluate((isWidgetSrc) => {
    const widget = eval(isWidgetSrc);
    const out = [];
    document.querySelectorAll('section').forEach((section, index) => {
      const heading = section.querySelector('h2, h3');
      out.push({
        index,
        widget: widget(section),
        heading: heading ? (heading.textContent || '').trim() : null,
        firstText: (section.innerText || '').trim().slice(0, 40),
        children: section.children.length,
      });
    });
    return out;
  }, isWidget.toString());

  const own = sectionInfo.filter((s) => !s.widget);
  const unnamed = own.filter((s) => !s.heading);
  check('every section has a heading', unnamed.length === 0,
        unnamed.length
          ? unnamed.map((s) => `#${s.index} starting "${s.firstText}" (${s.children} children)`).join(' | ')
          : `${own.length} sections, ${sectionInfo.length - own.length} widget region(s) ignored`);

  // --- every specimen must actually paint something -------------------------
  // A specimen that collapses to zero height, or renders transparent, is a
  // broken specimen. Looked for by the structural markers the guide uses.
  const specimens = await page.evaluate((isWidgetSrc) => {
    const widget = eval(isWidgetSrc);
    const out = [];
    const sections = [...document.querySelectorAll('section')];
    for (const section of sections) {
      if (widget(section)) continue;
      const heading = section.querySelector('h2, h3');
      const title = heading ? (heading.textContent || '').trim() : '(no heading)';
      // A specimen is a leaf-ish element with a background, border or shadow.
      const candidates = [...section.querySelectorAll('*')].filter((el) => {
        const cs = getComputedStyle(el);
        const hasPaint =
          (cs.backgroundColor && cs.backgroundColor !== 'rgba(0, 0, 0, 0)') ||
          cs.boxShadow !== 'none' ||
          (cs.borderTopWidth !== '0px' && cs.borderTopStyle !== 'none');
        if (!hasPaint) return false;
        const r = el.getBoundingClientRect();
        if (r.width < 24 || r.height < 12) return false;
        // Leaf only: skip anything that has painted descendants of its own.
        return ![...el.children].some((child) => {
          const ccs = getComputedStyle(child);
          return (ccs.backgroundColor && ccs.backgroundColor !== 'rgba(0, 0, 0, 0)') ||
            ccs.boxShadow !== 'none';
        });
      });
      out.push({
        title: title,
        count: candidates.length,
        collapsed: candidates.filter((el) => el.getBoundingClientRect().height < 4)
          .length,
      });
    }
    return out;
  }, isWidget.toString());

  for (const spec of specimens) {
    check(`section "${spec.title}" has specimens`, spec.count > 0,
          `${spec.count} painted`);
  }

  // --- the chart swatches must be five different colours -------------------
  // Scoped to the palette section specifically, and reading the SVG strokes.
  //
  // Three earlier versions of this check were wrong. The first counted every
  // element with an opaque background and found 23 "duplicate whites" that were
  // the page's own card and section wrappers. The second filtered by size and
  // saturation and matched *buttons* -- "Add target", "Try again", "Default" --
  // all legitimately the primary colour. The third looked for swatches with no
  // text and found none, because these are not swatches at all: each series is
  // drawn as an SVG line so the guide can show the dash pattern the chart
  // actually encodes. A chart has no CSS background to read.
  const palette = await page.evaluate(() => {
    const sections = [...document.querySelectorAll('section')];
    const section = sections.find((s) => {
      const h = s.querySelector('h2, h3');
      return h && /chart series palette/i.test(h.textContent || '');
    });
    if (!section) return null;

    const strokes = [...section.querySelectorAll('svg line, svg path, svg rect')]
      .map((el) => ({
        token: (el.getAttribute('stroke') || '').trim() ||
               (el.getAttribute('fill') || '').trim(),
        computed: getComputedStyle(el).stroke,
        dash: el.getAttribute('stroke-dasharray') || 'none',
        label: (el.closest('div') || {}).textContent || '',
      }));

    return {
      count: strokes.length,
      // A dash of "0" is solid; anything else is a pattern.
      dashes: strokes.map((s) => s.dash),
      computed: strokes.map((s) => s.computed),
      tokens: strokes.map((s) => s.token),
      labels: strokes.map((s) => s.label.trim().slice(0, 24)),
    };
  });

  if (palette === null) {
    check('the chart palette section is present', false, 'not found');
  } else {
    check('the chart palette draws every series',
          palette.count >= 6, `${palette.count} marks`);
    check('every series resolves to a real colour',
          palette.computed.every((c) => c && c !== 'none'),
          palette.computed.slice(0, 6).join(', '));
    const distinctColours = new Set(palette.computed);
    check('no two series share a colour',
          distinctColours.size === palette.count,
          `${distinctColours.size} distinct of ${palette.count}`);
    const distinctDashes = new Set(palette.dashes);
    check('the series are distinguishable without colour',
          distinctDashes.size >= 4,
          `${distinctDashes.size} dash patterns: ${palette.dashes.join(' / ')}`);
  }

  // --- interactive specimens must respond ---------------------------------
  const interactive = await page.evaluate(() => {
    const out = { buttons: 0, focusable: 0, ariaLabelled: 0, unnamed: [] };
    document.querySelectorAll('button, [role="button"], a[href]').forEach((el) => {
      const r = el.getBoundingClientRect();
      if (r.width < 8 || r.height < 8) return;
      out.buttons += 1;
      const text = (el.textContent || '').trim();
      const label = el.getAttribute('aria-label') || el.getAttribute('title') || '';
      if (text || label) out.ariaLabelled += 1;
      else out.unnamed.push(el.className.toString().slice(0, 60));
      if (el.tabIndex >= 0 || ['BUTTON', 'A'].includes(el.tagName)) out.focusable += 1;
    });
    return out;
  });

  check('every interactive specimen has an accessible name',
        interactive.unnamed.length === 0,
        interactive.unnamed.length ? interactive.unnamed.join(' | ') : `${interactive.buttons} controls`);
  check('every interactive specimen is keyboard reachable',
        interactive.focusable === interactive.buttons,
        `${interactive.focusable}/${interactive.buttons}`);

  // --- text contrast on the guide itself ------------------------------------
  const contrast = await page.evaluate(() => {
    const parse = (value) => {
      const m = value.match(/rgba?\(([^)]+)\)/);
      if (!m) return null;
      const parts = m[1].split(',').map((n) => parseFloat(n));
      return { r: parts[0], g: parts[1], b: parts[2], a: parts.length > 3 ? parts[3] : 1 };
    };
    const luminance = ({ r, g, b }) => {
      const f = (c) => {
        const s = c / 255;
        return s <= 0.03928 ? s / 12.92 : Math.pow((s + 0.055) / 1.055, 2.4);
      };
      return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b);
    };
    const effective = (fg, bg) => ({
      r: fg.r * fg.a + bg.r * (1 - fg.a),
      g: fg.g * fg.a + bg.g * (1 - fg.a),
      b: fg.b * fg.a + bg.b * (1 - fg.a),
      a: 1,
    });

    const bad = [];
    const seen = new Set();
    document.querySelectorAll('p, span, h1, h2, h3, h4, code, label, button, a, li, td, th').forEach((el) => {
      const text = (el.textContent || '').trim();
      if (!text || el.children.length > 0) return;
      const rect = el.getBoundingClientRect();
      if (rect.width < 4 || rect.height < 4) return;
      const cs = getComputedStyle(el);
      const fg = parse(cs.color);
      if (!fg) return;

      // Walk up for the first opaque background.
      let node = el;
      let bg = null;
      while (node) {
        const c = parse(getComputedStyle(node).backgroundColor);
        if (c && c.a > 0.95) { bg = c; break; }
        node = node.parentElement;
      }
      if (!bg) bg = parse(getComputedStyle(document.body).backgroundColor);
      if (!bg) return;

      const merged = effective(fg, bg);
      const l1 = luminance(merged);
      const l2 = luminance(bg);
      const ratio = (Math.max(l1, l2) + 0.05) / (Math.min(l1, l2) + 0.05);
      const size = parseFloat(cs.fontSize);
      const bold = parseInt(cs.fontWeight, 10) >= 700;
      const large = size >= 24 || (size >= 18.66 && bold);
      const need = large ? 3 : 4.5;

      if (ratio < need) {
        const key = `${cs.color}|${cs.fontSize}|${Math.round(ratio * 100)}`;
        if (seen.has(key)) return;
        seen.add(key);
        bad.push({ text: text.slice(0, 34), ratio: ratio.toFixed(2), need,
                   color: cs.color, size: cs.fontSize });
      }
    });
    return bad;
  });

  check('every text specimen meets its contrast requirement',
        contrast.length === 0,
        contrast.length
          ? contrast.map((c) => `"${c.text}" ${c.ratio}:1 (needs ${c.need}) ${c.color} @${c.size}`).slice(0, 5).join(' | ')
          : 'all pass');

  check('no console errors while rendering the guide',
        consoleErrors.length === 0,
        consoleErrors.length ? consoleErrors[0].slice(0, 140) : 'none');

  await browser.close();
  const failed = results.filter((r) => !r.ok);
  console.log(`\n${results.length - failed.length}/${results.length} OK`);
  if (failed.length) {
    console.log('\nfailures:');
    failed.forEach((f) => console.log(`  - ${f.name}`));
    process.exit(1);
  }
})().catch((e) => {
  console.error('FATAL', e.message);
  process.exit(1);
});