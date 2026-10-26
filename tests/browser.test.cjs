const {test,before,after,beforeEach,afterEach}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const path=require('node:path');
const os=require('node:os');
const net=require('node:net');
const {spawn}=require('node:child_process');
const {randomBytes}=require('node:crypto');
const {chromium}=require('playwright');

let directory,server,browser,page,url;
const password=randomBytes(24).toString('hex');
const errors=[],moves=[];
const pause=ms=>new Promise(resolve=>setTimeout(resolve,ms));

before(async()=>{
 directory=await fs.mkdtemp(path.join(os.tmpdir(),'mmm-browser-'));
 for(const name of ['music','inbox','state'])await fs.mkdir(path.join(directory,name));
 for(const name of ['music/Drag Test/Source','music/Drag Test/Target/Nested','inbox/Delete Album'])await fs.mkdir(path.join(directory,name),{recursive:true});
 await fs.writeFile(path.join(directory,'music/Drag Test/Source/Track.wav'),'browser test fixture');
 await fs.writeFile(path.join(directory,'inbox/Delete Album/Track.wav'),'browser test fixture');
 const port=await new Promise(resolve=>{const socket=net.createServer();socket.listen(0,'127.0.0.1',()=>{const port=socket.address().port;socket.close(()=>resolve(port));});});
 url=`http://127.0.0.1:${port}`;
 const python=process.env.MMM_TEST_PYTHON||'python';
 server=spawn(python,['-m','uvicorn','app.main:create_app','--factory','--host','127.0.0.1','--port',String(port)],{
  env:{...process.env,APP_PASSWORD:password,MUSIC_DIR:path.join(directory,'music'),INBOX_DIR:path.join(directory,'inbox'),STATE_DIR:path.join(directory,'state'),SLSKD_API_KEY:'',OUTBOUND_PROXY:''},stdio:['ignore','ignore','pipe']
 });
 let serverLog='';server.stderr.on('data',chunk=>serverLog=(serverLog+chunk).slice(-4000));
 let ready=false;
 for(let i=0;i<100;i++){try{if((await fetch(url+'/api/health')).ok){ready=true;break;}}catch{}await pause(100);}
 assert.ok(ready,'Disposable application failed to start: '+serverLog);
 browser=await chromium.launch({headless:true,args:['--no-sandbox']});
 page=await browser.newPage({viewport:{width:1440,height:1000}});
 page.setDefaultTimeout(10000);
 await page.addInitScript(()=>{
  window.dragEvents=[];
  for(const name of ['dragstart','dragover','dragleave','dragend','drop'])document.addEventListener(name,event=>{
   if(name==='dragover'&&window.dragEvents.at(-1)?.target===event.target.closest('[data-drop-path]')?.dataset.dropPath)return;
   window.dragEvents.push({type:name,target:event.target.closest('[data-drop-path]')?.dataset.dropPath,source:event.target.closest('[data-select]')?.dataset.select});
   if(window.dragEvents.length>80)window.dragEvents.shift();
  });
 });
 page.on('pageerror',error=>errors.push(error.message));
 page.on('request',request=>{if(request.url().endsWith('/api/move')&&request.method()==='POST')moves.push(request.postDataJSON());});
 await page.goto(url);await page.locator('#password').fill(password);await page.locator('#login-form button').click();
 await page.locator('[data-select="Drag Test"]').waitFor();
 await fs.mkdir('artifacts',{recursive:true});
});

beforeEach(async()=>{if(page){await page.keyboard.press('Escape');await page.mouse.up();}});
afterEach(async(context)=>{if(page){await page.screenshot({path:`artifacts/check-${context.name.split(' ')[0]}.png`,fullPage:true,animations:'disabled'});if(context.name.startsWith('native'))console.log('Native drag events:',JSON.stringify(await page.evaluate(()=>window.dragEvents)));await page.keyboard.press('Escape');}});

after(async()=>{
 if(browser)await browser.close();
 if(server){server.kill();await new Promise(resolve=>server.exitCode!==null?resolve():server.once('exit',resolve));}
 if(directory)await fs.rm(directory,{recursive:true,force:true});
});

async function hover(locator){const box=await locator.boundingBox();assert.ok(box);await page.mouse.move(box.x+box.width/2,box.y+box.height/2,{steps:12});}
async function exists(relative){try{await fs.access(path.join(directory,relative));return true;}catch{return false;}}
async function waitForFile(relative){for(let i=0;i<60;i++){if(await exists(relative))return;await pause(100);}assert.fail('Expected file: '+relative);}

test('native drag survives breadcrumb navigation and two hover-open folders',async()=>{
 await page.locator('[data-select="Drag Test"]').dblclick();
 await page.locator('[data-select="Drag Test/Source"]').dblclick();
 const source=page.locator('[data-select="Drag Test/Source/Track.wav"]');
 const box=await source.boundingBox();
 await page.mouse.move(box.x+70,box.y+box.height/2);await page.mouse.down();
 try{
  await page.mouse.move(box.x+95,box.y+box.height/2+8,{steps:8});
  await hover(page.locator('[data-path="Drag Test"]'));
  await page.locator('#drag-file-list [data-select="Drag Test/Target"]').waitFor({timeout:5000});
  assert.equal(await source.evaluate(element=>element.isConnected),true);
  assert.equal(moves.length,0,'Hovering must never move files');
  // Revisiting the source parent is a no-op drop, but must still allow browsing.
  await hover(page.locator('#drag-file-list [data-select="Drag Test/Source"]'));
  await page.locator('[data-path="Drag Test/Source"]').waitFor({timeout:5000});
  await hover(page.locator('[data-path="Drag Test"]'));
  await page.locator('#drag-file-list [data-select="Drag Test/Target"]').waitFor({timeout:5000});
  await hover(page.locator('#drag-file-list [data-select="Drag Test/Target"]'));
  await page.locator('#drag-file-list [data-select="Drag Test/Target/Nested"]').waitFor({timeout:5000});
  await hover(page.locator('#drag-file-list [data-select="Drag Test/Target/Nested"]'));
  await page.locator('[data-path="Drag Test/Target/Nested"]').waitFor({timeout:5000});
  await hover(page.locator('#drag-file-list .empty'));
  await page.mouse.up();
  await waitForFile('music/Drag Test/Target/Nested/Track.wav');
  assert.equal(await exists('music/Drag Test/Source/Track.wav'),false);
  assert.deepEqual(moves[0],{root:'library',source:'Drag Test/Source/Track.wav',destination:'Drag Test/Target/Nested/Track.wav'});
  await page.locator('#file-list [data-select="Drag Test/Target/Nested/Track.wav"]').waitFor();
 }catch(error){
  console.log('Drag failure state:',JSON.stringify(await page.evaluate(()=>({path:state.path,drag:state.drag?.path,hover:state.dragHoverKey,timer:state.dragHoverTimer,layer:document.querySelector('#drag-file-list')?.textContent,toast:document.querySelector('#toast').textContent}))));
  await page.screenshot({path:'artifacts/drag-failure.png',fullPage:true,animations:'disabled'});throw error;
 }finally{await page.keyboard.press('Escape');await page.mouse.up();}
 assert.deepEqual(errors,[]);
});

test('folder dragging and graphical picker perform actual filesystem moves',async()=>{
 await page.locator('[data-path="Drag Test"]').click();
 await page.locator('[data-select="Drag Test/Source"]').dragTo(page.locator('[data-select="Drag Test/Target"]'));
 await waitForFile('music/Drag Test/Target/Source');
 await page.locator('[data-select="Drag Test/Target"]').dblclick();
 await page.locator('[data-select="Drag Test/Target/Source"]').click();
 await page.locator('[data-action="move"]').click();
 await page.locator('.picker-breadcrumbs [data-picker-path="Drag Test"]').click();
 await page.locator('[data-action="confirm-move"]').click();
 await waitForFile('music/Drag Test/Source');
 assert.equal(await exists('music/Drag Test/Target/Source'),false);
 assert.deepEqual(errors,[]);
});

test('permanent deletion confirms the target and deletes nested Inbox contents',async()=>{
 await page.locator('[data-nav="inbox"]').click();await page.locator('[data-select="Delete Album"]').click();
 await page.locator('[data-action="delete"]').click();
 assert.match(await page.locator('#modal').textContent(),/permanently deleted/);
 await page.locator('.modal-footer [data-action="close-modal"]').click();assert.ok(await exists('inbox/Delete Album/Track.wav'));
 await page.locator('[data-action="delete"]').click();await page.locator('[data-action="confirm-delete"]').click();
 await page.locator('#modal').waitFor({state:'hidden'});
 assert.equal(await exists('inbox/Delete Album'),false);
 assert.deepEqual(errors,[]);
});

test('themes cover desktop, mobile and login without overflow or browser errors',async()=>{
 await page.locator('[data-nav="library"]').click();
 for(const theme of ['current','light','dark','gruvbox','mocha']){
  await page.locator('[data-theme-picker]').selectOption(theme);
  await page.screenshot({path:`artifacts/theme-${theme}.png`,fullPage:true,animations:'disabled'});
  const contrast=await page.evaluate(()=>{
   function luminance(value){const rgb=value.match(/[\d.]+/g).slice(0,3).map(Number).map(v=>v/255).map(v=>v<=.04045?v/12.92:((v+.055)/1.055)**2.4);return rgb[0]*.2126+rgb[1]*.7152+rgb[2]*.0722;}
   return ['.folder-card','.primary'].map(selector=>{const style=getComputedStyle(document.querySelector(selector)),a=luminance(style.color),b=luminance(style.backgroundColor);return {selector,ratio:(Math.max(a,b)+.05)/(Math.min(a,b)+.05)};});
  });
  for(const item of contrast)assert.ok(item.ratio>=4.5,`${theme} ${item.selector} contrast: ${item.ratio}`);
  assert.equal(await page.evaluate(()=>document.documentElement.dataset.theme),theme);
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
 }
 await page.reload();await page.locator('[data-select="Drag Test"]').waitFor();
 assert.equal(await page.locator('[data-theme-picker]').inputValue(),'mocha');
 await page.setViewportSize({width:390,height:844});
 await page.screenshot({path:'artifacts/mobile.png',fullPage:true,animations:'disabled'});
 assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
 await page.locator('[data-action="logout"]').click();await page.locator('#login-form').waitFor();
 await page.screenshot({path:'artifacts/login.png',fullPage:true,animations:'disabled'});
 assert.equal(await page.locator('[data-theme-picker]').inputValue(),'mocha');
 assert.deepEqual(errors,[]);
});
