const { chromium } = require(process.env.PLAYWRIGHT_MODULE_PATH || 'playwright');
const fs = require('node:fs');
const assert = require('node:assert/strict');
const base = process.env.QUIZ_TEST_URL;
if (!base) throw new Error('Set QUIZ_TEST_URL to the test site.');
(async () => {
  const browser = await chromium.launch({channel:'chrome',headless:true,args:['--no-proxy-server']});
  fs.mkdirSync('output/playwright/fill-blank',{recursive:true});
  try {
    for (const width of [360,390,1440]) {
      const page = await browser.newPage({viewport:{width,height:844},isMobile:width<500});
      await page.route(/\/static\/quiz\/participant.*\.css(?:\?|$)/, route => route.fulfill({contentType:'text/css',body:fs.readFileSync('src/quiz/static/quiz/participant.css','utf8')}));
      await page.goto(base+'/participant/',{waitUntil:'networkidle'});
      await page.waitForFunction(() => window.__prototypeReady);
      await page.evaluate(() => app.renderQuiz({id:'fill-fixture',category:{title:'输入框验证'},question_count:1,deadline_at:new Date(Date.now()+120000).toISOString(),questions:[{id:'fill-item',position:1,type:'fill_blank',prompt:'填空输入显示验证',image_url:null}]}));
      const input = page.locator('#answerOptions input[type=text]');
      await input.fill('可见答案');
      const style = await input.evaluate(el => ({opacity:getComputedStyle(el).opacity,position:getComputedStyle(el).position,width:el.getBoundingClientRect().width}));
      assert.equal(style.opacity,'1','Answer text must be painted, not just stored');
      assert.notEqual(style.position,'absolute');
      assert.ok(style.width>=150,'Mobile input must leave room to read the answer');
      await page.evaluate(() => app.renderCurrentQuestion());
      assert.equal(await input.inputValue(),'可见答案');
      await input.scrollIntoViewIfNeeded();
      await page.locator('#answerOptions').screenshot({path:`output/playwright/fill-blank/input-${width}.png`});
      console.log(JSON.stringify({width,status:'passed',style,draftRestored:true}));
      await page.close();
    }
  } finally { await browser.close(); }
})().catch(error => {console.error(error);process.exitCode=1;});
