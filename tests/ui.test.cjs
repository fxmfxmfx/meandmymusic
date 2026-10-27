const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {JSDOM, VirtualConsole} = require('jsdom');

const release = '4e9b3e98-6f40-4b9d-8765-6fa18dd73512';
const search = 'f3782ee1-5cd2-45d8-936e-57fd18216b21';
const planId = 'e16f8b08-a748-4f7a-b267-feb1bdc7c292';
const html = fs.readFileSync('app/static/index.html', 'utf8');
const code = fs.readFileSync('app/static/app.js', 'utf8');
const folder = (name) => ({name, path:name, directory:true, modified:1700000000, size:null});
const file = (name) => ({name, path:name, directory:false, audio:true, modified:1700000000, size:12345});
const tick = () => new Promise(resolve => setTimeout(resolve, 20));

async function fixture(authenticated=true, authFailure=null, matchOverrides={}, scenario={}) {
  const calls = [], errors = [];
  const console = new VirtualConsole();
  console.on('jsdomError', error => errors.push(error.message));
  const dom = new JSDOM(html, {url:'http://localhost:8080', runScripts:'outside-only', virtualConsole:console});
  const window = dom.window;
  window.HTMLDialogElement.prototype.showModal = function(){this.open=true;};
  window.HTMLDialogElement.prototype.close = function(){this.open=false;};
  const plan = {id:planId, kind:'preview', status:'ready', created:1700000000, result:{
    artist:'Test Artist', album:'Test Album', confidence:98, rows:[{source:'Album/01.wav', destination:'Test Artist/2026 - Test Album/1-01 Track.wav',
      before:{title:'Old title', artist:'Unknown'}, after:{title:'Correct title', artist:'Test Artist',album:'Test Album',year:2026,disc:1,track:1,tracktotal:1}}]}};
  let transfers=scenario.transfers||[], jobs=[plan], events=scenario.events||[], preferences={delete_inbox_after_import:Boolean(scenario.cleanup)};
  if(scenario.cleanup){plan.result.delete_source=true;plan.result.source='Album';}
  if(scenario.manual){plan.result.mode='manual';plan.result.source='Artist <script>';plan.result.destination='Second Artist/Offline Album';}
  if(scenario.matchJob){plan.kind='match';plan.source='Artist <script>';plan.result={source:plan.source,plan:plan.result};}
  let entries = scenario.entries||[folder('Artist <script>'), folder('Second Artist'), file('Song.wav')];
  let account = {managed:true,api_reachable:true,logged_in:false,saved:false,busy:false,error:null};
  window.fetch = async (url, options={}) => {
    const path = new URL(url, window.location.origin);
    const method = options.method || 'GET';
    const body = options.body ? JSON.parse(options.body) : null;
    calls.push({path:path.pathname, params:path.searchParams, method, body});
    let result = {}, status = 200;
    if(path.pathname==='/api/session') {result={authenticated};if(!authenticated)status=401;}
    else if(path.pathname==='/api/login') {authenticated=true;result={ok:true};}
    else if(path.pathname==='/api/preferences'){if(method==='PATCH')preferences=body;result=preferences;}
    else if(path.pathname==='/api/musicbrainz/match-all')result={queued:1,jobs:[{id:planId}],skipped_imported:1,skipped_matched:1,active:0};
    else if(path.pathname==='/api/system') result={slskd:true,beets:true,navidrome_url:''};
    else if(path.pathname==='/api/library/stats'||path.pathname==='/api/library/stats/refresh') result=scenario.stats||{ready:true,scanning:false,duration:0,tracks:0,artists:[],albums:0,skipped:0};
    else if(path.pathname==='/api/soulseek/auth/status') result=account;
    else if(path.pathname==='/api/soulseek/auth/login'||path.pathname==='/api/soulseek/auth/register') {if(authFailure){status=400;account={...account,error:authFailure.message,failure:authFailure};result={detail:authFailure.message,error:authFailure};}else{account={...account,logged_in:true,username:body.username,saved:body.remember};result={...account,message:'Signed in'};}}
    else if(path.pathname==='/api/soulseek/auth/logout') {account={...account,logged_in:false,username:null,saved:false};result=account;}
    else if(path.pathname==='/api/files'&&method==='DELETE') {entries=entries.filter(entry=>entry.path!==body.path);result={ok:true};}
    else if(path.pathname==='/api/files') result={path:path.searchParams.get('path')||'.',entries:path.searchParams.get('path')?[]:entries};
    else if(path.pathname==='/api/metadata') result={title:'Song',artist:'Test Artist',duration:61,bitrate:320};
    else if(path.pathname==='/api/folders') {entries.push(folder(body.path));result={ok:true};}
    else if(path.pathname==='/api/move') {entries=entries.map(entry=>entry.path===body.source?{...entry,path:body.destination,name:body.destination}:entry);result={ok:true};}
    else if(path.pathname==='/api/soulseek/searches'&&method==='POST') result={id:search};
    else if(path.pathname===`/api/soulseek/searches/${search}`) result={search:{id:search,isComplete:true},responses:scenario.responses||[{username:'Peer',hasFreeUploadSlot:true,uploadSpeed:1024,files:[{filename:'Music\\Album\\01.flac',size:123456,bitRate:900}]}]};
    else if(path.pathname==='/api/soulseek/downloads/completed'){if(scenario.clearFailure){status=502;result={detail:'slskd unavailable'};}else{transfers=transfers.map(u=>({...u,directories:u.directories.map(d=>({...d,files:d.files.filter(f=>!String(f.state).includes('Completed'))}))}));result={ok:true};}}
    else if(path.pathname==='/api/soulseek/downloads') result=method==='POST'?{ok:true}:transfers;
    else if(path.pathname==='/api/jobs'){if(method==='DELETE'){const finished=jobs.filter(j=>!['queued','running','ready','applying'].includes(j.status));jobs=jobs.filter(j=>!finished.includes(j));result={cleared:finished.length};}else result=jobs;}
    else if(path.pathname===`/api/jobs/${planId}/cancel`){if(scenario.cancelFailure){status=409;result={detail:'Task already applying'};}else{plan.status='cancelled';plan.result=null;result={ok:true};}}
    else if(path.pathname==='/api/musicbrainz/match') result={artist:body.artist??'Tagged Artist',album:body.album??'Tagged Album',tracks:2,has_tags:true,candidates:[{id:release,artist:'Tagged Artist',title:'Tagged Album',tracks:2,confidence:98,extra_files:0,missing_tracks:0}],...matchOverrides};
    else if(path.pathname==='/api/imports/manual-preview')result={id:planId};
    else if(path.pathname==='/api/imports/preview') result={id:planId};
    else if(path.pathname===`/api/jobs/${planId}`) result=plan;
    else if(path.pathname===`/api/imports/${planId}/apply`) result={id:'import-job'};
    else if(path.pathname==='/api/jobs/import-job') result={status:'done',result:{files:1}};
    else if(path.pathname==='/api/history') result=events;
    else if(path.pathname==='/api/logout') authenticated=false;
    else {status=404;result={detail:'Unmocked route: '+path.pathname};}
    return {ok:status<400,status,json:async()=>result};
  };
  window.eval(fs.readFileSync('app/static/theme.js','utf8'));
  window.eval(code+'\nwindow.testPollTransfers=loadTransfers;window.testPollSearch=pollSearch;window.testReadDropped=readDropped;');
  await tick();
  const click = selector => {
    const target=window.document.querySelector(selector);
    assert.ok(target,`Element exists: ${selector}`);
    target.click();
    return target;
  };
  const submit = selector => window.document.querySelector(selector).dispatchEvent(new window.Event('submit',{bubbles:true,cancelable:true}));
  return {dom,window,calls,errors,click,submit,setTransfers(value){transfers=value;},setJobs(value){jobs=value;},async poll(){await window.testPollTransfers(true);await tick();},close(){dom.window.close();}};
}

test('login opens the archive, escaped filenames render as text, filter works', async () => {
  const f=await fixture(false);
  try {
    assert.ok(f.window.document.querySelector('#login-form'));
    f.window.document.querySelector('#password').value='archive-password';f.submit('#login-form');await tick();
    assert.match(f.window.document.querySelector('h1').textContent,/Library/);
    assert.equal(f.window.document.querySelectorAll('.folder-card').length,2);
    assert.ok(f.window.document.querySelector('.folder-name').textContent.includes('<script>'));
    const filter=f.window.document.querySelector('#file-filter');filter.value='Second';filter.dispatchEvent(new f.window.Event('input',{bubbles:true}));
    assert.equal(f.window.document.querySelectorAll('.folder-card').length,1);
    assert.equal(f.window.document.querySelector('.folder-name').textContent,'Second Artist');
    assert.deepEqual(f.errors,[]);
  } finally {f.close();}
});

test('selection preserves DOM for double-click, folder creation and rename call server API', async () => {
  const f=await fixture();
  try {
    const card=f.click('.folder-card');
    assert.ok(card.isConnected,'Selection must not replace the clicked card');
    assert.ok(f.window.document.querySelector('[data-action="rename"]'));
    f.click('[data-action="rename"]');f.window.document.querySelector('#file-value').value='Renamed';f.submit('#file-operation');await tick();
    assert.ok(f.calls.some(c=>c.path==='/api/move'&&c.body.source==='Artist <script>'&&c.body.destination==='Renamed'));
    f.click('[data-action="new-folder"]');f.window.document.querySelector('#file-value').value='New Folder';f.submit('#file-operation');await tick();
    assert.ok(f.calls.some(c=>c.path==='/api/folders'&&c.body.path==='New Folder'));
    const renamed=[...f.window.document.querySelectorAll('.folder-card')].find(c=>c.textContent.includes('Renamed'));
    renamed.dispatchEvent(new f.window.MouseEvent('dblclick',{bubbles:true}));await tick();
    assert.ok(f.calls.some(c=>c.path==='/api/files'&&c.params.get('path')==='Renamed'));
    assert.deepEqual(f.errors,[]);
  } finally {f.close();}
});

test('Soulseek results group into folders and download sends exact filenames and sizes', async () => {
  const f=await fixture();
  try {
    f.click('[data-nav="soulseek"]');await tick();
    f.window.document.querySelector('#soulseek-query').value='Test Artist Album';f.submit('#soulseek-form');await tick();
    assert.equal(f.window.document.querySelectorAll('[data-tree-scope="search"] > details').length,1);
    const branch=[...f.window.document.querySelectorAll('[data-download-tree]')].find(b=>b.getAttribute('aria-label')==='Download folder Album');branch.click();await tick();
    const call=f.calls.find(c=>c.path==='/api/soulseek/downloads'&&c.method==='POST');
    assert.deepEqual(call.body,{username:'Peer',files:[{filename:'Music\\Album\\01.flac',size:123456}]});
    assert.match(f.window.document.querySelector('#toast').textContent,/files queued/);
    assert.deepEqual(f.errors,[]);
  } finally {f.close();}
});

test('saved preview shows exact destination and apply follows import job to completion', async () => {
  const f=await fixture();
  try {
    f.click('[data-nav="jobs"]');await tick();f.click(`[data-show-plan="${planId}"]`);await tick();
    assert.ok(f.window.document.querySelector('#modal').open);
    assert.match(f.window.document.querySelector('.plan-track code').textContent,/Test Artist\/2026 - Test Album/);
    assert.match(f.window.document.querySelector('.tag-diff').textContent,/Correct title/);
    f.click('[data-apply-plan]');await tick();
    assert.ok(f.calls.some(c=>c.path===`/api/imports/${planId}/apply`&&c.method==='POST'));
    assert.match(f.window.document.querySelector('#toast').textContent,/Original files remain/);
    assert.equal(f.window.document.querySelector('#modal').open,false);
    assert.deepEqual(f.errors,[]);
  } finally {f.close();}
});

test('Soulseek registration confirms passwords and displays only verified server login', async () => {
  const f=await fixture();
  try {
    f.click('[data-nav="settings"]');await tick();
    assert.match(f.window.document.querySelector('.account-status').textContent,/Not signed in/);
    f.click('[data-action="soulseek-auth-register"]');
    f.window.document.querySelector('#soulseek-username').value='New User';
    f.window.document.querySelector('#soulseek-password').value='private-password';
    f.window.document.querySelector('#soulseek-confirm').value='different';
    f.submit('#soulseek-account-form');await tick();
    assert.match(f.window.document.querySelector('#soulseek-account-error').textContent,/do not match/);
    assert.ok(!f.calls.some(c=>c.path==='/api/soulseek/auth/register'));
    f.window.document.querySelector('#soulseek-confirm').value='private-password';
    f.submit('#soulseek-account-form');await tick();
    const call=f.calls.find(c=>c.path==='/api/soulseek/auth/register');
    assert.equal(call.body.username,'New User');assert.equal(call.body.remember,true);
    assert.match(f.window.document.querySelector('.account-status').textContent,/Signed in/);
    assert.ok(!f.window.document.body.innerHTML.includes('private-password'));
    f.click('[data-action="soulseek-logout"]');await tick();
    assert.match(f.window.document.querySelector('.account-status').textContent,/Not signed in/);
    assert.deepEqual(f.errors,[]);
  } finally {f.close();}
});

test('Soulseek failure displays code and hint, clears password and preserves manager session', async () => {
  const f=await fixture(true,{code:'INVALID_PASSWORD',stage:'login',message:'Soulseek rejected the password.',hint:'Check the password or choose an available name.'});
  try {
    f.click('[data-nav="settings"]');await tick();
    f.window.document.querySelector('#soulseek-username').value='User';
    f.window.document.querySelector('#soulseek-password').value='do-not-display-this';
    f.submit('#soulseek-account-form');await tick();
    assert.match(f.window.document.querySelector('.account-panel .error-box').textContent,/INVALID_PASSWORD/);
    assert.match(f.window.document.querySelector('.account-panel .error-box').textContent,/available name/);
    assert.ok(f.window.document.querySelector('#soulseek-account-form'));
    assert.ok(!f.window.document.querySelector('#login-form'));
    assert.equal(f.window.document.querySelector('#soulseek-password').value,'');
    assert.ok(!f.window.document.body.innerHTML.includes('do-not-display-this'));
    assert.deepEqual(f.errors,[]);
  } finally {f.close();}
});

test('delete requires confirmation and works in Library and Inbox', async () => {
  for(const root of ['library','inbox']){
    const f=await fixture();
    try{
      if(root==='inbox'){f.click('[data-nav="inbox"]');await tick();}
      assert.equal(f.window.document.querySelector('[data-action="delete"]').disabled,true);
      f.click('.folder-card');f.click('[data-action="delete"]');
      assert.match(f.window.document.querySelector('#modal').textContent,/permanently deleted/);
      assert.ok(!f.calls.some(c=>c.method==='DELETE'));
      f.click('[data-action="close-modal"]');assert.ok(!f.calls.some(c=>c.method==='DELETE'));
      f.click('[data-action="delete"]');f.click('[data-action="confirm-delete"]');await tick();
      assert.ok(f.calls.some(c=>c.method==='DELETE'&&c.path==='/api/files'&&c.body.root===root&&c.body.path==='Artist <script>'));
      assert.equal(f.window.document.querySelectorAll('.folder-card').length,1);
      assert.deepEqual(f.errors,[]);
    }finally{f.close();}
  }
});

test('graphical move picker navigates folders without typing a path', async () => {
  const f=await fixture();
  try{
    f.click('.folder-card');f.click('[data-action="move"]');await tick();
    assert.ok(!f.window.document.querySelector('#file-value'));
    f.click('[data-picker-path="Second Artist"]');await tick();
    f.click('[data-action="confirm-move"]');await tick();
    assert.ok(f.calls.some(c=>c.path==='/api/move'&&c.body.source==='Artist <script>'&&c.body.destination==='Second Artist/Artist <script>'));
    assert.deepEqual(f.errors,[]);
  }finally{f.close();}
});

test('all five themes persist across initialization and contain no promotional panels', async () => {
  const f=await fixture();
  try{
    assert.ok(!f.window.document.querySelector('.topbar,header'));
    assert.ok(!f.window.document.querySelector('[data-theme-picker],[data-action="logout"]'));
    f.click('[data-nav="settings"]');await tick();
    const select=f.window.document.querySelector('[data-theme-picker]');
    assert.equal(select.options.length,5);
    for(const id of ['current','light','dark','gruvbox','mocha']){
      select.value=id;select.dispatchEvent(new f.window.Event('change',{bubbles:true}));
      assert.equal(f.window.localStorage.getItem('mmm-theme'),id);
      f.window.eval(fs.readFileSync('app/static/theme.js','utf8'));
      assert.equal(f.window.document.documentElement.dataset.theme,id);
    }
    assert.ok(!f.window.document.querySelector('.hero,.own-your-music,.login-story'));
    assert.equal(f.window.document.documentElement.lang,'en');
    assert.deepEqual(f.errors,[]);
  }finally{f.close();}
});

test('matching starts with metadata and accepts explicit artist and album corrections', async () => {
  const f=await fixture();
  try{
    f.click('[data-nav="inbox"]');await tick();f.click('.folder-card');f.click('[data-action="match"]');await tick();
    assert.ok(f.calls.some(c=>c.path==='/api/musicbrainz/match'&&c.body.path==='Artist <script>'&&!('artist' in c.body)));
    assert.equal(f.window.document.querySelector('#match-artist').value,'Tagged Artist');
    assert.equal(f.window.document.querySelector('#match-album').value,'Tagged Album');
    assert.match(f.window.document.querySelector('.match-score').textContent,/98%/);
    f.window.document.querySelector('#match-artist').value='Corrected Artist';f.submit('#release-form');await tick();
    assert.ok(f.calls.some(c=>c.path==='/api/musicbrainz/match'&&c.body.artist==='Corrected Artist'&&c.body.album==='Tagged Album'));
    assert.ok(!f.calls.some(c=>c.path==='/api/musicbrainz/releases'));
    assert.deepEqual(f.errors,[]);
  }finally{f.close();}
});

test('strong matching opens a preview automatically without applying it', async () => {
  const f=await fixture(true,null,{recommended_release_id:release});
  try{
    f.click('[data-nav="inbox"]');await tick();f.click('.folder-card');f.click('[data-action="match"]');await tick();await tick();
    assert.ok(f.calls.some(c=>c.path==='/api/imports/preview'&&c.body.release_id===release));
    assert.ok(f.window.document.querySelector('.plan-track'));
    assert.ok(!f.calls.some(c=>c.path.endsWith('/apply')));
    assert.deepEqual(f.errors,[]);
  }finally{f.close();}
});

test('weak matching remains suggestions and never opens an automatic preview', async () => {
  const f=await fixture(true,null,{candidates:[{id:release,artist:'Tagged Artist',title:'Wrong Album',tracks:2,confidence:41,extra_files:0,missing_tracks:0}]});
  try{
    f.click('[data-nav="inbox"]');await tick();f.click('.folder-card');f.click('[data-action="match"]');await tick();
    assert.match(f.window.document.querySelector('#release-results .warning').textContent,/weak suggestions/);
    assert.ok(!f.calls.some(c=>c.path==='/api/imports/preview'));
    assert.deepEqual(f.errors,[]);
  }finally{f.close();}
});

test('hover navigation retains the drag source and drop uses the original file path', async () => {
  const f=await fixture();
  try{
    const transfer={setData(){},effectAllowed:'',dropEffect:''};
    const drag=(element,type)=>{const event=new f.window.Event(type,{bubbles:true,cancelable:true});Object.defineProperty(event,'dataTransfer',{value:transfer});element.dispatchEvent(event);};
    const source=f.window.document.querySelector('.file-row');drag(source,'dragstart');
    const target=f.window.document.querySelector('[data-drop-path="Second Artist"]');drag(target,'dragover');
    await new Promise(resolve=>setTimeout(resolve,710));
    assert.ok(source.isConnected,'Native drag source must remain connected through navigation');
    assert.ok(f.calls.some(c=>c.path==='/api/files'&&c.params.get('path')==='Second Artist'));
    drag(f.window.document.querySelector('.browser'),'drop');await tick();
    assert.ok(f.calls.some(c=>c.path==='/api/move'&&c.body.source==='Song.wav'&&c.body.destination==='Second Artist/Song.wav'));
    assert.ok(!f.window.document.body.classList.contains('is-dragging'));
    assert.deepEqual(f.errors,[]);
  }finally{f.close();}
});

const transferFile=(id,name,state='InProgress',extra={})=>({id,filename:`Music\\Album\\${name}`,size:100,bytesTransferred:50,state,requestedAt:'2026-10-27T10:00:00Z',...extra});
const transferData=files=>[{username:'Peer',directories:[{directory:'Music\\Album',files}]}];

test('search tree keeps peers separate and downloads a parent branch with nested discs',async()=>{
 const files=[{filename:'Music\\Artist <b>\\Album\\CD1\\01.flac',size:10},{filename:'Music\\Artist <b>\\Album\\CD2\\02.flac',size:20}];
 const f=await fixture(true,null,{}, {responses:[{username:'Peer "A"',files,hasFreeUploadSlot:true},{username:'Peer B',files:[files[0]]}]});
 try{
  f.click('[data-nav="soulseek"]');await tick();
  assert.ok(f.window.document.querySelector('.page-heading #slskd-status'));
  f.window.document.querySelector('#soulseek-query').value='Artist Album';f.submit('#soulseek-form');await tick();
  assert.equal(f.window.document.querySelectorAll('[data-tree-scope="search"] > details').length,2);
  assert.equal(f.window.document.querySelectorAll('.tree-file').length,3);
  assert.ok(!f.window.document.querySelector('#search-results b'));
  const albumButton=[...f.window.document.querySelectorAll('[data-download-tree]')].find(b=>b.getAttribute('aria-label')==='Download folder Album');
  const albumBranch=albumButton.closest('details');albumBranch.open=true;const key=albumBranch.dataset.treeKey;
  albumButton.click();await tick();
  const call=f.calls.find(c=>c.path==='/api/soulseek/downloads'&&c.method==='POST');
  assert.deepEqual(call.body,{username:'Peer "A"',files});assert.equal(albumBranch.open,true);
  await f.window.testPollSearch();
  assert.equal([...f.window.document.querySelectorAll('details')].find(el=>el.dataset.treeKey===key).open,true);
  f.setTransfers([{username:'Peer "A"',directories:[{files:files.map((file,index)=>({...file,id:index,state:'Completed, Succeeded',bytesTransferred:file.size}))}]}]);await f.poll();
  assert.match(f.window.document.querySelector('#download-notices').textContent,/Folder downloaded: Album · 2 files/);
  assert.equal(f.window.document.querySelectorAll('.download-notice').length,1);
  f.click('[data-nav="library"]');await tick();assert.ok(!f.window.document.querySelector('#slskd-status'));
  assert.deepEqual(f.errors,[]);
 }finally{f.close();}
});

test('download tree preserves expanded branches, sorts files and filters without lying about folder completion',async()=>{
 const f=await fixture(true,null,{}, {transfers:transferData([transferFile('a','Z.flac'),transferFile('b','A.flac','Completed, Succeeded',{bytesTransferred:100,size:200})])});
 try{
  f.click('[data-nav="transfers"]');await tick();
  const branch=[...f.window.document.querySelectorAll('details')].find(el=>el.querySelector('summary > .tree-label').textContent==='Album');branch.open=true;const key=branch.dataset.treeKey;
  const sort=f.window.document.querySelector('#transfer-sort');sort.value='name';sort.dispatchEvent(new f.window.Event('change',{bubbles:true}));
  assert.match(f.window.document.querySelector('.transfer-file .tree-label').textContent,/A.flac/);
  await f.poll();assert.equal([...f.window.document.querySelectorAll('details')].find(el=>el.dataset.treeKey===key).open,true);
  const filter=f.window.document.querySelector('#transfer-filter');filter.value='complete';filter.dispatchEvent(new f.window.Event('change',{bubbles:true}));
  assert.equal(f.window.document.querySelectorAll('.transfer-file').length,1);
  assert.match(f.window.document.querySelector('.tree-meta').textContent,/1\/2 downloaded/);
  assert.ok(f.window.document.querySelector('summary progress').value<100);
  assert.deepEqual(f.errors,[]);
 }finally{f.close();}
});

test('completion notices work off the Downloads page and do not repeat or report failed transfers as successful',async()=>{
 const initial=[transferFile('a','A.flac'),transferFile('b','B.flac'),transferFile('c','Failure.flac','Completed, Errored',{filename:'Other\\Failure.flac'})];
 const f=await fixture(true,null,{}, {transfers:transferData(initial)});
 try{
  assert.equal(f.window.document.querySelectorAll('.download-notice').length,0);
  f.setTransfers(transferData([{...initial[0],state:'Completed, Succeeded',bytesTransferred:100},initial[1],initial[2]]));await f.poll();
  assert.match(f.window.document.querySelector('.download-notice').textContent,/File downloaded: A.flac/);
  await f.poll();assert.equal(f.window.document.querySelectorAll('.download-notice').length,1);
  f.setTransfers(transferData([{...initial[0],state:'Completed, Succeeded',bytesTransferred:100},{...initial[1],state:'Completed, Succeeded',bytesTransferred:100},initial[2]]));await f.poll();
  assert.equal(f.window.document.querySelectorAll('.download-notice').length,2);
  assert.match(f.window.document.querySelector('#download-notices').textContent,/Folder downloaded: Album · 2 files/);
  assert.ok(!f.window.document.querySelector('#download-notices').textContent.includes('Failure'));
  assert.match(f.window.document.querySelector('h1').textContent,/Library/);
  f.click('[data-action="dismiss-notice"]');assert.equal(f.window.document.querySelectorAll('.download-notice').length,1);
  assert.deepEqual(f.errors,[]);
 }finally{f.close();}
});

test('clear finished downloads keeps active records and failed cleanup leaves the tree intact',async()=>{
 for(const fail of [false,true]){
  const f=await fixture(true,null,{}, {clearFailure:fail,transfers:transferData([transferFile('a','Active.flac'),transferFile('b','Done.flac','Completed, Succeeded')])});
  try{
   f.click('[data-nav="transfers"]');await tick();f.click('[data-action="clear-downloads"]');await tick();await tick();
   assert.ok(f.calls.some(c=>c.path==='/api/soulseek/downloads/completed'&&c.method==='DELETE'));
   assert.equal(f.window.document.querySelectorAll('.transfer-file').length,fail?2:1);
   if(fail)assert.match(f.window.document.querySelector('#toast').textContent,/slskd unavailable/);
   else assert.match(f.window.document.querySelector('#toast').textContent,/Files remain in Inbox/);
   assert.deepEqual(f.errors,[]);
  }finally{f.close();}
 }
});

test('review can be rejected from its modal and cleared without applying; rejection errors leave it reviewable',async()=>{
 for(const fail of [false,true]){
  const f=await fixture(true,null,{}, {cancelFailure:fail});
  try{
   f.click('[data-nav="jobs"]');await tick();f.click(`[data-show-plan="${planId}"]`);await tick();f.click(`[data-cancel-job="${planId}"]`);await tick();
   assert.equal(f.window.document.querySelector('#modal').open,fail);
   assert.ok(!f.calls.some(c=>c.path.endsWith('/apply')));
   if(!fail){assert.match(f.window.document.querySelector('.job-card').textContent,/Cancelled/);f.click('[data-action="clear-jobs"]');await tick();assert.equal(f.window.document.querySelectorAll('.job-card').length,0);}
   else assert.match(f.window.document.querySelector('#toast').textContent,/already applying/);
   assert.deepEqual(f.errors,[]);
  }finally{f.close();}
 }
});

test('history uses a dated timeline with a filter, escaped paths and undo only for supported actions',async()=>{
 const f=await fixture(true,null,{}, {events:[{id:'move-id',created:1700000000,kind:'move',undone:false,data:{root:'inbox',source:'<script>',destination:'Album/New'}},{id:'delete-id',created:1700000000,kind:'delete',undone:false,data:{root:'library',path:'Old Album'}},{id:'mkdir-id',created:1699800000,kind:'mkdir',undone:true,data:{root:'inbox',path:'Folder'}}]});
 try{
  f.click('[data-nav="history"]');await tick();assert.equal(f.window.document.querySelectorAll('.history-day').length,2);assert.equal(f.window.document.querySelectorAll('.history-event').length,3);
  assert.equal(f.window.document.querySelectorAll('[data-undo]').length,1);assert.ok(!f.window.document.querySelector('#history-timeline script'));
  assert.match(f.window.document.querySelector('.history-detail').textContent,/<script>/);
  const filter=f.window.document.querySelector('#history-filter');filter.value='delete';filter.dispatchEvent(new f.window.Event('change',{bubbles:true}));
  assert.equal(f.window.document.querySelectorAll('.history-event').length,1);assert.match(f.window.document.querySelector('.history-detail').textContent,/Old Album/);
  assert.deepEqual(f.errors,[]);
 }finally{f.close();}
});

test('Inbox badges appear in grid, list and details while Match all queues the current folder',async()=>{
 const processed={...folder('Matched Album'),processing:{audio_files:2,matched_files:2,imported_files:2,imported:true,match:'matched'}};
 const review={...folder('Review Album'),processing:{audio_files:2,matched_files:0,imported_files:0,imported:false,match:'review'}};
 const f=await fixture(true,null,{}, {entries:[processed,review]});
 try{
  f.click('[data-nav="inbox"]');await tick();
  assert.match(f.window.document.querySelector('.folder-card').textContent,/Imported/);
  assert.match(f.window.document.querySelector('.folder-card').textContent,/Matched/);
  assert.match(f.window.document.querySelectorAll('.folder-card')[1].textContent,/Needs review/);
  f.click('.folder-card');assert.match(f.window.document.querySelector('#inspector').textContent,/Imported/);
  f.click('[data-action="view-list"]');assert.match(f.window.document.querySelector('.file-row').textContent,/Imported/);
  f.click('[data-action="match-all"]');await tick();
  assert.ok(f.calls.some(c=>c.path==='/api/musicbrainz/match-all'&&c.body.path===''));
  assert.match(f.window.document.querySelector('h1').textContent,/Tasks/);
  assert.ok(!f.calls.some(c=>c.path.endsWith('/apply')));
  assert.deepEqual(f.errors,[]);
 }finally{f.close();}
});

test('cleanup switch persists and a batch preview names the folder before its explicit import action',async()=>{
 const f=await fixture(true,null,{}, {cleanup:true,matchJob:true});
 try{
  f.click('[data-nav="settings"]');await tick();
  const input=f.window.document.querySelector('#delete-inbox-after-import');assert.equal(input.checked,true);
  input.checked=false;input.dispatchEvent(new f.window.Event('change',{bubbles:true}));await tick();
  assert.ok(f.calls.some(c=>c.path==='/api/preferences'&&c.method==='PATCH'&&c.body.delete_inbox_after_import===false));
  f.click('[data-nav="jobs"]');await tick();f.click(`[data-show-plan="${planId}"]`);await tick();
  assert.match(f.window.document.querySelector('.cleanup-warning').textContent,/Inbox\/Album.*everything inside/);
  assert.match(f.window.document.querySelector('[data-apply-plan]').textContent,/Import & delete Inbox folder/);
  assert.ok(!f.calls.some(c=>c.path.endsWith('/apply')));
  assert.deepEqual(f.errors,[]);
 }finally{f.close();}
});

test('folder drop reader retains nested and empty folders and reads every page beyond 100 entries',async()=>{
 const f=await fixture();
 try{
  const entry=(name)=>({name,isFile:true,file(resolve){resolve(new f.window.File(['x'],name));}});
  const empty={name:'Empty',isDirectory:true,createReader(){return {readEntries(resolve){resolve([]);}};}};
  const nested={name:'Disc',isDirectory:true,createReader(){let page=0;return {readEntries(resolve){resolve(page++===0?[entry('Nested.flac')]:[]);}};}};
  const album={name:'Album',isDirectory:true,createReader(){let page=0;return {readEntries(resolve){resolve(page++===0?Array.from({length:100},(_,i)=>entry(`${i}.flac`)):page===2?[entry('100.flac'),empty,nested]:[]);}};}};
  const result=await f.window.testReadDropped([{entry:album}],[]);
  assert.equal(result.files.length,102);
  assert.ok(result.files.some(file=>file.path==='Album/Disc/Nested.flac'));
  assert.deepEqual([...result.directories],['Album','Album/Empty','Album/Disc']);
  assert.deepEqual(f.errors,[]);
 }finally{f.close();}
});

test('Add to Library picks a destination graphically and previews an as-is copy without MusicBrainz',async()=>{
 const f=await fixture(true,null,{}, {manual:true});
 try{
  f.click('[data-nav="inbox"]');await tick();f.click('.folder-card');f.click('[data-action="add-to-library"]');await tick();
  assert.ok(f.calls.some(c=>c.path==='/api/files'&&c.params.get('root')==='library'));
  f.click('[data-picker-path="Second Artist"]');await tick();
  const input=f.window.document.querySelector('#manual-album-name');input.value='Offline Album';input.dispatchEvent(new f.window.Event('input',{bubbles:true}));
  assert.match(f.window.document.querySelector('#manual-destination').textContent,/Library\/Second Artist\/Offline Album/);
  f.click('[data-action="confirm-manual"]');await tick();await tick();
  assert.ok(f.calls.some(c=>c.path==='/api/imports/manual-preview'&&c.body.path==='Artist <script>'&&c.body.destination==='Second Artist/Offline Album'));
  assert.match(f.window.document.querySelector('.plan-banner').textContent,/kept as-is.*No MusicBrainz/);
  assert.ok(!f.calls.some(c=>c.path.startsWith('/api/musicbrainz/')||c.path.endsWith('/apply')));
  assert.deepEqual(f.errors,[]);
 }finally{f.close();}
});

test('Library shows only its total duration and refreshes through the existing button',async()=>{
 const f=await fixture(true,null,{}, {stats:{ready:true,scanning:false,duration:600,artists:[{name:'Hidden artist'}]}});
 try{
  const doc=f.window.document;
  assert.equal(doc.querySelector('.library-time').textContent,'Total library time10 min');
  assert.equal(doc.querySelector('#library-map,.map-chart,#map-filter'),null);
  assert.ok(!doc.querySelector('#content').textContent.includes('Hidden artist'));
  f.click('[data-action="reload"]');await tick();
  assert.ok(f.calls.some(c=>c.path==='/api/library/stats/refresh'&&c.method==='POST'));
  f.click('[data-nav="inbox"]');await tick();
  assert.equal(doc.querySelector('.library-time'),null);
  assert.deepEqual(f.errors,[]);
 }finally{f.close();}
});

test('Library duration handles hours, short tracks, empty libraries and pending counts',async()=>{
 for(const [stats,expected] of [
  [{ready:true,duration:4455},'1 h 14 min'],
  [{ready:true,duration:45},'0 min 45 sec'],
  [{ready:true,duration:0},'0 min'],
  [{ready:false,scanning:true},'…'],
  [{ready:false,error:'Cannot read Library'},'Unavailable']
 ]){
  const f=await fixture(true,null,{}, {stats});
  try{assert.equal(f.window.document.querySelector('#library-time-value').textContent,expected);assert.deepEqual(f.errors,[]);}finally{f.close();}
 }
});
