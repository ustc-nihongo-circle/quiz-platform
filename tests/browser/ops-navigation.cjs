// Run only against a disposable local database with generated fixture data.
const { chromium } = require(process.env.PLAYWRIGHT_MODULE_PATH || 'playwright');
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const base = process.env.QUIZ_TEST_URL;
assert(base && ['127.0.0.1', 'localhost'].includes(new URL(base).hostname), 'Local fixture server required');
const fixture = JSON.parse(fs.readFileSync(process.env.QUIZ_OPS_FIXTURE, 'utf8'));
assert.equal(fixture.syntheticOnly, true, 'Only generated local data may be changed');
const output = path.resolve(process.env.QUIZ_BROWSER_OUTPUT || 'output/playwright/ops-navigation');
fs.mkdirSync(output, { recursive: true });
const ids = ['participantPanel', 'attemptPanel', 'leaderboardPanel', 'exportPanel', 'activityControls', 'auditPanel'];
async function downloadText(download) {
  const chunks = [];
  for await (const chunk of await download.createReadStream()) chunks.push(chunk);
  return Buffer.concat(chunks).toString('utf8');
}
(async () => {
  const browser = await chromium.launch({ channel: 'chrome', headless: true });
  const results = [];
  try {
    for (const item of fixture.activities) {
      const width = item.width;
      const context = await browser.newContext({ viewport: { width, height: width > 640 ? 1000 : 844 }, isMobile: width < 640, reducedMotion: 'reduce' });
      const page = await context.newPage();
      page.setDefaultTimeout(15000);
      page.setDefaultNavigationTimeout(15000);
      const errors = [];
      page.on('pageerror', error => errors.push(error.message));
      page.on('console', message => {
        // The existing local app has no favicon; keep all application errors visible.
        if (message.type() === 'error' && !message.location().url.endsWith('/favicon.ico')) errors.push(message.text());
      });
      const activityUrl = `${base}/ops/activities/${item.activityId}/`;
      await page.goto(activityUrl);
      await page.locator('[name=username]').fill(fixture.username);
      await page.locator('[name=password]').fill(fixture.password);
      await Promise.all([page.waitForURL(activityUrl), page.getByRole('button', { name: '登录', exact: true }).click()]);
      await page.waitForFunction(() => document.querySelector('[data-metric=participants]').textContent === '1');
      assert.deepEqual(await page.locator('main.console > section[id]').evaluateAll(elements => elements.map(e => e.id)), ids);
      assert.equal(await page.locator('form[action*="reward-rules"], [data-deactivate-rule-url]').count(), 0);
      assert(!(await page.locator('body').innerText()).includes('REMOVED_REWARD_PANEL_SENTINEL'));
      assert(!(await page.locator('body').innerText()).includes('兑奖词'));
      const go = async id => {
        if (width < 640) await page.locator('#sectionSelect').selectOption(id);
        else await page.locator(`[data-section-link][href="#${id}"]`).click();
        await page.waitForFunction(id => document.querySelector('#sectionSelect').value === id, id);
        const position = await page.locator('#' + id).evaluate(el => ({ top: el.getBoundingClientRect().top, height: innerHeight, header: parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--ops-header-height')) }));
        assert(position.top >= position.header - 1 && position.top < position.height, `Target heading hidden: ${id}`);
        assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), 'Page overflow');
      };
      for (const id of ids) await go(id);
      await page.reload();
      await page.waitForFunction(() => document.querySelector('#sectionSelect').value === 'auditPanel');
      await go('participantPanel');
      await page.locator('#participantSearch [name=participant_id]').fill(item.participantId);
      await go('attemptPanel');
      await go('participantPanel');
      assert.equal(await page.locator('#participantSearch [name=participant_id]').inputValue(), item.participantId);
      await Promise.all([page.waitForResponse(r => r.url().includes('/participants/search/') && r.status() === 200), page.locator('#participantSearch button').click()]);
      assert.equal(await page.locator('#participantRows tr[data-participant-id]').count(), 1);
      await Promise.all([page.waitForResponse(r => r.url().includes('/participants/reveal/') && r.status() === 200), page.locator('[data-reveal=identifier]').click()]);
      assert.equal(await page.locator('#participantRows [data-field=identifier]').innerText(), item.identifier);
      await page.locator('[data-reveal=identifier]').click();
      assert.notEqual(await page.locator('#participantRows [data-field=identifier]').innerText(), item.identifier);
      await go('leaderboardPanel');
      await Promise.all([page.waitForResponse(r => r.url().includes('/leaderboard/') && r.status() === 200), page.locator('#leaderboardCategory').selectOption('culture')]);
      assert.equal(await page.locator('#leaderboardRows li').count(), 0);
      await Promise.all([page.waitForResponse(r => r.url().includes('/leaderboard/') && r.status() === 200), page.locator('#leaderboardCategory').selectOption('language')]);
      assert.equal(await page.locator('#leaderboardRows li').count(), 1);
      await go('exportPanel');
      const [statistics] = await Promise.all([page.waitForEvent('download'), page.getByRole('button', { name: '导出无身份统计', exact: true }).click()]);
      const statisticsCsv = await downloadText(statistics);
      assert(statisticsCsv.includes(item.participantId));
      assert(!statisticsCsv.includes(item.identifier));
      const identityForm = page.locator('#exportPanel form[action$="identities/"]');
      assert.equal(await identityForm.evaluate(form => form.checkValidity()), false);
      await identityForm.locator('[name=reason]').fill('本地合成数据导航验收');
      const [identities] = await Promise.all([page.waitForEvent('download'), identityForm.locator('button').click()]);
      assert((await downloadText(identities)).includes(item.identifier));
      await go('activityControls');
      const entryForm = page.locator('form[action$="participant-entry/"]');
      if (await entryForm.count()) {
        await entryForm.locator('[name=reason]').fill('本地合成活动入口验证');
        await Promise.all([page.waitForEvent('load'), entryForm.locator('button').click()]);
      }
      const pause = page.locator('form.ajax-form').filter({ has: page.locator('input[name=status][value=paused]') });
      await pause.locator('[name=reason]').fill('本地导航暂停验证');
      await Promise.all([page.waitForEvent('load'), pause.locator('button').click()]);
      assert(await page.locator('.status-chip.status-paused').count());
      const resume = page.locator('form.ajax-form').filter({ has: page.locator('input[name=status][value=open]') });
      await resume.locator('[name=reason]').fill('本地导航恢复验证');
      await Promise.all([page.waitForEvent('load'), resume.locator('button').click()]);
      assert(await page.locator('.status-chip.status-open').count());
      await go('attemptPanel');
      page.once('dialog', dialog => dialog.accept('本地合成答卷作废验证'));
      await Promise.all([page.waitForEvent('load'), page.locator('[data-invalidate-url]').click()]);
      assert.equal(await page.locator('[data-invalidate-url]').count(), 0);
      await go('auditPanel');
      assert((await page.locator('#auditPanel').innerText()).includes('attempt_invalidated'));
      await page.evaluate(() => window.scrollTo(0, 0));
      // Mask all identity cells in screenshots, including synthetic test names.
      const mask = [page.locator('#participantRows'), page.locator('#leaderboardRows')];
      await page.screenshot({ path: path.join(output, `console-${width}.png`), mask, maskColor: '#d9e2f0' });
      await go('exportPanel');
      await page.screenshot({ path: path.join(output, `exports-${width}.png`), mask, maskColor: '#d9e2f0' });
      await Promise.all([page.waitForURL(`${base}/ops/activities/${fixture.draftId}/`), page.locator('#activitySelect').selectOption(`/ops/activities/${fixture.draftId}/`)]);
      assert(await page.locator('.status-chip.status-draft').count());
      assert((await page.locator('#participantRows').textContent()).includes('暂无参与者'));
      await Promise.all([page.waitForURL(activityUrl), page.locator('#activitySelect').selectOption(`/ops/activities/${item.activityId}/`)]);
      await page.setViewportSize({ width: width < 640 ? 1440 : 390, height: 900 });
      assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
      // Entry selection correctly rejects switching away from an open/paused activity.
      // Close this disposable activity before the next viewport selects its own fixture.
      const close = page.locator('form.ajax-form').filter({ has: page.locator('input[name=status][value=closed]') });
      await close.locator('[name=reason]').fill('本地合成活动验收结束');
      await Promise.all([page.waitForEvent('load'), close.locator('button').click()]);
      await page.getByRole('button', { name: '退出', exact: true }).click();
      await page.waitForURL('**/ops/login/');
      assert.deepEqual(errors, []);
      results.push({ width, status: 'passed', flows: ['login', 'six anchors', 'hash reload', 'search draft retention', 'search', 'reveal and mask', 'leaderboard', 'two exports', 'entry', 'pause/resume', 'invalidate', 'audit', 'empty activity', 'resize', 'close', 'logout'] });
      console.log(`Viewport ${width}: passed`);
      await context.close();
    }
    fs.writeFileSync(path.join(output, 'results.json'), JSON.stringify(results, null, 2));
    console.log(JSON.stringify({ passed: results.length, output }));
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exit(1); });
