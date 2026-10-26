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

async function fixture(authenticated=true) {
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
  let entries = [folder('Artist <script>'), folder('Second Artist'), file('Song.wav')];
  let account = {managed:true,api_reachable:true,logged_in:false,saved:false,busy:false,error:null};
  window.fetch = async (url, options={}) => {
    const path = new URL(url, window.location.origin);
    const method = options.method || 'GET';
    const body = options.body ? JSON.parse(options.body) : null;
    calls.push({path:path.pathname, params:path.searchParams, method, body});
    let result = {}, status = 200;
    if(path.pathname==='/api/session') {result={authenticated};if(!authenticated)status=401;}
    else if(path.pathname==='/api/login') {authenticated=true;result={ok:true};}
    else if(path.pathname==='/api/system') result={slskd:true,beets:true,navidrome_url:''};
    else if(path.pathname==='/api/soulseek/auth/status') result=account;
    else if(path.pathname==='/api/soulseek/auth/login'||path.pathname==='/api/soulseek/auth/register') {account={...account,logged_in:true,username:body.username,saved:body.remember};result={...account,message:'Вход подтверждён'};}
    else if(path.pathname==='/api/soulseek/auth/logout') {account={...account,logged_in:false,username:null,saved:false};result=account;}
    else if(path.pathname==='/api/files') result={path:path.searchParams.get('path')||'.',entries:path.searchParams.get('path')?[]:entries};
    else if(path.pathname==='/api/metadata') result={title:'Song',artist:'Test Artist',duration:61,bitrate:320};
    else if(path.pathname==='/api/folders') {entries.push(folder(body.path));result={ok:true};}
    else if(path.pathname==='/api/move') {entries=entries.map(entry=>entry.path===body.source?{...entry,path:body.destination,name:body.destination}:entry);result={ok:true};}
    else if(path.pathname==='/api/soulseek/searches'&&method==='POST') result={id:search};
    else if(path.pathname===`/api/soulseek/searches/${search}`) result={search:{id:search,isComplete:true},responses:[{username:'Peer',hasFreeUploadSlot:true,uploadSpeed:1024,files:[{filename:'Music\\Album\\01.flac',size:123456,bitRate:900}]}]};
    else if(path.pathname==='/api/soulseek/downloads') result=method==='POST'?{ok:true}:[];
    else if(path.pathname==='/api/jobs') result=[plan];
    else if(path.pathname===`/api/jobs/${planId}`) result=plan;
    else if(path.pathname===`/api/imports/${planId}/apply`) result={id:'import-job'};
    else if(path.pathname==='/api/jobs/import-job') result={status:'done',result:{files:1}};
    else if(path.pathname==='/api/history') result=[];
    else if(path.pathname==='/api/logout') authenticated=false;
    else {status=404;result={detail:'Unmocked route: '+path.pathname};}
    return {ok:status<400,status,json:async()=>result};
  };
  window.eval(code);
  await tick();
  const click = selector => {
    const target=window.document.querySelector(selector);
    assert.ok(target,`Element exists: ${selector}`);
    target.click();
    return target;
  };
  const submit = selector => window.document.querySelector(selector).dispatchEvent(new window.Event('submit',{bubbles:true,cancelable:true}));
  return {dom,window,calls,errors,click,submit,close(){dom.window.close();}};
}

test('login opens the archive, escaped filenames render as text, filter works', async () => {
  const f=await fixture(false);
  try {
    assert.ok(f.window.document.querySelector('#login-form'));
    f.window.document.querySelector('#password').value='archive-password';f.submit('#login-form');await tick();
    assert.match(f.window.document.querySelector('h1').textContent,/Место для твоей музыки/);
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
    assert.equal(f.window.document.querySelectorAll('.search-group').length,1);
    f.click('[data-download-group="0"]');await tick();
    const call=f.calls.find(c=>c.path==='/api/soulseek/downloads'&&c.method==='POST');
    assert.deepEqual(call.body,{username:'Peer',files:[{filename:'Music\\Album\\01.flac',size:123456}]});
    assert.match(f.window.document.querySelector('#toast').textContent,/поставлено в очередь/);
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
    assert.match(f.window.document.querySelector('#toast').textContent,/Оригиналы сохранены/);
    assert.equal(f.window.document.querySelector('#modal').open,false);
    assert.deepEqual(f.errors,[]);
  } finally {f.close();}
});

test('Soulseek registration confirms passwords and displays only verified server login', async () => {
  const f=await fixture();
  try {
    f.click('[data-nav="settings"]');await tick();
    assert.match(f.window.document.querySelector('.account-status').textContent,/Нет входа/);
    f.click('[data-action="soulseek-auth-register"]');
    f.window.document.querySelector('#soulseek-username').value='New User';
    f.window.document.querySelector('#soulseek-password').value='private-password';
    f.window.document.querySelector('#soulseek-confirm').value='different';
    f.submit('#soulseek-account-form');await tick();
    assert.match(f.window.document.querySelector('#soulseek-account-error').textContent,/не совпадают/);
    assert.ok(!f.calls.some(c=>c.path==='/api/soulseek/auth/register'));
    f.window.document.querySelector('#soulseek-confirm').value='private-password';
    f.submit('#soulseek-account-form');await tick();
    const call=f.calls.find(c=>c.path==='/api/soulseek/auth/register');
    assert.equal(call.body.username,'New User');assert.equal(call.body.remember,true);
    assert.match(f.window.document.querySelector('.account-status').textContent,/Вход подтверждён/);
    assert.ok(!f.window.document.body.innerHTML.includes('private-password'));
    f.click('[data-action="soulseek-logout"]');await tick();
    assert.match(f.window.document.querySelector('.account-status').textContent,/Нет входа/);
    assert.deepEqual(f.errors,[]);
  } finally {f.close();}
});
