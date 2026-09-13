// Run against a test site; API responses are isolated synthetic fixtures.
const { chromium } = require(process.env.PLAYWRIGHT_MODULE_PATH || 'playwright');
const fs = require('node:fs');
const assert = require('node:assert/strict');
const base = process.env.QUIZ_TEST_URL;
if (!base) throw new Error('Set QUIZ_TEST_URL to the test site.');
const source = fs.readFileSync('src/quiz/static/quiz/participant.js', 'utf8');
const activity = {code:'network-check', status:'open', categories:[
  {code:'sample', title:'网络恢复验证', question_count:2, time_limit_seconds:120},
]};
(async () => {
  const browser = await chromium.launch({channel:'chrome', headless:true, args:['--no-proxy-server']});
  try {
    for (const scenario of ['initial-failure', 'persistent-failure', 'invalid-json', 'timeout', 'write-failure']) {
      const context = await browser.newContext({viewport:{width:390,height:844},isMobile:true});
      const page = await context.newPage();
      let activityCalls = 0, registrations = 0, recovered = false;
      await page.route(/\/static\/quiz\/participant.*\.js(?:\?|$)/, route => route.fulfill({contentType:'application/javascript',body:source}));
      await page.route('**/api/v1/**', async route => {
        const pathname = new URL(route.request().url()).pathname;
        if (pathname === '/api/v1/activity') {
          activityCalls++;
          if (!recovered && scenario !== 'write-failure' && (scenario === 'persistent-failure' || activityCalls === 1)) {
            if (scenario === 'invalid-json') return route.fulfill({status:502,contentType:'text/html',body:'<html>Gateway unavailable</html>'});
            if (scenario === 'timeout') { await new Promise(resolve => setTimeout(resolve, 16000)); }
            else return route.abort('failed');
          }
          return route.fulfill({json:{activity}});
        }
        if (pathname === '/api/v1/participant-session') {
          registrations++;
          if (scenario === 'write-failure' && registrations === 1) return route.fulfill({status:502,contentType:'text/html',body:'<html>Gateway unavailable</html>'});
          return route.fulfill({status:201,json:{created:true,participant:{id:'browser-fixture',display_name:'网络恢复验证',best_scores:{}}}});
        }
        throw new Error('Unexpected API route: '+pathname);
      });
      await page.goto(base+'/participant/', {waitUntil:'load'});
      await page.waitForFunction(() => window.__prototypeReady, null, {timeout:45000});
      await page.locator('input[name=display_name]').fill('网络恢复验证');
      await page.locator('input[name=identifier]').fill('SYNTHETIC-NETWORK');
      await page.locator('input[name=contact_type][value=email]').check();
      await page.locator('input[name=contact]').fill('network-check@example.invalid');
      if (scenario === 'persistent-failure') {
        await page.locator('#registrationSubmit').click();
        await page.waitForFunction(() => !document.querySelector('#registrationSubmit').disabled, null, {timeout:45000});
        assert.equal(registrations,0,'Do not create a session while activity is unavailable');
        assert.match(await page.locator('#registrationFeedback').innerText(), /网络|超时|响应/);
        recovered = true;
      }
      await page.locator('#registrationSubmit').click();
      if (scenario === 'write-failure') {
        await page.waitForFunction(() => !document.querySelector('#registrationSubmit').disabled);
        assert.equal(registrations,1,'Never automatically repeat a write');
        assert.match(await page.locator('#registrationFeedback').innerText(), /服务响应/);
        await page.locator('#registrationFeedback button').click();
      }
      await page.waitForFunction(() => document.body.dataset.screen === 'sections',null,{timeout:45000});
      assert.equal(registrations,scenario === 'write-failure' ? 2 : 1);
      assert.equal(await page.locator('#registrationSubmit').isDisabled(),false);
      assert.equal(await page.locator('#registrationSubmit span').first().innerText(),'登记并继续');
      console.log(JSON.stringify({scenario,status:'passed',activityCalls,registrations}));
      await context.close();
    }
  } finally { await browser.close(); }
})().catch(error => {console.error(error);process.exitCode=1;});
