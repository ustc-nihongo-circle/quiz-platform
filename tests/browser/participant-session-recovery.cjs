const { chromium } = require(process.env.PLAYWRIGHT_MODULE_PATH || 'playwright');
const fs = require('node:fs');
const assert = require('node:assert/strict');
const base = process.env.QUIZ_TEST_URL;
if (!base) throw new Error('Set QUIZ_TEST_URL to the test site.');
(async () => {
  const browser = await chromium.launch({channel:'chrome',headless:true,args:['--no-proxy-server']});
  try {
    const context = await browser.newContext();
    await context.addInitScript(() => {
      sessionStorage.setItem('ustc-nihongo-quiz:participant',JSON.stringify({id:'expired-local-fixture',display_name:'旧标签页'}));
      sessionStorage.setItem('ustc-nihongo-quiz:last-attempt','expired-attempt');
      sessionStorage.setItem('ustc-nihongo-quiz:draft:kept-fixture','{"sample":"B"}');
    });
    const page = await context.newPage();
    await page.route(/\/static\/quiz\/participant.*\.js(?:\?|$)/, route => route.fulfill({contentType:'application/javascript',body:fs.readFileSync('src/quiz/static/quiz/participant.js','utf8')}));
    await page.goto(base+'/participant/',{waitUntil:'networkidle'});
    await page.waitForFunction(() => window.__prototypeReady);
    assert.equal(await page.locator('body').getAttribute('data-screen'),'register','Expired browser identity must not override the server session');
    assert.equal(await page.evaluate(() => sessionStorage.getItem('ustc-nihongo-quiz:participant')),null);
    assert.equal(await page.evaluate(() => sessionStorage.getItem('ustc-nihongo-quiz:draft:kept-fixture')),'{"sample":"B"}','Do not erase saved answers during session reconciliation');
    console.log('STALE_SESSION_RECOVERY_PASSED');
  } finally { await browser.close(); }
})().catch(error => {console.error(error);process.exitCode=1;});
