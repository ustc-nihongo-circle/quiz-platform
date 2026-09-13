// Synthetic text only. Verify safe formatting through the actual participant UI.
const {chromium}=require(process.env.PLAYWRIGHT_MODULE_PATH || 'playwright');
const fs=require('node:fs');
const assert=require('node:assert/strict');
const base=process.env.QUIZ_TEST_URL;
const localSource=process.env.QUIZ_LOCAL_JS==='1';
const questions=[
 {id:'underline',position:1,prompt:'読み方：<span style="text-decoration:underline;">対象語</span>。',type:'single_choice',image_url:null,options:[{id:'A',text:'<span style="text-decoration:underline;">強調</span>'},{id:'B',text:'普通'}]},
 {id:'inert',position:2,prompt:'比較 < 5。<img src="/markup-probe" onerror="window.markupExecuted=true"><script>window.markupExecuted=true</script>',type:'single_choice',image_url:null,options:[{id:'A',text:'<span style="text-decoration:underline;" onclick="window.markupExecuted=true">保留文字</span>'},{id:'B',text:'普通'}]},
 {id:'spacing',position:3,prompt:"前<span style=' text-decoration : underline ; '>一</span>中<span style=\"text-decoration:underline;\">二</span>後",type:'single_choice',image_url:null,options:[{id:'A',text:'普通'},{id:'B',text:'普通'}]},
];
async function screen(p,name){await p.waitForFunction(s=>document.body.dataset.screen===s,name);}
(async()=>{
 const browser=await chromium.launch({channel:'chrome',headless:true,args:['--no-proxy-server']});
 try {
  for(const [width,height] of [[1440,900],[390,844],[360,800]]) {
   const context=await browser.newContext({viewport:{width,height},isMobile:width<500,reducedMotion:'reduce'});
   const page=await context.newPage();
   let attemptedUnsafeRequests=0,attempt=null;
   page.on('request',r=>{if(r.url().includes('/markup-probe'))attemptedUnsafeRequests++;});
   if(localSource) await page.route('**/static/quiz/participant.js*',r=>r.fulfill({contentType:'application/javascript',body:fs.readFileSync('src/quiz/static/quiz/participant.js','utf8')}));
   await page.route('**/api/v1/**',r=>{
    const p=new URL(r.request().url()).pathname;
    if(p==='/api/v1/activity')return r.fulfill({json:{activity:{code:'isolated-markup',status:'open',categories:[{code:'demo',title:'表示検証',question_count:3,time_limit_seconds:300}]}}});
    if(p==='/api/v1/participant-session')return r.fulfill({status:201,json:{created:true,participant:{id:'synthetic-markup',display_name:'表示検証'}}});
    if(p==='/api/v1/attempts/current')return r.fulfill({status:404,json:{error:{code:'attempt_not_found'}}});
    if(p==='/api/v1/attempts') {attempt={id:'markup-attempt',status:'in_progress',category:{code:'demo',title:'表示検証'},started_at:new Date().toISOString(),deadline_at:new Date(Date.now()+300000).toISOString(),question_count:3,questions};return r.fulfill({status:201,json:{attempt,created:true}});}
    if(p.endsWith('/submission'))return r.fulfill({json:{attempt:{...attempt,status:'submitted',score:0,score_rate:0,category_high_score:0,questions:questions.map(q=>({...q,correct:false,answer:'A'}))}}});
    throw new Error('Unexpected intercepted request');
   });
   await page.goto(base+'/participant/',{waitUntil:'networkidle'});
   await screen(page,'register');
   await page.locator('input[name=display_name]').fill('表示検証');
   await page.locator('input[name=identifier]').fill('SYNTHETIC-MARKUP');
   await page.locator('input[name=contact_type][value=email]').check();
   await page.locator('input[name=contact]').fill('markup@example.invalid');
   await page.locator('#registrationSubmit').click(); await screen(page,'sections');
   await page.locator('#categoryGrid button').click(); await screen(page,'quiz');
   assert.equal(await page.locator('#questionPrompt').textContent(),'読み方：対象語。');
   assert.equal(await page.locator('#questionPrompt u').textContent(),'対象語');
   assert.ok((await page.locator('#questionPrompt u').evaluate(el=>getComputedStyle(el).textDecorationLine)).includes('underline'));
   assert.equal(await page.locator('#answerOptions u').textContent(),'強調');
   await page.locator('#questionMap button').nth(1).click();
   assert.equal(await page.locator('#questionPrompt').textContent(),questions[1].prompt);
   assert.equal(await page.locator('#questionPrompt img, #questionPrompt script, #answerOptions [onclick]').count(),0);
   assert.equal(await page.evaluate(()=>window.markupExecuted===true),false);
   await page.locator('#questionMap button').nth(2).click();
   assert.equal(await page.locator('#questionPrompt').textContent(),'前一中二後');
   assert.equal(await page.locator('#questionPrompt u').count(),2);
   assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
   await page.locator('#submitAttempt').click();
   await page.getByRole('button',{name:'确认交卷',exact:true}).click(); await screen(page,'result');
   assert.equal(await page.locator('#reviewList li').first().locator('b').textContent(),'読み方：対象語。');
   assert.equal(await page.locator('#reviewList u').count(),3);
   assert.equal(await page.locator('#reviewList img, #reviewList script, #reviewList [onclick]').count(),0);
   assert.equal(attemptedUnsafeRequests,0);
   console.log(JSON.stringify({viewport:[width,height],prompt:true,options:true,results:true,unsafeMarkupInert:true,status:'passed'}));
   await context.close();
  }
 }finally{await browser.close();}
})().catch(e=>{console.error(e.message.split('\n')[0]);process.exitCode=1;});
