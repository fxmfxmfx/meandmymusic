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
const tick = (ms=20) => new Promise(resolve => setTimeout(resolve, ms));

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
    else if(path.pathname==='/api/library/search') result=scenario.search?await scenario.search(path.searchParams):{query:path.searchParams.get('q'),entries:entries.filter(entry=>entry.name.toLowerCase().includes(path.searchParams.get('q').toLowerCase())),next_offset:null};
    else if(path.pathname==='/api/library/stats'||path.pathname==='/api/library/stats/refresh') result=scenario.stats||{ready:true,scanning:false,duration:0,tracks:0,artists:[],albums:0,skipped:0};
    else if(path.pathname==='/api/soulseek/auth/status') result=account;
    else if(path.pathname==='/api/soulseek/auth/login'||path.pathname==='/api/soulseek/auth/register') {if(authFailure){status=400;account={...account,error:authFailure.message,failure:authFailure};result={detail:authFailure.message,error:authFailure};}else{account={...account,logged_in:true,username:body.username,saved:body.remember};result={...account,message:'Signed in'};}}
    else if(path.pathname==='/api/soulseek/auth/logout') {account={...account,logged_in:false,username:null,saved:false};result=account;}
    else if(path.pathname==='/api/files'&&method==='DELETE') {entries=entries.filter(entry=>entry.path!==body.path);result={ok:true};}
    else if(path.pathname==='/api/files') result={path:path.searchParams.get('path')||'.',entries:path.searchParams.get('path')?[]:entries};
    else if(path.pathname==='/api/metadata') result={title:'Song',artist:'Test Artist',duration:61,bitrate:320};
    else if(path.pathname==='/api/folders') {entries.push(folder(body.path));result={ok:true};}
    else if(path.pathname==='/api/move') {entries=entries.map(entry=>entry.path===body.source?{...entry,path:body.destination,name:body.destination}:entry);result={ok:true};}
    else if(path.pathname==='/api/soulseek/searches'&&method==='POST') result=scenario.searchStart?await scenario.searchStart(body,calls):{id:search};
    else if(path.pathname.startsWith('/api/soulseek/searches/')&&['DELETE','PUT'].includes(method)) result={ok:true};
    else if(path.pathname.startsWith('/api/soulseek/searches/')) {
      const id=path.pathname.split('/').pop();
      result=scenario.searchResponse?await scenario.searchResponse(id,calls):{search:{id,isComplete:true},responses:scenario.responses||[{username:'Peer',hasFreeUploadSlot:true,uploadSpeed:1024,files:[{filename:'Music\\Album\\01.flac',size:123456,bitRate:900}]}]};
    }
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
    assert.equal(f.window.document.querySelector('.browser-toolbar .breadcrumbs'),null);
    const filter=f.window.document.querySelector('.file-toolbar #file-filter');filter.value='Second';filter.dispatchEvent(new f.window.Event('input',{bubbles:true}));await tick(320);
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

test('Soulseek results expose filters and ranking controls and keep original remote paths',async()=>{
 const files=[
  {filename:'Music\\Artist\\Album\\CD1\\01.flac',size:1000,bitRate:900,isVariableBitRate:true,bitDepth:24,sampleRate:96000,length:125},
  {filename:'Music\\Artist\\Album\\CD1\\01.flac',size:1000,bitRate:900,bitRateIsVariable:true,bitDepth:24,sampleRate:96000,length:125},
  {filename:'Music\\Artist\\Album\\CD1\\02.mp3',size:500,bitRate:320,length:61},
  {filename:'Music\\Artist\\Other\\03.flac',size:800,bitRate:800,length:90}
 ];
 const f=await fixture(true,null,{}, {responses:[
  {username:'Zulu Peer',files,hasFreeUploadSlot:false,queueLength:8,uploadSpeed:2048},
  {username:'Alpha Peer',files:[{filename:'Music\\Artist\\Album\\song.mp3',size:200}],hasFreeUploadSlot:true,queueLength:2,uploadSpeed:1024}
 ]});
 try{
  f.click('[data-nav="soulseek"]');await tick();
  f.window.document.querySelector('#soulseek-query').value='artist';f.submit('#soulseek-form');await tick();
  for(const selector of ['#search-format','#search-slots','#search-sort','#search-filter']) assert.ok(f.window.document.querySelector(selector),`Expected ${selector}`);
  assert.deepEqual([...f.window.document.querySelector('#search-format').options].map(o=>o.value).filter(v=>['all','lossless','mp3','flac'].includes(v)).sort(),['all','flac','lossless','mp3']);
  assert.ok(f.window.document.querySelector('#search-sort').querySelector('option[value="size"]'));
  assert.equal(f.window.document.querySelectorAll('.search-file').length,4,'same username and exact filename is deduplicated');
  const rendered=f.window.document.querySelector('.search-results')?.textContent||f.window.document.querySelector('#search-results').textContent;
  assert.match(rendered,/FLAC/);assert.match(rendered,/2:05|02:05/);assert.match(rendered,/VBR/i);assert.match(rendered,/24 bit/i);
  const format=f.window.document.querySelector('#search-format');format.value='mp3';format.dispatchEvent(new f.window.Event('change',{bubbles:true}));
  assert.ok([...f.window.document.querySelectorAll('.search-file')].every(el=>el.textContent.toLowerCase().includes('.mp3')));
  const filter=f.window.document.querySelector('#search-filter');filter.value='Zulu Peer CD1 02.mp3';filter.dispatchEvent(new f.window.Event('input',{bubbles:true}));await tick(180);
  const branch=[...f.window.document.querySelectorAll('[data-download-tree]')].find(el=>el.getAttribute('aria-label')?.includes('folder'));
  assert.ok(branch,'filtered result has a downloadable folder branch');branch.click();await tick();
  const download=f.calls.find(c=>c.path==='/api/soulseek/downloads'&&c.method==='POST');
  assert.deepEqual(download.body.files,[{filename:'Music\\Artist\\Album\\CD1\\02.mp3',size:500}], 'download uses only visible files and preserves original remote filename');
 }finally{f.close();}
});

test('Soulseek extended search reports live counters, audio-only results, clear filters and caps',async()=>{
 const f=await fixture(true,null,{}, {
  searchResponse:async()=>({search:{state:'InProgress',isComplete:false,fileCount:32,responseCount:4},responses:[]})
 });
 try{
  f.click('[data-nav="soulseek"]');await tick();
  const mode=f.window.document.querySelector('#soulseek-mode');assert.ok(mode);
  mode.value='extended';f.window.document.querySelector('#soulseek-query').value='album';f.submit('#soulseek-form');await tick();
  assert.deepEqual(f.calls.find(c=>c.path==='/api/soulseek/searches'&&c.method==='POST').body,{query:'album',mode:'extended'});
  assert.match(f.window.document.querySelector('#search-status').textContent,/32 files reported/i);
  assert.match(f.window.document.querySelector('#search-status').textContent,/4 replies/i);
  assert.match(f.window.document.querySelector('#search-results').textContent,/Collecting results/i);
  assert.equal(f.window.document.querySelectorAll('.search-file').length,0);
  const inputs=f.window.document.querySelector('#search-format');assert.ok(inputs.querySelector('option[value="audio"]'));
  f.window.document.querySelector('#search-filter').value='CD1';f.window.document.querySelector('#search-filter').dispatchEvent(new f.window.Event('input',{bubbles:true}));
  inputs.value='audio';inputs.dispatchEvent(new f.window.Event('change',{bubbles:true}));
  f.window.document.querySelector('#search-slots').checked=true;f.window.document.querySelector('#search-slots').dispatchEvent(new f.window.Event('change',{bubbles:true}));
  const clear=f.window.document.querySelector('[data-action="clear-search-filters"]');assert.ok(clear&&!clear.hidden);
  const postCount=f.calls.filter(c=>c.path==='/api/soulseek/searches'&&c.method==='POST').length;
  clear.click();await tick();
  assert.equal(f.window.document.querySelector('#search-filter').value,'');
  assert.equal(f.window.document.querySelector('#search-format').value,'all');
  assert.equal(f.window.document.querySelector('#search-slots').checked,false);
  assert.equal(f.calls.filter(c=>c.path==='/api/soulseek/searches'&&c.method==='POST').length,postCount);
  assert.equal(f.window.document.querySelector('#soulseek-query').value,'album');
  f.window.document.querySelector('#search-filter').value='peer';f.window.document.querySelector('#search-filter').dispatchEvent(new f.window.Event('input',{bubbles:true}));
  f.window.document.querySelector('#search-format').value='audio';f.window.document.querySelector('#search-format').dispatchEvent(new f.window.Event('change',{bubbles:true}));
  f.window.document.querySelector('#search-slots').checked=true;f.window.document.querySelector('#search-slots').dispatchEvent(new f.window.Event('change',{bubbles:true}));
  f.window.document.querySelector('#soulseek-query').value='new album';f.submit('#soulseek-form');await tick();
  assert.equal(f.window.document.querySelector('#search-filter').value,'','a fresh query clears the local text filter');
  assert.equal(f.window.document.querySelector('#search-format').value,'audio','a fresh query preserves the selected format');
  assert.equal(f.window.document.querySelector('#search-slots').checked,true,'a fresh query preserves the free-slot choice');

  const terminal=await fixture(true,null,{}, {searchResponse:async()=>({search:{state:'Completed, FileLimitReached',isComplete:true,fileCount:3,responseCount:20},responses:[{username:'Peer',files:[
   {filename:'Music\\Album\\song.m4a',size:10},{filename:'Music\\Album\\cover.jpg',size:5},{filename:'Music\\Album\\archive.zip',size:20}
  ]}]})});
  try{
   terminal.click('[data-nav="soulseek"]');await tick();terminal.window.document.querySelector('#soulseek-mode').value='standard';terminal.window.document.querySelector('#soulseek-query').value='album';terminal.submit('#soulseek-form');await tick();
   assert.deepEqual(terminal.calls.find(c=>c.path==='/api/soulseek/searches'&&c.method==='POST').body,{query:'album'});
   const notice=terminal.window.document.querySelector('#search-status').textContent;assert.match(notice,/limit reached/i);assert.match(notice,/extended/i);
   const format=terminal.window.document.querySelector('#search-format');format.value='audio';format.dispatchEvent(new terminal.window.Event('change',{bubbles:true}));
   assert.equal(terminal.window.document.querySelectorAll('.search-file').length,1,'audio includes m4a and excludes cover art and ZIP files');
   assert.match(terminal.window.document.querySelector('#search-status').textContent,/1 audio file/i);
   assert.match(terminal.window.document.querySelector('#search-status').textContent,/2 other files/i);
  }finally{terminal.close();}
  assert.deepEqual(f.errors,[]);
 }finally{f.close();}
});

test('Soulseek sorting ranks availability, queues, names, counts and sizes',async()=>{
 const peer=(username,slot,queue,speed,sizes)=>({username,hasFreeUploadSlot:slot,queueLength:queue,uploadSpeed:speed,files:sizes.map((size,index)=>({filename:`Album/${index}.flac`,size}))});
 const f=await fixture(true,null,{}, {responses:[peer('Zulu',false,3,3000,[200,200,200]),peer('Alpha',true,5,1000,[500,500]),peer('Beta',true,1,2000,[10])]});
 try{
  f.click('[data-nav="soulseek"]');await tick();f.window.document.querySelector('#soulseek-query').value='album';f.submit('#soulseek-form');await tick();
  for(const [sort,expected] of [['speed',['Beta','Alpha','Zulu']],['queue',['Beta','Alpha','Zulu']],['name',['Alpha','Beta','Zulu']],['files',['Zulu','Alpha','Beta']],['size',['Alpha','Zulu','Beta']]]){
   const select=f.window.document.querySelector('#search-sort');select.value=sort;select.dispatchEvent(new f.window.Event('change',{bubbles:true}));
   assert.deepEqual([...f.window.document.querySelectorAll('[data-tree-scope="search"] > details > summary > .tree-label')].map(el=>el.textContent),expected,sort);
  }
 }finally{f.close();}
});

test('Soulseek result list paginates users in batches of 25',async()=>{
 const responses=Array.from({length:27},(_,i)=>({username:`Peer ${String(i).padStart(2,'0')}`,files:[{filename:`Music\\${i}.flac`,size:i+1}]}));
 const f=await fixture(true,null,{}, {responses});
 try{
  f.click('[data-nav="soulseek"]');await tick();f.window.document.querySelector('#soulseek-query').value='music';f.submit('#soulseek-form');await tick();
  assert.equal(f.window.document.querySelectorAll('[data-tree-scope="search"] > details').length,25);
  f.click('[data-action="more-search-results"]');
  assert.equal(f.window.document.querySelectorAll('[data-tree-scope="search"] > details').length,27);
  assert.deepEqual(f.errors,[]);
 }finally{f.close();}
});

test('unchanged Soulseek poll keeps result DOM and a late response cannot restore a stopped search',async()=>{
 let starts=0, release;
 const f=await fixture(true,null,{}, {
  searchStart:async()=>({id:`search-${++starts}`}),
  searchResponse:async(id)=>id==='search-1'?{search:{id,isComplete:false},responses:[{username:'Peer',files:[{filename:'Music\\Album\\01.flac',size:10}]}]}:{search:{id,isComplete:false},responses:null}
 });
 try{
  f.click('[data-nav="soulseek"]');await tick();f.window.document.querySelector('#soulseek-query').value='first';f.submit('#soulseek-form');await tick();
  const original=f.window.document.querySelector('[data-tree-scope="search"] > details');
  await f.window.testPollSearch();assert.equal(f.window.document.querySelector('[data-tree-scope="search"] > details'),original,'unchanged response preserves DOM identity');
  const pending=new Promise(resolve=>{release=resolve;});
  // Replace the response callback after the first completed fixture response.
  const fetch=f.window.fetch;
  f.window.fetch=async(url,options={})=>{
   if(new URL(url,f.window.location.origin).pathname==='/api/soulseek/searches/search-1'&&options.method!=='DELETE'){
    f.calls.push({path:'/api/soulseek/searches/search-1',method:'GET'});return {ok:true,status:200,json:()=>pending};
   }
   return fetch(url,options);
  };
  const stale=f.window.testPollSearch();await tick();
  f.click('[data-action="stop-search"]');await tick();release({search:{id:'search-1',isComplete:false},responses:[{username:'Late Peer',files:[{filename:'Late.flac',size:1}]}]});await stale;await tick();
  assert.match(f.window.document.querySelector('#search-status').textContent,/Stopped/);
  assert.doesNotMatch(f.window.document.querySelector('#search-results').textContent,/Late Peer/);
 }finally{f.close();}
});

test('empty Soulseek searches replace waiting state on completion and stop',async()=>{
 let polls=0;
 const f=await fixture(true,null,{}, {
  searchResponse:async()=>++polls===2
   ?{search:{isComplete:true},responses:null,revision:'0:0:0'}
   :{search:{isComplete:false},responses:[],revision:'0:0:0'}
 });
 try{
  f.click('[data-nav="soulseek"]');await tick();
  f.window.document.querySelector('#soulseek-query').value='empty album';f.submit('#soulseek-form');await tick();
  assert.match(f.window.document.querySelector('#search-results').textContent,/Waiting for results/);
  assert.ok(f.window.document.querySelector('#search-status .spinner'));
  await f.window.testPollSearch();
  assert.match(f.window.document.querySelector('#search-results').textContent,/No results/);
  assert.doesNotMatch(f.window.document.querySelector('#search-results').textContent,/Waiting for results/);
  assert.equal(f.window.document.querySelector('#search-status .spinner'),null);
  assert.match(f.window.document.querySelector('#search-status').textContent,/Search complete/);
  f.window.document.querySelector('#soulseek-query').value='stopped empty';f.submit('#soulseek-form');await tick();
  assert.match(f.window.document.querySelector('#search-results').textContent,/Waiting for results/);
  f.click('[data-action="stop-search"]');await tick();
  assert.doesNotMatch(f.window.document.querySelector('#search-results').textContent,/Waiting for results/);
  assert.equal(f.window.document.querySelector('#search-status .spinner'),null);
  assert.match(f.window.document.querySelector('#search-status').textContent,/Stopped/);
  assert.deepEqual(f.errors,[]);
 }finally{f.close();}
});

test('a fresh Soulseek search renders files when the first empty poll is followed by results',async()=>{
 let polls=0;
 const f=await fixture(true,null,{}, {searchResponse:async()=>++polls===1
  ?{search:{isComplete:false},responses:[],revision:null}
  :{search:{isComplete:true},responses:[{username:'Late Peer',files:[{filename:'Music\\Album\\01.flac',size:10}]}],revision:null}});
 try{
  f.click('[data-nav="soulseek"]');await tick();
  f.window.document.querySelector('#soulseek-query').value='late album';f.submit('#soulseek-form');await tick();
  assert.match(f.window.document.querySelector('#search-results').textContent,/Waiting for results/);
  await f.window.testPollSearch();
  assert.match(f.window.document.querySelector('#search-results').textContent,/Late Peer/);
  assert.match(f.window.document.querySelector('#search-status').textContent,/Search complete/);
  assert.equal(f.window.document.querySelector('#search-status .spinner'),null);
  assert.deepEqual(f.errors,[]);
 }finally{f.close();}
});

test('a terminal Soulseek status waits for its delayed full file snapshot',async()=>{
 const sinceValues=[];
 let polls=0;
 const f=await fixture(true,null,{}, {searchResponse:async(id,calls)=>{
  const call=calls.at(-1);sinceValues.push(call.params?.get('since')??null);
  polls++;
  if(polls===1)return {search:{id,state:'InProgress',responseCount:1,fileCount:1,lockedFileCount:0},responses:[],revision:null};
  if(polls===2)return {search:{id,state:'Completed, TimedOut',responseCount:1,fileCount:1,lockedFileCount:0},responses:[],revision:null};
  return {search:{id,state:'Completed, TimedOut',responseCount:1,fileCount:1,lockedFileCount:0},responses:[{username:'Delayed Peer',files:[{filename:'Music\\Album\\01.flac',size:10}]}],revision:'1:1:0'};
 }});
 try{
  f.click('[data-nav="soulseek"]');await tick();
  f.window.document.querySelector('#soulseek-query').value='delayed album';f.submit('#soulseek-form');await tick();
  assert.match(f.window.document.querySelector('#search-results').textContent,/Collecting results/);
  await f.window.testPollSearch();
  assert.deepEqual(sinceValues,[null,null]);
  assert.match(f.window.document.querySelector('#search-status').textContent,/Loading results/);
  assert.match(f.window.document.querySelector('#search-results').textContent,/Collecting results/);
  await f.window.testPollSearch();
  assert.deepEqual(sinceValues,[null,null,null],'an unverified partial snapshot must keep polling without a cursor');
  assert.match(f.window.document.querySelector('#search-results').textContent,/Delayed Peer/);
  assert.match(f.window.document.querySelector('#search-status').textContent,/Search complete/);
  assert.deepEqual(f.errors,[]);
 }finally{f.close();}
});

test('a terminal Soulseek result with a missing snapshot eventually offers retry',async()=>{
 let now=1_700_000_000_000,polls=0;
 const f=await fixture(true,null,{}, {searchResponse:async(id)=>{
  polls++;
  return {search:{id,state:polls===1?'InProgress':'Completed, TimedOut',responseCount:1,fileCount:1,lockedFileCount:0},responses:[],revision:null};
 }});
 try{
  f.window.Date.now=()=>now;
  f.click('[data-nav="soulseek"]');await tick();
  f.window.document.querySelector('#soulseek-query').value='missing snapshot';f.submit('#soulseek-form');await tick();
  await f.window.testPollSearch();
  assert.match(f.window.document.querySelector('#search-results').textContent,/Collecting results/);
  now+=10_001;
  await f.window.testPollSearch();
  assert.match(f.window.document.querySelector('#search-status').textContent,/Search interrupted/);
  assert.match(f.window.document.querySelector('#search-status').textContent,/Retry/);
  assert.doesNotMatch(f.window.document.querySelector('#search-results').textContent,/No results/);
  assert.deepEqual(f.errors,[]);
 }finally{f.close();}
});

test('failed Soulseek polls show a retry action and retry can recover',async()=>{
 let polls=0;
 const f=await fixture(true,null,{}, {
  searchResponse:async(id)=>{if(++polls===1)throw new Error('temporary slskd failure');return {search:{id,isComplete:false},responses:[{username:'Recovered Peer',files:[{filename:'Music\\Recovered.flac',size:12}]}]};}
 });
 try{
  f.click('[data-nav="soulseek"]');await tick();f.window.document.querySelector('#soulseek-query').value='recover';f.submit('#soulseek-form');await tick();
  assert.match(f.window.document.querySelector('#search-status').textContent,/temporary slskd failure/);
  f.click('[data-action="retry-search"]');await tick();
  assert.match(f.window.document.querySelector('#search-status').textContent,/Searching|Search complete/);
  assert.match(f.window.document.querySelector('#search-results').textContent,/Recovered Peer/);
  assert.deepEqual(f.errors,[]);
 }finally{f.close();}
});

test('a failed old search response cannot replace results for a newer query',async()=>{
 let starts=0,rejectOld;
 const oldResult=new Promise((resolve,reject)=>{rejectOld=reject;});
 const f=await fixture(true,null,{}, {
  searchStart:async()=>({id:`search-${++starts}`}),
  searchResponse:async(id)=>id==='search-1'?oldResult:{search:{id,isComplete:true},responses:[{username:'New Peer',files:[{filename:'Music\\New.flac',size:20}]}]}
 });
 try{
  f.click('[data-nav="soulseek"]');await tick();const input=f.window.document.querySelector('#soulseek-query');input.value='first';f.submit('#soulseek-form');await tick();
  f.window.document.querySelector('#soulseek-query').value='second';f.submit('#soulseek-form');await tick();
  assert.equal(f.calls.filter(call=>call.path==='/api/soulseek/searches'&&call.method==='POST').at(-1).body.query,'second');
  assert.deepEqual(f.calls.filter(call=>call.path==='/api/soulseek/searches/search-1'&&['PUT','DELETE'].includes(call.method)).map(call=>call.method),['PUT','DELETE']);
  rejectOld(new Error('stale failure'));await tick();
  assert.match(f.window.document.querySelector('#search-results').textContent,/New Peer/);
  assert.doesNotMatch(f.window.document.querySelector('#search-status').textContent,/stale failure/);
  assert.deepEqual(f.errors,[]);
 }finally{f.close();}
});

test('slskd search deadline completes normally while cancellation and errors stay distinct',async()=>{
 for(const [remoteState,expected] of [
  ['Completed, Cancelled','Stopped'],
  ['Completed, TimedOut','Search complete'],
  ['Completed, Errored','Search interrupted']
 ]){
  const f=await fixture(true,null,{}, {searchResponse:async(id)=>({search:{id,state:remoteState,errorMessage:'peer connection dropped'},responses:[]})});
  try{
   f.click('[data-nav="soulseek"]');await tick();f.window.document.querySelector('#soulseek-query').value='terminal';f.submit('#soulseek-form');await tick();
   assert.match(f.window.document.querySelector('#search-status').textContent,new RegExp(expected));
   assert.equal(Boolean(f.window.document.querySelector('#search-status .spinner')),false,`${remoteState} is terminal`);
   if(remoteState.endsWith('Errored')){
    assert.match(f.window.document.querySelector('#search-status').textContent,/The remote search ended with an error\. Retry to start a new search/);
    assert.doesNotMatch(f.window.document.querySelector('#search-status').textContent,/peer connection dropped/,'raw upstream details stay hidden');
    assert.ok(f.window.document.querySelector('[data-action="retry-search"]'));
   }
  }finally{f.close();}
 }
});

test('retry after terminal slskd error starts a fresh search request',async()=>{
 let starts=0;
 const f=await fixture(true,null,{}, {
  searchStart:async()=>({id:`terminal-${++starts}`}),
  searchResponse:async(id)=>id==='terminal-1'?{search:{id,state:'Completed, Errored',errorMessage:'upstream error'},responses:[]}:{search:{id,state:'Completed, Succeeded'},responses:[{username:'Retry Peer',files:[{filename:'Retry.flac',size:1}]}]}
 });
 try{
  f.click('[data-nav="soulseek"]');await tick();f.window.document.querySelector('#soulseek-query').value='retry';f.submit('#soulseek-form');await tick();
  f.click('[data-action="retry-search"]');await tick();
  assert.equal(f.calls.filter(call=>call.path==='/api/soulseek/searches'&&call.method==='POST').length,2);
  assert.match(f.window.document.querySelector('#search-results').textContent,/Retry Peer/);
  assert.deepEqual(f.errors,[]);
 }finally{f.close();}
});

test('pollSearch does not issue overlapping GET requests',async()=>{
 let release,responseCalls=0;
 const pending=new Promise(resolve=>{release=resolve;});
 const f=await fixture(true,null,{}, {searchResponse:async(id)=>{responseCalls++;return pending;}});
 try{
  f.click('[data-nav="soulseek"]');await tick();f.window.document.querySelector('#soulseek-query').value='overlap';f.submit('#soulseek-form');await tick();
  const poll=f.window.testPollSearch();await tick();const parallelA=f.window.testPollSearch(),parallelB=f.window.testPollSearch();await Promise.all([parallelA,parallelB]);
  assert.equal(responseCalls,1,'in-flight request prevents additional polls');
  release({search:{id:search,state:'Searching'},responses:[]});await poll;await tick();
  assert.equal(responseCalls,1);
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
  assert.equal(doc.querySelector('.page-heading h1').textContent,'Library (10 min)');
  assert.equal(doc.querySelector('.library-time'),null);
  assert.equal(doc.querySelector('.browser-toolbar .breadcrumbs'),null);
  assert.equal(doc.querySelector('#library-map,.map-chart,#map-filter'),null);
  assert.ok(!doc.querySelector('#content').textContent.includes('Hidden artist'));
  f.click('[data-action="reload"]');await tick();
  assert.ok(f.calls.some(c=>c.path==='/api/library/stats/refresh'&&c.method==='POST'));
  f.click('[data-nav="inbox"]');await tick();
  assert.equal(doc.querySelector('.library-time'),null);
  assert.equal(doc.querySelector('.browser-toolbar').hidden,true);
  assert.equal(doc.querySelector('#file-filter'),null);
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

test('recursive Library results show parent paths and rename within the actual source folder',async()=>{
 const nested={...file('Deep Song.flac'),path:'Artist/Album/CD2/Deep Song.flac'};
 const f=await fixture(true,null,{}, {search:async()=>({entries:[nested],next_offset:null})});
 try{
  const input=f.window.document.querySelector('#file-filter');input.focus();input.value='Deep';input.dispatchEvent(new f.window.Event('input',{bubbles:true}));await tick(320);
  assert.equal(f.window.document.activeElement,input,'Typing a query keeps focus');
  assert.equal(f.window.document.querySelector('.result-path').textContent,'Library/Artist/Album/CD2');
  assert.ok(f.calls.some(c=>c.path==='/api/library/search'&&c.params.get('q')==='Deep'&&!c.params.has('path')));
  f.click('.file-row');f.click('[data-action="rename"]');f.window.document.querySelector('#file-value').value='Renamed.flac';f.submit('#file-operation');await tick();
  assert.ok(f.calls.some(c=>c.path==='/api/move'&&c.body.source===nested.path&&c.body.destination==='Artist/Album/CD2/Renamed.flac'));
  f.click('.file-row');f.click('[data-action="open-containing-folder"]');await tick();
  assert.ok(f.calls.some(c=>c.path==='/api/files'&&c.params.get('path')==='Artist/Album/CD2'));
  assert.equal(f.window.document.querySelector('#file-filter').value,'');
  assert.equal(f.window.document.querySelector('.page-heading h1').textContent,'Library (0 min)');
  assert.deepEqual(f.errors,[]);
 }finally{f.close();}
});

test('recursive Library pagination keeps duplicate names distinct and clearing returns to the browsed folder',async()=>{
 const f=await fixture(true,null,{}, {search:async params=>({entries:[{...file('Song.flac'),path:params.get('offset')==='1'?'B/Song.flac':'A/Song.flac'}],next_offset:params.get('offset')==='1'?null:1})});
 try{
  const input=f.window.document.querySelector('#file-filter');input.value='Song';input.dispatchEvent(new f.window.Event('input',{bubbles:true}));await tick(320);
  f.click('[data-action="more-library-results"]');await tick();
  assert.equal(f.window.document.querySelectorAll('.file-row').length,2);
  assert.ok(f.window.document.querySelector('[data-select="A/Song.flac"]'));
  assert.ok(f.window.document.querySelector('[data-select="B/Song.flac"]'));
  assert.equal(f.window.document.querySelector('#file-summary').textContent,'2 results');
  input.value='';input.dispatchEvent(new f.window.Event('input',{bubbles:true}));await tick(320);
  assert.equal(f.window.document.querySelectorAll('.folder-card').length,2);
  assert.equal(f.window.document.querySelector('.library-search-summary'),null);
  assert.deepEqual(f.errors,[]);
 }finally{f.close();}
});

test('late Library search responses cannot overwrite a newer query or the Inbox page',async()=>{
 let old,late;
 const f=await fixture(true,null,{}, {search:async params=>{
  if(params.get('q')==='old')return new Promise(resolve=>old=resolve);
  if(params.get('q')==='late')return new Promise(resolve=>late=resolve);
  return {entries:[file('New.flac')],next_offset:null};
 }});
 try{
  const input=f.window.document.querySelector('#file-filter');
  input.value='old';input.dispatchEvent(new f.window.Event('input',{bubbles:true}));await tick(320);
  input.value='new';input.dispatchEvent(new f.window.Event('input',{bubbles:true}));await tick(320);
  old({entries:[file('Old.flac')],next_offset:null});await tick();
  assert.ok(f.window.document.querySelector('[data-select="New.flac"]'));
  assert.equal(f.window.document.querySelector('[data-select="Old.flac"]'),null);
  input.value='late';input.dispatchEvent(new f.window.Event('input',{bubbles:true}));await tick(320);
  f.click('[data-nav="inbox"]');await tick();
  late({entries:[file('Late.flac')],next_offset:null});await tick();
  assert.equal(f.window.document.querySelector('#file-filter'),null);
  assert.equal(f.window.document.querySelector('h1').textContent,'Inbox');
  assert.equal(f.window.document.querySelector('[data-select="Late.flac"]'),null);
  assert.deepEqual(f.errors,[]);
 }finally{f.close();}
});

test('Library search failures remain retryable instead of claiming that the archive is empty',async()=>{
 let failed=true;
 const f=await fixture(true,null,{}, {search:async()=>{if(failed)throw new Error('Search unavailable');return {entries:[],next_offset:null};}});
 try{
  const input=f.window.document.querySelector('#file-filter');input.value='missing';input.dispatchEvent(new f.window.Event('input',{bubbles:true}));await tick(320);
  assert.match(f.window.document.querySelector('#file-list').textContent,/Search unavailable/);
  assert.ok(!f.window.document.querySelector('#file-list').textContent.includes('Library is empty'));
  failed=false;f.click('[data-action="retry-library-search"]');await tick();
  assert.match(f.window.document.querySelector('#file-list').textContent,/No results/);
  assert.deepEqual(f.errors,[]);
 }finally{f.close();}
});
