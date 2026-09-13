const {chromium}=require(process.env.PLAYWRIGHT_MODULE_PATH || 'playwright');
const fs=require('node:fs');
const path=require('node:path');
const assert=require('node:assert/strict');
const base=process.env.QUIZ_TEST_URL;
if(!base || !base.includes(':28444')) throw new Error('Requires the dedicated isolated lab tunnel.');
(async()=>{
 const browser=await chromium.launch({channel:'chrome',headless:true,args:['--no-proxy-server','--host-resolver-rules=MAP test.ustc-nihongo-circle.top 127.0.0.1']});
 const sessions=[];const failures=[];let images=0;
 const output='output/playwright/security-hardening';fs.mkdirSync(output,{recursive:true});
 try {
  for(let i=0;i<50;i++){
   const mobile=i%2===1;
   const context=await browser.newContext({viewport:mobile?{width:390,height:844}:{width:1440,height:900},isMobile:mobile,reducedMotion:'reduce'});
   const page=await context.newPage();
   page.on('pageerror',e=>failures.push({index:i,error:e.message}));
   page.on('response',r=>{if(r.status()===429||r.status()>=500)failures.push({index:i,status:r.status()});});
   sessions.push({context,page,index:i});
  }
  await Promise.all(sessions.map(async({page,index})=>{
   await page.goto(base+'/participant/',{waitUntil:'networkidle',timeout:60000});
   await page.waitForFunction(()=>window.__prototypeReady===true);
   await page.locator('[name=display_name]').fill('合成并发验收');
   await page.locator('[name=identifier]').fill('BROWSER-SECURITY-'+index);
   await page.locator('[name=contact_type][value=email]').check();
   await page.locator('[name=contact]').fill(`browser${index}@example.com`);
  }));
  await Promise.all(sessions.map(async({page})=>{
   await page.locator('#registrationSubmit').click();
   await page.waitForFunction(()=>document.body.dataset.screen==='sections',null,{timeout:60000});
  }));
  console.log('50 independent browser registrations passed');
  await Promise.all(sessions.map(async({page})=>{
   await page.locator('#categoryGrid button').first().click();
   await page.waitForFunction(()=>document.body.dataset.screen==='quiz',null,{timeout:60000});
   const decoded=await page.evaluate(async()=>{
    const image=document.querySelector('#questionImage');
    if(!image || !image.src)return false;
    await image.decode();return image.naturalWidth>0;
   });
   assert.ok(decoded);images++;
  }));
  for(const {page,index} of sessions.slice(0,2))await page.screenshot({path:path.join(output,`synthetic-quiz-${index}.png`),fullPage:true});
  await Promise.all(sessions.map(async({page})=>{
   await page.locator('#answerOptions label').first().click();
   await page.locator('#submitAttempt').click();
   await page.locator('#modalActions button').first().click();
   await page.waitForFunction(()=>document.body.dataset.screen==='result',null,{timeout:60000});
  }));
  assert.deepEqual(failures,[]);
  const result={sessions:50,registered:50,started:50,submitted:50,decoded_question_images:images,failures};
  fs.writeFileSync(path.join(output,'fifty-sessions.json'),JSON.stringify(result,null,2));
  console.log(JSON.stringify(result));
 }finally{await browser.close();}
})().catch(e=>{console.error(e.stack);process.exitCode=1;});
