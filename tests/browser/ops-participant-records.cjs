// Uses generated local data only. See QUIZ_OPS_RECORDS_FIXTURE for IDs, never real identities.
const { chromium } = require(process.env.PLAYWRIGHT_MODULE_PATH || 'playwright');
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const base = process.env.QUIZ_TEST_URL;
assert(base && ['127.0.0.1', 'localhost'].includes(new URL(base).hostname));
const fixture = JSON.parse(fs.readFileSync(process.env.QUIZ_OPS_RECORDS_FIXTURE, 'utf8'));
assert.equal(fixture.syntheticOnly, true);
const output = path.resolve(process.env.QUIZ_BROWSER_OUTPUT || 'output/playwright/ops-records');
fs.mkdirSync(output, { recursive: true });
(async () => {
  const browser = await chromium.launch({ channel: 'chrome', headless: true });
  const results = [];
  try {
    for (const item of fixture.activities) {
      const context = await browser.newContext({ viewport: { width: item.width, height: 900 }, isMobile: item.width < 640, reducedMotion: 'reduce' });
      const page = await context.newPage();
      page.setDefaultTimeout(15000);
      const errors = [];
      page.on('pageerror', e => errors.push(e.message));
      page.on('console', m => { if (m.type() === 'error' && !m.location().url.endsWith('/favicon.ico')) errors.push(m.text()); });
      const activityUrl = `${base}/ops/activities/${item.activityId}/`;
      const detailUrl = `${activityUrl}participants/${item.participantId}/`;
      await page.goto(activityUrl);
      await page.locator('[name=username]').fill(fixture.username);
      await page.locator('[name=password]').fill(fixture.password);
      await Promise.all([page.waitForURL(activityUrl), page.locator('button[type=submit]').click()]);
      assert.equal(await page.locator('#participantSort').inputValue(), 'recent');
      const firstId = () => page.locator('#participantRows tr[data-participant-id]').first().getAttribute('data-participant-id');
      assert.equal(await firstId(), item.recentId);
      assert.equal(await page.locator('#participantRows tr[data-participant-id]').count(), 50);
      assert.equal(await page.locator(`#attemptPanel a[href$="/participants/${item.participantId}/"]`).count(), 27);
      await page.locator('[data-participant-page=next]').click();
      await page.waitForFunction(() => document.getElementById('participantPageStatus').textContent.includes('第 2 / 2'));
      assert.equal(await page.locator('#participantRows tr[data-participant-id]').count(), 2);
      await page.locator('#participantSort').selectOption('oldest');
      await page.waitForFunction(id => document.querySelector('#participantRows tr').dataset.participantId === id, item.participantId);
      assert((await page.locator('#participantPageStatus').textContent()).includes('第 1 / 2'));
      await page.locator('#participantSort').selectOption('newest');
      await page.waitForFunction(id => document.querySelector('#participantRows tr').dataset.participantId === id, item.newestId);
      await page.locator('#participantSort').selectOption('attempts');
      await page.waitForFunction(id => document.querySelector('#participantRows tr').dataset.participantId === id, item.participantId);
      await page.locator('#participantSearch [name=display_name]').fill('同名样例');
      await page.locator('#participantSearch button').click();
      await page.waitForFunction(() => document.querySelectorAll('#participantRows tr[data-participant-id]').length === 2);
      await page.locator('#participantSort').selectOption('recent');
      await page.waitForFunction(id => document.querySelector('#participantRows tr').dataset.participantId === id, item.recentId);
      assert.equal(await page.locator('#participantSearch [name=display_name]').inputValue(), '同名样例');
      const hrefs = await page.locator('#participantRows .participant-link').evaluateAll(links => links.map(link => link.href));
      assert.equal(new Set(hrefs).size, 2);
      assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
      await page.screenshot({ path: path.join(output, `participants-${item.width}.png`), mask: [page.locator('#participantRows'), page.locator('#participantSearch input'), page.locator('#attemptPanel tbody')], maskColor: '#d9e2f0' });
      await Promise.all([page.waitForURL(detailUrl), page.locator(`#participantRows tr[data-participant-id="${item.participantId}"] .participant-link`).click()]);
      assert.equal(await page.locator('.attempt-record').count(), 25);
      await page.locator('details summary').first().click();
      assert((await page.locator('.submitted-answer').first().textContent()).startsWith('<img'));
      assert.equal(await page.locator('.submitted-answer img').count(), 0);
      assert.equal(await page.evaluate(() => window.badAnswer), undefined);
      assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
      await page.screenshot({ path: path.join(output, `detail-${item.width}.png`), mask: [page.locator('.hero-window h2'), page.locator('.submitted-answer')], maskColor: '#d9e2f0' });
      await Promise.all([page.waitForURL('**/?page=2'), page.getByRole('link', { name: '下一页', exact: true }).click()]);
      assert.equal(await page.locator('.attempt-record').count(), 2);
      await Promise.all([page.waitForURL(activityUrl + '#participantPanel'), page.getByRole('link', { name: '返回活动后台', exact: true }).click()]);
      // Hold an earlier sort response until the newer one has rendered.
      await page.route('**/participants/search/', async route => {
        if (route.request().postData().includes('oldest')) await new Promise(resolve => setTimeout(resolve, 500));
        await route.continue();
      });
      const earlier = page.waitForResponse(r => r.url().endsWith('/participants/search/') && r.request().postData().includes('oldest'));
      await page.locator('#participantSort').selectOption('oldest');
      await page.locator('#participantSort').selectOption('newest');
      await page.waitForFunction(id => document.querySelector('#participantRows tr').dataset.participantId === id, item.newestId);
      await (await earlier).finished();
      await page.waitForTimeout(100);
      assert.equal(await firstId(), item.newestId);
      assert.deepEqual(errors, []);
      results.push({ width: item.width, passed: true, checks: ['recent names', 'same-name IDs', 'global sorts', '52 participants paginated', 'filters', '27 attempts paginated', 'submitted values escaped', 'mobile overflow', 'stale response ignored'] });
      await context.close();
      console.log(`Records viewport ${item.width}: passed`);
    }
    fs.writeFileSync(path.join(output, 'results.json'), JSON.stringify(results, null, 2));
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exit(1); });
