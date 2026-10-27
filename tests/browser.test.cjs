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

beforeEach(async()=>{if(page){await page.keyboard.press('Escape');await page.mouse.up();if(await page.locator('#login-form').count()){await page.locator('#password').fill(password);await page.locator('#login-form button').click();await page.locator('.shell').waitFor();}}});
afterEach(async(context)=>{if(page){await page.screenshot({path:`artifacts/check-${context.name.split(' ')[0]}.png`,fullPage:true,animations:'disabled'});if(context.name.startsWith('native'))console.log('Native drag events:',JSON.stringify(await page.evaluate(()=>window.dragEvents)));await page.keyboard.press('Escape');}});

after(async()=>{
 if(browser)await browser.close();
 if(server){server.kill();await new Promise(resolve=>server.exitCode!==null?resolve():server.once('exit',resolve));}
 if(directory)await fs.rm(directory,{recursive:true,force:true});
});

async function hover(locator){const box=await locator.boundingBox();assert.ok(box);await page.mouse.move(box.x+box.width/2,box.y+box.height/2,{steps:12});await page.mouse.move(box.x+box.width/2+1,box.y+box.height/2+1);}
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
  // Root headings replace the breadcrumb row; a native drag must survive both transitions.
  await hover(page.locator('[data-path=""]'));
  await page.locator('#drag-file-list [data-select="Drag Test"]').waitFor({timeout:5000});
  assert.equal(await page.locator('.browser-toolbar .breadcrumbs').count(),0);
  assert.equal(await source.evaluate(element=>element.isConnected),true);
  await hover(page.locator('#drag-file-list [data-select="Drag Test"]'));
  await page.locator('#drag-file-list [data-select="Drag Test/Target"]').waitFor({timeout:5000});
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
 await page.locator('#file-list .empty').waitFor();
 assert.equal(await page.locator('.browser-toolbar').isVisible(),false);
 assert.equal(await page.locator('#file-filter').count(),0);
 await page.screenshot({path:'artifacts/inbox-toolbar.png',fullPage:true,animations:'disabled'});
 assert.deepEqual(errors,[]);
});

test('themes cover desktop, mobile and login without overflow or browser errors',async()=>{
 await page.locator('[data-nav="settings"]').click();
 for(const theme of ['current','light','dark','gruvbox','mocha']){
  await page.locator('[data-theme-picker]').selectOption(theme);
  await page.locator('[data-nav="library"]').click();
  await page.locator('.folder-card').first().waitFor();
  await page.screenshot({path:`artifacts/theme-${theme}.png`,fullPage:true,animations:'disabled'});
  const contrast=await page.evaluate(()=>{
   function luminance(value){const rgb=value.match(/[\d.]+/g).slice(0,3).map(Number).map(v=>v/255).map(v=>v<=.04045?v/12.92:((v+.055)/1.055)**2.4);return rgb[0]*.2126+rgb[1]*.7152+rgb[2]*.0722;}
   return ['.folder-card','.primary'].map(selector=>{const style=getComputedStyle(document.querySelector(selector)),a=luminance(style.color),b=luminance(style.backgroundColor);return {selector,ratio:(Math.max(a,b)+.05)/(Math.min(a,b)+.05)};});
  });
  for(const item of contrast)assert.ok(item.ratio>=4.5,`${theme} ${item.selector} contrast: ${item.ratio}`);
  assert.equal(await page.evaluate(()=>document.documentElement.dataset.theme),theme);
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
  await page.locator('[data-nav="settings"]').click();
 }
 await page.reload();await page.locator('[data-select="Drag Test"]').waitFor();await page.locator('[data-nav="settings"]').click();
 assert.equal(await page.locator('[data-theme-picker]').inputValue(),'mocha');
 await page.setViewportSize({width:390,height:844});
 await page.screenshot({path:'artifacts/mobile.png',fullPage:true,animations:'disabled'});
 assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
 await page.locator('[data-action="logout"]').click();await page.locator('#login-form').waitFor();
 await page.screenshot({path:'artifacts/login.png',fullPage:true,animations:'disabled'});
 assert.equal(await page.locator('[data-theme-picker]').inputValue(),'mocha');
 assert.deepEqual(errors,[]);
});

test('tree search and downloads support native expansion, branch downloads, completion notices and cleanup',async()=>{
 await page.setViewportSize({width:1440,height:1000});
 const requested=[];
 const remoteFiles=[{filename:'Music\\Test Artist\\Double Album\\CD1\\01 One.flac',size:100},{filename:'Music\\Test Artist\\Double Album\\CD2\\01 Two.flac',size:200}];
 let transfers=[{username:'Fixture Peer',directories:[{directory:'Music',files:remoteFiles.map((file,index)=>({...file,id:`fixture-${index}`,state:'InProgress',bytesTransferred:20,requestedAt:`2026-10-27T10:0${index}:00Z`}))}]}];
 await page.route('**/api/system',route=>route.fulfill({json:{slskd:true,slskd_configured:true}}));
 await page.route('**/api/soulseek/searches',route=>route.fulfill({json:{id:'f3782ee1-5cd2-45d8-936e-57fd18216b21'}}));
 await page.route('**/api/soulseek/searches/*',route=>route.fulfill({json:{search:{isComplete:true},responses:[{username:'Fixture Peer',hasFreeUploadSlot:true,files:remoteFiles}]}}));
 await page.route('**/api/soulseek/downloads',async route=>{
  if(route.request().method()==='POST'){requested.push(route.request().postDataJSON());await route.fulfill({json:{ok:true}});}
  else await route.fulfill({json:transfers});
 });
 await page.route('**/api/soulseek/downloads/completed',route=>{transfers=transfers.map(user=>({...user,directories:user.directories.map(dir=>({...dir,files:dir.files.filter(f=>!f.state.includes('Completed'))}))}));return route.fulfill({json:{ok:true}});});
 try{
  await page.reload();await page.locator('[data-nav="soulseek"]').click();
  assert.equal(await page.locator('.topbar').count(),0);assert.equal(await page.locator('.page-heading #slskd-status').count(),1);
  await page.locator('#soulseek-query').fill('Test Artist Double Album');await page.locator('#soulseek-form button[type="submit"]').click();
  await page.locator('[data-tree-scope="search"]').waitFor();
  // Exercise the native summary toggle instead of setting details.open in the test.
  await page.locator('summary').filter({has:page.locator(':scope > .tree-label',{hasText:'Test Artist'})}).click();
  const album=page.locator('details').filter({has:page.locator(':scope > summary > .tree-label',{hasText:'Double Album'})});
  await album.locator(':scope > summary').click();
  await album.locator(':scope > summary [data-download-tree]').click();
  assert.deepEqual(requested,[{username:'Fixture Peer',files:remoteFiles}]);
  assert.equal(await album.getAttribute('open'),'');
  await page.locator('[data-nav="transfers"]').click();
  await page.locator('#transfer-sort').selectOption('name');
  await page.locator('#transfer-tree details').evaluateAll(nodes=>nodes.forEach(node=>node.open=true));
  await page.screenshot({path:'artifacts/download-tree-desktop.png',fullPage:true,animations:'disabled'});
  await page.evaluate(()=>loadTransfers(true));assert.equal(await page.locator('#transfer-tree details:not([open])').count(),0);
  await page.locator('[data-nav="library"]').click();
  transfers[0].directories[0].files[0]={...transfers[0].directories[0].files[0],state:'Completed, Succeeded',bytesTransferred:100};
  await page.evaluate(()=>loadTransfers(true));await page.locator('.download-notice').waitFor();
  await page.evaluate(()=>loadTransfers(true));assert.equal(await page.locator('.download-notice').count(),1);
  await page.locator('[data-nav="transfers"]').click();await page.locator('[data-action="clear-downloads"]').click();
  await page.waitForFunction(()=>document.querySelectorAll('.transfer-file').length===1);
  assert.match(await page.locator('.transfer-file').textContent(),/Two.flac/);
  await page.setViewportSize({width:390,height:844});
  await page.screenshot({path:'artifacts/download-tree-mobile.png',fullPage:true,animations:'disabled'});
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
 }finally{
  await page.unroute('**/api/system');await page.unroute('**/api/soulseek/searches');await page.unroute('**/api/soulseek/searches/*');await page.unroute('**/api/soulseek/downloads');await page.unroute('**/api/soulseek/downloads/completed');
 }
 assert.deepEqual(errors,[]);
});

test('task rejection and cleanup use real task state and history renders a filtered timeline',async()=>{
 await page.setViewportSize({width:1440,height:1000});
 // Seed only the disposable test application's database, never an archive.
 await new Promise((resolve,reject)=>{
  const child=spawn(process.env.MMM_TEST_PYTHON||'python',['-c',`from pathlib import Path; import time; from app.store import Store; s=Store(Path(${JSON.stringify(path.join(directory,'state'))})); j=s.new_job('preview'); s.update_job(j,'ready',{'artist':'Fixture Artist','album':'Fixture Album','confidence':98,'rows':[],'created':time.time()})`],{stdio:['ignore','ignore','pipe']});
  let log='';child.stderr.on('data',data=>log+=data);child.on('exit',code=>code?reject(new Error(log)):resolve());
 });
 await page.locator('[data-nav="jobs"]').click();await page.locator('[data-show-plan]').click();await page.locator('#modal [data-cancel-job]').click();
 await page.locator('#modal').waitFor({state:'hidden'});assert.match(await page.locator('.job-card').textContent(),/Cancelled/);
 await page.locator('[data-action="clear-jobs"]').click();await page.locator('.jobs-list').waitFor({state:'detached'});
 await page.locator('[data-nav="history"]').click();await page.locator('.history-timeline').first().waitFor();
 await page.locator('#history-filter').selectOption('delete');assert.match(await page.locator('.history-event').first().textContent(),/Deleted/);
 await page.screenshot({path:'artifacts/history-timeline.png',fullPage:true,animations:'disabled'});
 assert.deepEqual(errors,[]);
});

test('system file drop and folder picker upload real files into Library and Inbox without overwriting',async()=>{
 await page.setViewportSize({width:1440,height:1000});
 const localFolder=path.join(directory,'desktop','Local Album');
 await fs.mkdir(path.join(localFolder,'CD1'),{recursive:true});
 await fs.writeFile(path.join(localFolder,'CD1','One.flac'),'original local audio');
 await fs.writeFile(path.join(localFolder,'cover.jpg'),'local cover');
 await page.locator('[data-nav="inbox"]').click();
 await page.locator('#upload-folder-input').setInputFiles(localFolder);
 await waitForFile('inbox/Local Album/CD1/One.flac');await page.locator('#modal').waitFor({state:'hidden'});
 assert.equal(await fs.readFile(path.join(directory,'inbox/Local Album/CD1/One.flac'),'utf8'),'original local audio');
 assert.equal(await fs.readFile(path.join(directory,'inbox/Local Album/cover.jpg'),'utf8'),'local cover');
 await page.locator('[data-nav="library"]').click();
 await page.locator('.browser').waitFor();
 await page.evaluate(()=>{
  const data=new DataTransfer();data.items.add(new File(['dropped audio'],'Dropped.flac',{type:'audio/flac'}));
  document.querySelector('.browser').dispatchEvent(new DragEvent('drop',{bubbles:true,cancelable:true,dataTransfer:data}));
 });
 await waitForFile('music/Dropped.flac');await page.locator('#modal').waitFor({state:'hidden'});
 assert.equal(await fs.readFile(path.join(directory,'music/Dropped.flac'),'utf8'),'dropped audio');
 // A second upload with the same name must fail before any archive data changes.
 await page.locator('#upload-files-input').setInputFiles({name:'Dropped.flac',mimeType:'audio/flac',buffer:Buffer.from('replacement')});
 await page.locator('#modal .error-box').waitFor();assert.match(await page.locator('#modal').textContent(),/Already exists/);
 assert.equal(await fs.readFile(path.join(directory,'music/Dropped.flac'),'utf8'),'dropped audio');
 await page.keyboard.press('Escape');
 await page.screenshot({path:'artifacts/upload-library.png',fullPage:true,animations:'disabled'});
 assert.deepEqual(errors,[]);
});

test('Inbox match badges, Match all task review and cleanup preference survive real page reloads',async()=>{
 await page.locator('[data-nav="settings"]').click();
 await page.locator('#delete-inbox-after-import').check();
 await page.waitForFunction(()=>document.querySelector('#toast').textContent.includes('cleanup enabled'));
 await page.reload();await page.locator('[data-nav="settings"]').click();
 assert.equal(await page.locator('#delete-inbox-after-import').isChecked(),true);
 // A ready match task and its persistent cache are seeded in the disposable DB.
 let jobId;
 await new Promise((resolve,reject)=>{
  const script=`import json, time\nfrom pathlib import Path\nfrom app.config import Settings\nfrom app.store import Store\nfrom app.inbox import snapshot\ns=Settings(library=Path(${JSON.stringify(path.join(directory,'music'))}),inbox=Path(${JSON.stringify(path.join(directory,'inbox'))}),state=Path(${JSON.stringify(path.join(directory,'state'))}),password='browser-test-password')\ndigest,_=snapshot(s,'Local Album')\nstore=Store(s.state)\nmatch={'artist':'Local Artist','album':'Local Album','tracks':1,'has_tags':True,'candidates':[{'id':'4e9b3e98-6f40-4b9d-8765-6fa18dd73512','confidence':99}],'recommended_release_id':'4e9b3e98-6f40-4b9d-8765-6fa18dd73512'}\nstore.save_match('Local Album',digest,match)\nj=store.new_job('match','Local Album')\nstore.update_job(j,'ready',{'source':'Local Album','match':match,'plan':{'artist':'Local Artist','album':'Local Album','confidence':99,'rows':[],'created':time.time(),'source':'Local Album','delete_source':True}})\nprint(j)`;
  const child=spawn(process.env.MMM_TEST_PYTHON||'python',['-c',script],{stdio:['ignore','pipe','pipe']});
  let log='',out='';child.stderr.on('data',data=>log+=data);child.stdout.on('data',data=>out+=data);child.on('exit',code=>{if(code)reject(new Error(log));else{jobId=out.trim();resolve();}});
 });
 await page.locator('[data-nav="inbox"]').click();
 assert.match(await page.locator('[data-select="Local Album"]').textContent(),/Matched/);
 await page.route('**/api/musicbrainz/match-all',route=>route.fulfill({json:{queued:1,skipped_imported:0,skipped_matched:0,active:0,jobs:[{id:jobId}]}}));
 try{
  await page.locator('[data-action="match-all"]').click();await page.locator(`[data-show-plan="${jobId}"]`).click();
  assert.match(await page.locator('.cleanup-warning').textContent(),/Inbox\/Local Album.*everything inside/);
  assert.match(await page.locator('[data-apply-plan]').textContent(),/Import & delete Inbox folder/);
  await page.locator('#modal [data-cancel-job]').click();await page.locator('#modal').waitFor({state:'hidden'});
  assert.ok(await exists('inbox/Local Album/CD1/One.flac'));
 }finally{await page.unroute('**/api/musicbrainz/match-all');}
 await page.locator('[data-nav="inbox"]').click();
 await page.screenshot({path:'artifacts/inbox-matched.png',fullPage:true,animations:'disabled'});
 await page.setViewportSize({width:390,height:844});
 assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
 await page.screenshot({path:'artifacts/inbox-matched-mobile.png',fullPage:true,animations:'disabled'});
 assert.deepEqual(errors,[]);
});

test('manual Add to Library copies an unmatched album and artwork through the folder picker without MusicBrainz',async()=>{
 await page.setViewportSize({width:1440,height:1000});
 const lookups=[];
 const observe=request=>{if(request.url().includes('/api/musicbrainz/'))lookups.push(request.url());};page.on('request',observe);
 try{
  await page.locator('[data-nav="inbox"]').click();await page.locator('[data-select="Local Album"]').click();await page.locator('[data-action="add-to-library"]').click();
  await page.locator('.picker-create > summary').click();
  await page.locator('#picker-new-folder input').fill('Offline Artist');await page.locator('#picker-new-folder button').click();
  await page.locator('.picker-breadcrumbs').filter({hasText:'Offline Artist'}).waitFor();
  await page.locator('#manual-album-name').fill('Offline Album');await page.locator('[data-action="confirm-manual"]').click();
  await page.locator('[data-apply-plan]').waitFor();
  assert.match(await page.locator('.plan-banner').textContent(),/kept as-is.*No MusicBrainz/);
  await page.screenshot({path:'artifacts/manual-import-preview.png',fullPage:true,animations:'disabled'});
  await page.locator('[data-apply-plan]').click();await waitForFile('music/Offline Artist/Offline Album/CD1/One.flac');await page.locator('#modal').waitFor({state:'hidden'});
  assert.equal(await fs.readFile(path.join(directory,'music/Offline Artist/Offline Album/CD1/One.flac'),'utf8'),'original local audio');
  assert.equal(await fs.readFile(path.join(directory,'music/Offline Artist/Offline Album/cover.jpg'),'utf8'),'local cover');
  assert.equal(await exists('inbox/Local Album'),false,'Reviewed cleanup option should remove the Inbox source');
  assert.equal(lookups.length,0);
 }finally{page.off('request',observe);}
 assert.deepEqual(errors,[]);
});

test('Library total time reads actual audio and stays compact on desktop and mobile',async()=>{
 await page.setViewportSize({width:1440,height:1000});
 await new Promise((resolve,reject)=>{
  const script=`from pathlib import Path\nimport wave\nfrom mediafile import MediaFile\nroot=Path(${JSON.stringify(path.join(directory,'music','Duration Fixture'))})\nfor artist,album,seconds in [('Aurora','First Light',40),('Aurora','After Hours',20),('Velvet','Blue Room',30)]:\n p=root/artist/album/'01.wav'\n p.parent.mkdir(parents=True,exist_ok=True)\n with wave.open(str(p),'wb') as w:\n  w.setnchannels(1)\n  w.setsampwidth(2)\n  w.setframerate(8000)\n  w.writeframes(b'\\0\\0'*8000*seconds)\n m=MediaFile(p)\n m.artist=m.albumartist=artist\n m.album=album\n m.save()\n`;
  const child=spawn(process.env.MMM_TEST_PYTHON||'python',['-c',script],{stdio:['ignore','ignore','pipe']});let log='';child.stderr.on('data',data=>log+=data);child.on('exit',code=>code?reject(new Error(log)):resolve());
 });
 await page.locator('[data-nav="library"]').click();
 await page.locator('#library-time-value').waitFor();
 await page.evaluate(()=>loadLibraryTime(true));
 await page.waitForFunction(()=>document.querySelector('#library-time-value')?.textContent==='1 min 30 sec'&&!state.libraryStats?.scanning);
 assert.equal(await page.locator('.page-heading h1').textContent(),'Library (1 min 30 sec)');
 assert.equal(await page.locator('.library-time').count(),0);
 assert.equal(await page.locator('.browser-toolbar .breadcrumbs').count(),0);
 assert.equal(await page.locator('.file-toolbar #file-filter').count(),1);
 assert.equal(await page.locator('#library-map,.map-chart,#map-filter').count(),0);
 for(const theme of ['current','light','dark','gruvbox','mocha']){
  await page.evaluate(value=>applyMusicTheme(value),theme);
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
 }
 await page.screenshot({path:'artifacts/library-time-desktop.png',fullPage:true,animations:'disabled'});
 await page.setViewportSize({width:390,height:844});
 await page.screenshot({path:'artifacts/library-time-mobile.png',fullPage:true,animations:'disabled'});
 assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
 await page.locator('[data-action="reload"]').click();
 await page.waitForFunction(()=>document.querySelector('#library-time-value')?.textContent==='1 min 30 sec');
 assert.deepEqual(errors,[]);
});

test('recursive Library search finds remote folders, renames in place and moves actual nested results',async()=>{
 await page.setViewportSize({width:1440,height:1000});
 const original='Lookup Artist/Record/CD2/Nested Search.flac';
 for(const name of [original,'Elsewhere/Nested Search.flac']){
  await fs.mkdir(path.dirname(path.join(directory,'music',name)),{recursive:true});
  await fs.writeFile(path.join(directory,'music',name),'search fixture audio');
 }
 await fs.mkdir(path.join(directory,'music','Lookup Artist','Search Album'),{recursive:true});
 await page.locator('[data-nav="inbox"]').click();assert.equal(await page.locator('#file-filter').count(),0);
 await page.locator('[data-nav="library"]').click();await page.locator('#file-filter').fill('Nested Search');
 await page.locator(`[data-select="${original}"]`).waitFor();
 await page.waitForFunction(()=>document.querySelectorAll('.file-row').length===2);
 assert.match(await page.locator(`[data-select="${original}"] .result-path`).textContent(),/Lookup Artist\/Record\/CD2/);
 await page.locator(`[data-select="${original}"]`).click();await page.locator('[data-action="rename"]').click();
 await page.locator('#file-value').fill('Renamed Search.flac');await page.locator('#modal button[form="file-operation"]').click();
 await waitForFile('music/Lookup Artist/Record/CD2/Renamed Search.flac');
 assert.equal(await exists('music/Renamed Search.flac'),false,'Renaming a result must keep its original parent');
 await page.locator(`[data-select="${original}"]`).waitFor({state:'detached'});
 await page.locator('[data-select="Elsewhere/Nested Search.flac"]').click();
 await page.locator('[data-action="open-containing-folder"]').click();await page.locator('[data-path="Elsewhere"]').waitFor();
 assert.equal(await page.locator('#file-filter').inputValue(),'');
 await page.locator('#file-filter').fill('Search Album');await page.locator('[data-select="Lookup Artist/Search Album"]').dblclick();
 await page.locator('[data-path="Lookup Artist/Search Album"]').waitFor();
 assert.equal(await page.locator('#file-filter').inputValue(),'');
 await page.locator('#file-filter').fill('Search');
 await page.locator('[data-select="Lookup Artist/Record/CD2/Renamed Search.flac"]').waitFor();
 await page.screenshot({path:'artifacts/library-recursive-search.png',fullPage:true,animations:'disabled'});
 await page.setViewportSize({width:390,height:844});
 assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
 await page.screenshot({path:'artifacts/library-recursive-search-mobile.png',fullPage:true,animations:'disabled'});
 await page.setViewportSize({width:1440,height:1000});
 await page.locator('[data-select="Lookup Artist/Record/CD2/Renamed Search.flac"]').dragTo(page.locator('[data-select="Lookup Artist/Search Album"]'));
 await waitForFile('music/Lookup Artist/Search Album/Renamed Search.flac');
 assert.equal(await exists('music/Lookup Artist/Record/CD2/Renamed Search.flac'),false);
 assert.deepEqual(errors,[]);
});
