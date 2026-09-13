const {chromium}=require(process.env.PLAYWRIGHT_MODULE_PATH || 'playwright');
const fs=require('node:fs');
const assert=require('node:assert/strict');
const base=process.env.QUIZ_TEST_URL;
(async()=>{
 const browser=await chromium.launch({channel:'chrome',headless:true,args:['--no-proxy-server',...(base.includes(':28444')?['--host-resolver-rules=MAP test.ustc-nihongo-circle.top 127.0.0.1']:[])]});
 const results=[];
 try {
  for(const [width,height] of [[1440,900],[390,844]]) {
   const context=await browser.newContext({viewport:{width,height}});
   const page=await context.newPage();
   page.on('pageerror',e=>console.error('browser error:',e.message));
   const counts={register:0,start:0,submit:0,unsafe:0};
   const malicious='<img src="/security-probe" onerror="window.securityInjected=true">';
   let attempt;
   page.on('request',r=>{if(r.url().includes('/security-probe'))counts.unsafe++;});
   if(process.env.QUIZ_LOCAL_JS==='1') await page.route('**/static/quiz/participant.js*',r=>r.fulfill({contentType:'application/javascript',body:fs.readFileSync('src/quiz/static/quiz/participant.js','utf8')}));
   await page.route('**/api/v1/**',r=>{
    const path=new URL(r.request().url()).pathname;
    const limited=()=>r.fulfill({status:429,headers:{'Retry-After':'2'},json:{error:{code:'rate_limited',message:'请稍后重试',retryable:true,retry_after_seconds:2}}});
    if(path==='/api/v1/activity')return r.fulfill({json:{activity:{code:'security-test',status:'open',categories:[{code:malicious,title:malicious,question_count:1,time_limit_seconds:300}]}}});
    if(path==='/api/v1/participant-session') {counts.register++;return counts.register===1?limited():r.fulfill({status:201,json:{created:true,participant:{id:'security-test',display_name:'合成用户'}}});}
    if(path==='/api/v1/attempts') {
     counts.start++;if(counts.start===1)return limited();
     attempt={id:'security-attempt',status:'in_progress',category:{code:'synthetic',title:'测试板块'},question_count:1,started_at:new Date().toISOString(),deadline_at:new Date(Date.now()+300000).toISOString(),questions:[{id:'q',position:1,type:'fill_blank',prompt:'合成填空题',image_url:null,options:[]}]};
     return r.fulfill({status:201,json:{attempt}});
    }
    if(path.endsWith('/submission')) {counts.submit++;if(counts.submit===1)return limited();return r.fulfill({json:{attempt:{...attempt,status:'submitted',score:1,score_rate:1,category_high_score:1,questions:[{...attempt.questions[0],answer:'保留草稿',correct:true}]}}});}
    return r.fulfill({status:404,json:{error:{code:'attempt_not_found'}}});
   });
   await page.goto(base+'/participant/',{waitUntil:'networkidle'});
   await page.waitForFunction(()=>window.__prototypeReady===true);
   console.log(JSON.stringify({width,initial_screen:await page.locator('body').getAttribute('data-screen'),feedback:await page.locator('#registrationFeedback').textContent()}));
   await page.locator('[name=display_name]').fill('安全验证');
   await page.locator('[name=identifier]').fill('SYNTHETIC-SECURITY');
   await page.locator('[name=contact_type][value=email]').check();
   await page.locator('[name=contact]').fill('security@example.com');
   await page.locator('#registrationSubmit').click();
   await page.waitForTimeout(400);
   assert.equal(await page.locator('#registrationSubmit').isDisabled(),true);
   assert.equal(await page.locator('[name=identifier]').inputValue(),'SYNTHETIC-SECURITY');
   await page.waitForTimeout(2100);
   assert.equal(counts.register,1);
   await page.locator('#registrationSubmit').click();
   await page.waitForFunction(()=>document.body.dataset.screen==='sections');
   assert.equal(await page.locator('#categoryGrid img').count(),0);
   assert.equal(await page.locator('#categoryGrid h2').textContent(),malicious);
   assert.equal(await page.evaluate(()=>window.securityInjected===true),false);
   await page.locator('#categoryGrid button').click();await page.waitForTimeout(400);
   assert.equal(await page.locator('#categoryGrid button').isDisabled(),true);
   await page.waitForTimeout(2100);assert.equal(counts.start,1);
   await page.locator('#categoryGrid button').click();
   await page.waitForFunction(()=>document.body.dataset.screen==='quiz');
   await page.locator('#answerOptions input[type=text]').fill('保留草稿');
   await page.locator('#submitAttempt').click();await page.locator('#modalActions button').first().click();
   await page.waitForTimeout(400);
   assert.equal(await page.locator('#submitAttempt').isDisabled(),true);
   assert.equal(await page.locator('#answerOptions input[type=text]').inputValue(),'保留草稿');
   await page.waitForTimeout(2100);assert.equal(counts.submit,1);
   await page.locator('#submitAttempt').click();await page.locator('#modalActions button').first().click();
   await page.waitForFunction(()=>document.body.dataset.screen==='result');
   assert.equal(counts.unsafe,0);
   results.push({width,counts,passed:true});await context.close();
  }
  console.log(JSON.stringify(results));
 }finally {await browser.close();}
})().catch(e=>{console.error(e.stack);process.exitCode=1});
