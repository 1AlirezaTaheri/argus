/**
 * Remove the probe rule left behind by an earlier API test.
 *
 * `rules_api_probe.js` created a rule and its DELETE returned 400 -- the
 * endpoint requires `confirm=true`, so that run's assertion was wrong, not the
 * endpoint. The follow-up proved the endpoint was fine and cleaned up its own
 * rule, but this earlier one was never removed.
 *
 * It has been sitting in the operator's list ever since: disabled, priority 1,
 * described "probe: temporary". And because it is the only rule in the
 * deployment, it reads as a deliberate configuration rather than residue.
 *
 * Only rules matching the probe signature are touched. Anything an operator
 * actually wrote is left alone -- "probe" in a description is a hint, not
 * grounds, so both the name and the description have to match.
 */
const puppeteer = require('puppeteer');

const BASE = process.env.TARGET_BASE || 'https://127.0.0.1:8443';
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function api(page, path, method, body) {
  return page.evaluate(async (p, m, b) => {
    const res = await fetch(p, {
      method: m,
      credentials: 'same-origin',
      headers: b ? { 'Content-Type': 'application/json' } : undefined,
      body: b ? JSON.stringify(b) : undefined,
    });
    let parsed = null;
    try { parsed = await res.json(); } catch (e) { /* not json */ }
    return { status: res.status, body: parsed };
  }, path, method, body || null);
}

// Deliberately narrow: a name that starts with the probe prefix AND a
// description that says it was temporary.
const isProbeResidue = (rule) =>
  /^(probe|ui-probe)-[0-9a-f]{6,}$/.test(rule.name || '') &&
  /^probe: temporary$/i.test((rule.description || '').trim());

(async () => {
  const browser = await puppeteer.launch({
    headless: 'new',
    protocolTimeout: 120000,
    args: ['--no-sandbox', '--disable-setuid-sandbox', '--disable-dev-shm-usage',
           '--ignore-certificate-errors'],
  });
  const page = await (await browser.createBrowserContext()).newPage();

  await page.goto(`${BASE}/admin/login`, { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('#username');
  await page.type('#username', process.env.ADMIN_USERNAME, { delay: 3 });
  await page.type('#password', process.env.ADMIN_PASSWORD, { delay: 3 });
  await Promise.all([
    page.click('button[type="submit"]'),
    page.waitForNavigation({ waitUntil: 'domcontentloaded' }).catch(() => {}),
  ]);

  const listed = await api(page, '/api/rules', 'GET');
  const rules = (listed.body && listed.body.rules) || [];
  console.log(`${rules.length} rule(s) present:`);
  for (const rule of rules) {
    console.log(`  id=${rule.id} ${rule.name} type=${rule.rule_type} ` +
                `value=${rule.value} enabled=${rule.enabled} ` +
                `priority=${rule.priority} desc=${JSON.stringify(rule.description)}`);
  }

  let removed = 0;
  for (const rule of rules) {
    if (!isProbeResidue(rule)) {
      console.log(`  keeping ${rule.name} — not probe residue`);
      continue;
    }
    const res = await api(page, `/api/rules/${rule.id}?confirm=true`, 'DELETE');
    console.log(`  removed ${rule.name} -> ${res.status}`);
    if (res.status === 200) removed += 1;
  }

  await sleep(500);
  const after = await api(page, '/api/rules', 'GET');
  const remaining = (after.body && after.body.rules) || [];
  console.log(`\nremoved ${removed}; ${remaining.length} rule(s) remain`);
  remaining.forEach((r) =>
    console.log(`  id=${r.id} ${r.name} enabled=${r.enabled}`));

  await browser.close();
})().catch((e) => {
  console.error('FATAL', e.message);
  process.exit(1);
});