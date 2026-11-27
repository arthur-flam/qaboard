// Drives the app of `qa mcp` in a host that uses the official AppBridge, and fails if the protocol breaks:
// the handshake, sizing, links, messages to the chat, and tool calls from the app. Screenshots in ./screenshots/.
// Start `python harness.py` first (see README.md).
const { chromium } = require('playwright');
const assert = require('assert');
const fs = require('fs');
const HOST = process.env.HOST || 'http://127.0.0.1:5199';
const NEW = 'a1b2c3d4' + '0'.repeat(32);

(async () => {
  const fake = await (await fetch(`${HOST}/fake`)).text();
  fs.mkdirSync('screenshots', { recursive: true });
  const browser = await chromium.launch();
  async function open(tool, args, theme = 'light') {
    const page = await browser.newPage({ viewport: { width: 780, height: 900 } });
    const errors = [];
    page.on('console', m => { if (['error', 'warning'].includes(m.type())) errors.push(m.text()); });
    page.on('pageerror', e => errors.push(String(e)));
    await page.goto(`${HOST}/host.html?tool=${tool}&theme=${theme}&args=${encodeURIComponent(JSON.stringify(args))}`);
    await page.waitForFunction(() => window.events?.some(e => e.type === 'result-sent'), null, { timeout: 30000 });
    await page.waitForTimeout(500);
    const events = () => page.evaluate(() => window.events);
    return { page, frame: page.frames()[1], errors, events };
  }

  // compare: links, a question to the chat, then the app asks the server for a test's images
  let { page, frame, errors, events } = await open('compare', { url: `${fake}/group/repo/commit/${NEW}` });
  assert.ok((await events()).some(e => e.type === 'initialized'), 'the app initializes with the host');
  assert.ok((await events()).some(e => e.type === 'size'), 'the app tells the host its size');
  await frame.click('text=Open the Metrics Diff');
  await frame.click('text=Ask about the worst regression');
  await frame.click('td.name >> text=night/02');
  await frame.waitForSelector('#image figure img', { timeout: 30000 });
  await page.screenshot({ path: 'screenshots/compare.png', fullPage: true });
  let seen = await events();
  assert.ok(seen.some(e => e.type === 'open-link' && e.url.includes('selected_views=table-compare')));
  assert.ok(seen.some(e => e.type === 'message' && e.content[0].text.includes('night/02')));
  assert.deepStrictEqual(seen.filter(e => e.type === 'tools/call').map(e => e.arguments), [{ run_id: 2 }, { run_id: 2, path: 'output.png', reference_run_id: 12 }]);
  assert.deepStrictEqual(errors, []);
  // the actions of what a drill-down added work: another output of the test
  await frame.click('#drill button:has-text("junit.xml")');
  await frame.waitForSelector('#image table', { timeout: 30000 });
  assert.match(await frame.locator('#image table').innerText(), /broken/);
  assert.deepStrictEqual(errors, []);

  // results are data: HTML in a project's metric units, test names or messages doesn't run
  await page.evaluate(() => window.bridge.sendToolResult({
    content: [{ type: 'text', text: 'x' }, { type: 'image', data: '"><img src=x onerror="window.parent.pwned=1">', mimeType: 'image/png' }],
    structuredContent: {
      view: 'compare', project: '<img src=x onerror="window.parent.pwned=1">', status: 'complete', messages: ['<b id=injected>m</b>'],
      commit: { id: 'abc', message: '<b id=injected>', runs: { total: '<b id=injected>' } }, reference: { id: 'def', name: '<b id=injected>' },
      metrics: [{ metric: '<b id=injected>', suffix: '<img src=x id=injected onerror="window.parent.pwned=1">', new: 1, ref: 2, delta_relative: -0.5,
                  median: 'worse', consistency: '<b id=injected>', tests: { better: '<b id=injected>', worse: 1, same: 0 }, smaller_is_better: false }],
      movers: [{ metric: 'm', regressions: [{ run_id: '<b id=injected>', test: '<b id=injected>', new: 1, ref: 2, delta_relative: -0.5 }], improvements: [] }],
      failed: 0, links: {},
    },
  }));
  await page.waitForTimeout(500);
  assert.strictEqual(await frame.locator('#injected').count(), 0, 'HTML from results is shown as text');
  assert.strictEqual(await page.evaluate(() => window.pwned), undefined, 'scripts from results never run');
  assert.ok((await frame.locator('table').first().innerText()).includes('<img src=x'), 'the text is still shown');
  // a view that replaces the page drops the actions of what it replaced
  const [registered, onPage] = await frame.evaluate(() => [Object.keys(actions).sort(), [...document.querySelectorAll('[data-action]')].map(e => e.dataset.action).sort()]);
  assert.deepStrictEqual(registered, onPage);

  // metric_history, in the dark
  ({ page, frame, errors, events } = await open('metric_history', { project: 'group/repo', label: 'nightly' }, 'dark'));
  assert.strictEqual(await frame.locator('circle').count(), 3);
  await frame.click('text=/Ask about a1b2c3d4/');
  await page.screenshot({ path: 'screenshots/history-dark.png', fullPage: true });
  assert.ok((await events()).some(e => e.type === 'message' && e.content[0].text.includes('compare')));
  assert.deepStrictEqual(errors, []);

  // look_at_output: new and reference, whole and where they differ
  ({ page, frame, errors } = await open('look_at_output', { run_id: 2, path: 'output.png' }));
  assert.strictEqual(await frame.locator('figure img').count(), 4);
  await page.screenshot({ path: 'screenshots/images.png', fullPage: true });
  assert.deepStrictEqual(errors, []);

  // outputs compared by their format: a table, and the curves of new and reference
  ({ page, frame, errors } = await open('look_at_output', { run_id: 2, path: 'series.json' }));
  assert.match(await frame.locator('table').innerText(), /train\/loss/);
  assert.strictEqual(await frame.locator('[data-chart] path').count(), 2 * 3, 'new and reference, for each series');
  await page.screenshot({ path: 'screenshots/series.png', fullPage: true });
  assert.deepStrictEqual(errors, []);

  // the files say their type; one opens next to the reference's
  ({ page, frame, errors, events } = await open('look_at_output', { run_id: 2 }));
  assert.match(await frame.locator('button', { hasText: 'junit.xml' }).innerText(), /junit/);
  await frame.click('button:has-text("junit.xml")');
  await frame.waitForSelector('#image table', { timeout: 30000 });
  assert.match(await frame.locator('#image table').innerText(), /broken/);
  assert.deepStrictEqual((await events()).filter(e => e.type === 'tools/call').map(e => e.arguments), [{ run_id: 2, path: 'junit.xml', reference_run_id: 12 }]);
  await page.screenshot({ path: 'screenshots/junit.png', fullPage: true });
  assert.deepStrictEqual(errors, []);

  // results are data in tables too
  await page.evaluate(() => window.bridge.sendToolResult({
    content: [{ type: 'text', text: 'x' }],
    structuredContent: {
      view: 'table', path: '<b id=injected>', run_id: '<b id=injected>', title: '`<img src=x id=injected onerror="window.parent.pwned=1">`',
      summary: ['<b id=injected>', '`</code><b id=injected>`'], columns: ['<b id=injected>'], more: '<b id=injected>',
      rows: [{ cells: ['<b id=injected>', '<img src=x id=injected onerror="window.parent.pwned=1">'], verdict: '"><b id=injected>' }],
      charts: [{ name: '<b id=injected>', new: [[0, '<b>'], [1, 2], [2, 3]], reference: '<b id=injected>' }], links: {},
    },
  }));
  await page.waitForTimeout(500);
  assert.strictEqual(await frame.locator('#injected').count(), 0, 'HTML from results is shown as text');
  assert.strictEqual(await page.evaluate(() => window.pwned), undefined, 'scripts from results never run');
  assert.match(await frame.locator('table').innerText(), /<img src=x/);

  // errors are shown
  ({ frame } = await open('look_at_output', { run_id: 999 }));
  assert.match(await frame.locator('pre').innerText(), /No run 999/);

  await browser.close();
  console.log('OK: the app works with the official AppBridge');
})().catch(e => { console.error(e); process.exit(1); });
