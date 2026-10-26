'use strict';
const app = document.querySelector('#app');
const modal = document.querySelector('#modal');
const state = {page:'library', path:'', entries:[], selected:null, filter:'', system:null, account:null, authMode:'login', accountBusy:false, accountTimer:null, searchId:null, groups:[], searchQuery:'', searchDone:false, jobs:[], player:null, view:'grid', searchTimer:null, jobTimer:null, transferTimer:null, generation:0};
const paths = {
 disc:'<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="3"/><path d="M5.6 8a7 7 0 0 1 2.7-2.5M18.4 16a7 7 0 0 1-2.7 2.5"/>',
 folder:'<path d="M3 7V5a2 2 0 0 1 2-2h5l2 3h7a2 2 0 0 1 2 2v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2Z"/>',
 inbox:'<path d="M4 4h16l2 13v3H2v-3Z"/><path d="M2 15h6l2 3h4l2-3h6M12 3v9m-3-3 3 3 3-3"/>',
 search:'<circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 5 5"/>',
 download:'<path d="M12 3v12m-5-5 5 5 5-5M4 17v4h16v-4"/>',
 jobs:'<rect x="4" y="3" width="16" height="18" rx="2"/><path d="m8 9 2 2 4-4M8 16h8"/>',
 history:'<path d="M3 11a9 9 0 1 1 2 7M3 4v7h7m2-4v6l4 2"/>',
 settings:'<path d="M4 7h16M4 17h16"/><circle cx="9" cy="7" r="3" fill="currentColor" stroke="none"/><circle cx="16" cy="17" r="3" fill="currentColor" stroke="none"/>',
 music:'<path d="M9 18V5l11-2v13M9 8l11-2"/><ellipse cx="6" cy="18" rx="3" ry="3"/><ellipse cx="17" cy="16" rx="3" ry="3"/>',
 play:'<path d="m8 4 13 8-13 8Z"/>', plus:'<path d="M12 5v14M5 12h14"/>',
 arrow:'<path d="M4 12h16m-6-6 6 6-6 6"/>', check:'<path d="m5 12 4 4L20 5"/>',
 close:'<path d="m6 6 12 12M6 18 18 6"/>', grid:'<rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/>',
 list:'<path d="M8 6h13M8 12h13M8 18h13M3 6h.1M3 12h.1M3 18h.1"/>',
 refresh:'<path d="M20 7a8 8 0 1 0 1 7M20 2v6h-6"/>',
 edit:'<path d="m15 4 5 5M4 20l5-1L21 7a2 2 0 0 0-5-5L4 14Z"/>',
 move:'<path d="M12 3v18M3 12h18m-12-6 3-3 3 3m-6 12 3 3 3-3M6 9l-3 3 3 3m12-6 3 3-3 3"/>',
 shield:'<path d="m12 3 8 3v7c0 5-8 9-8 9s-8-4-8-9V6Z"/><path d="m8 12 3 3 5-6"/>',
 external:'<path d="M14 3h7v7m0-7L10 14M10 3H3v18h18v-7"/>',
 logout:'<path d="M9 3H3v18h6m5-15 6 6-6 6M7 12h13"/>',
 trash:'<path d="M3 6h18M9 6V3h6v3M5 6l1 15h12l1-15M10 10v7m4-7v7"/>',
 file:'<path d="M5 3h9l5 5v13H5Zm9 0v6h5M8 14h8M8 17h5"/>'
};
const icon = name => `<svg class="icon" viewBox="0 0 24 24" aria-hidden="true">${paths[name] || paths.file}</svg>`;
const esc = value => String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
const size = bytes => bytes == null ? '—' : bytes < 1024 ? `${bytes} B` : bytes < 1048576 ? `${(bytes/1024).toFixed(0)} KB` : bytes < 1073741824 ? `${(bytes/1048576).toFixed(1)} MB` : `${(bytes/1073741824).toFixed(1)} GB`;
const date = value => new Date(value*1000).toLocaleString('en-GB',{day:'numeric',month:'short',hour:'2-digit',minute:'2-digit'});
const color = name => [...name].reduce((n,c)=>n+c.charCodeAt(0),0)%5;
const join = (a,b) => a && a !== '.' ? `${a}/${b}` : b;
const basename = path => path.split(/[\\/]/).pop();
const root = () => state.page === 'inbox' ? 'inbox' : 'library';
const query = values => new URLSearchParams(values).toString();
const mediaURL = (path, r=root()) => `/api/media?${query({root:r,path})}`;
const themes={current:'Original',light:'Light',dark:'Dark',gruvbox:'Gruvbox',mocha:'Catppuccin Mocha'};
function themePicker(){return `<label class="theme-picker"><span class="visually-hidden">Theme</span><select aria-label="Theme" data-theme-picker>${Object.entries(themes).map(([id,label])=>`<option value="${id}" ${document.documentElement.dataset.theme===id?'selected':''}>${label}</option>`).join('')}</select></label>`;}
let toastTimeout;
function toast(message,error=false){const el=document.querySelector('#toast');el.textContent=message;el.className=`visible${error?' error':''}`;clearTimeout(toastTimeout);toastTimeout=setTimeout(()=>el.className='',6500);}
async function api(url, options={}) {
 const response=await fetch(`/api${url}`,{...options,headers:{'Content-Type':'application/json','X-Requested-With':'meandmymusic',...options.headers},body:options.body===undefined?undefined:JSON.stringify(options.body)});
 const data=await response.json().catch(()=>({detail:`HTTP error ${response.status}`}));
 if(!response.ok){if(response.status===401 && url!=='/login')showLogin();const error=new Error(typeof data.detail==='string'?data.detail:JSON.stringify(data.detail));error.connection=data.error;throw error;}
 return data;
}
function brand(){return `<div class="brand"><div class="brand-mark">${icon('disc')}</div><div class="brand-type">me&my<span>MUSIC MANAGER</span></div></div>`;}
function showLogin(){
 clearInterval(state.searchTimer);clearInterval(state.jobTimer);clearInterval(state.transferTimer);clearInterval(state.accountTimer);if(modal.open)modal.close();
 app.innerHTML=`<main class="login-screen"><div class="login-theme">${themePicker()}</div><form class="login-form" id="login-form">${brand()}<h1>Open your library</h1><label for="password">Password</label><input id="password" name="password" type="password" autocomplete="current-password" required autofocus><button class="primary" type="submit">Sign in ${icon('arrow')}</button><p class="login-error" id="login-error" role="alert"></p></form></main>`;
}
function shell(){
 app.innerHTML=`<div class="shell"><aside class="sidebar">${brand()}<nav class="nav" aria-label="Navigation">${[['library','folder','Library'],['inbox','inbox','Inbox'],['soulseek','search','Soulseek'],['transfers','download','Downloads'],['jobs','jobs','Tasks'],['history','history','History'],['settings','settings','Settings']].map(([id,i,title])=>`<button data-nav="${id}">${icon(i)}${title}</button>`).join('')}</nav><div class="sidebar-bottom"><span class="version">me&my music · 0.4</span></div></aside><main class="main"><header class="topbar"><strong id="context-title">Library</strong><div class="status-group"><span class="status-pill" id="slskd-status"></span>${themePicker()}<button class="quiet icon-button" data-action="logout" title="Sign out" aria-label="Sign out">${icon('logout')}</button></div></header><div id="content"></div></main></div><div id="player-slot"></div>`;
 navigate(state.page);
 api('/system').then(system=>{state.system=system;updateStatus();if(state.page==='settings')renderSettings();}).catch(error=>toast(error.message,true));
 refreshAccount().catch(()=>{});clearInterval(state.accountTimer);state.accountTimer=setInterval(()=>refreshAccount(false).catch(()=>{}),10000);
}
function updateStatus(){const el=document.querySelector('#slskd-status');if(el)el.innerHTML=`<i class="dot${state.account?.logged_in?'':' off'}"></i> ${state.account?.logged_in?'Soulseek connected':state.system?.slskd?'Soulseek offline':'slskd unavailable'}`;}
async function refreshAccount(render=true){
 if(state.accountBusy)return;
 state.account=await api('/soulseek/auth/status');updateStatus();
 if(state.page==='settings'&&(render||!document.querySelector('#soulseek-account-form')?.contains(document.activeElement)))renderAccountPanel();
}
function heading(number,title,subtitle,actions=''){const titles={'01':'Library','02':'Inbox','03':'Soulseek','04':'Downloads','05':'Tasks','06':'History','07':'Settings'};return `<section class="page-heading"><h1>${titles[number]||title}</h1>${actions?`<div class="heading-actions">${actions}</div>`:''}</section>`;}
async function navigate(page){
 state.generation++;const generation=state.generation;
 clearInterval(state.transferTimer);clearInterval(state.jobTimer);
 if(state.page!==page){state.path='';state.selected=null;state.filter='';}
 state.page=page;
 document.querySelectorAll('[data-nav]').forEach(button=>button.classList.toggle('active',button.dataset.nav===page));
 const titles={library:'Library',inbox:'Inbox',soulseek:'Soulseek',transfers:'Downloads',jobs:'Tasks',history:'History',settings:'Settings'};
 document.querySelector('#context-title').textContent=titles[page];
 const content=document.querySelector('#content');content.innerHTML='<div class="loading"><span class="spinner"></span>Loading…</div>';
 try{
  if(page==='library'||page==='inbox')await loadFiles();
  else if(page==='soulseek')renderSoulseek();
  else if(page==='transfers'){await loadTransfers();state.transferTimer=setInterval(()=>loadTransfers().catch(()=>{}),4000);}
  else if(page==='jobs'){await loadJobs();state.jobTimer=setInterval(()=>loadJobs().catch(()=>{}),2500);}
  else if(page==='history')await loadHistory();
  else if(page==='settings')renderSettings();
 }catch(error){if(generation===state.generation && document.querySelector('#content'))content.innerHTML=`<div class="error-box">${esc(error.message)}<br><button class="small" data-action="reload">${icon('refresh')} Retry</button></div>`;}
}
async function loadFiles(){
 const page=state.page, path=state.path;
 const data=await api(`/files?${query({root:root(),path})}`);
 if(state.page!==page||state.path!==path)return;
 state.entries=data.entries;state.path=data.path==='.'?'':data.path;
 state.selected=state.selected?state.entries.find(e=>e.path===state.selected.path)||null:null;
 renderFiles();
}
function renderFiles(){
 const inbox=state.page==='inbox';
 if(state.drag&&document.querySelector('#file-list')){
  // Retain the native drag source until drop, even while browsing other folders.
  const original=document.querySelector('#file-list');original.classList.add('drag-preserved');
  if(!document.querySelector('#drag-file-list')){const layer=document.createElement('div');layer.id='drag-file-list';original.after(layer);}
  document.querySelector('.breadcrumbs').innerHTML=breadcrumbs();document.querySelector('.browser').dataset.dropPath=state.path;
  renderFileList();return;
 }
 document.querySelector('#content').innerHTML=heading(inbox?'02':'01','','',`<button data-action="reload" class="icon-button" aria-label="Refresh" title="Refresh">${icon('refresh')}</button><button class="primary" data-action="new-folder">${icon('plus')} New folder</button>`)+
 `<div class="workspace${state.selected?' has-selection':''}"><section class="browser" data-drop-path="${esc(state.path)}"><div class="browser-toolbar"><div class="breadcrumbs">${breadcrumbs()}</div><input class="filter-input" id="file-filter" aria-label="Filter files" placeholder="Filter files…" value="${esc(state.filter)}"></div><div class="file-actions" id="file-actions"></div><div id="file-list"></div><div class="footer-line"><span>${state.entries.length} items</span><span>Drag to move · Double-click to open</span></div></section><aside class="inspector" id="inspector" aria-label="File details"></aside></div>`;
 renderFileActions();renderFileList();renderInspector();
}
function renderFileActions(){
 const el=document.querySelector('#file-actions');if(!el)return;const disabled=state.selected?'':'disabled';
 el.innerHTML=`<button class="small" data-action="rename" ${disabled}>${icon('edit')}Rename</button><button class="small" data-action="move" ${disabled}>${icon('move')}Move</button><button class="small danger" data-action="delete" ${disabled}>${icon('trash')}Delete</button>${root()==='inbox'?`<button class="small" data-action="match" ${state.selected?.directory?'':'disabled'}>${icon('disc')}Match album</button>`:''}<span class="selection-name">${esc(state.selected?.name||'Select an item')}</span>`;
}
function breadcrumbs(){
 const chunks=state.path?state.path.split('/'):[];
 return `<button class="quiet" data-path="" data-drop-path="">${icon(root()==='inbox'?'inbox':'folder')}${root()==='inbox'?'Inbox':'Library'}</button>`+chunks.map((part,i)=>`<span>/</span><button class="quiet" data-path="${esc(chunks.slice(0,i+1).join('/'))}" data-drop-path="${esc(chunks.slice(0,i+1).join('/'))}">${esc(part)}</button>`).join('');
}
function renderFileList(){
 const entries=state.entries.filter(e=>e.name.toLowerCase().includes(state.filter.toLowerCase()));
 const dirs=entries.filter(e=>e.directory), files=entries.filter(e=>!e.directory);
 const el=document.querySelector(state.drag&&document.querySelector('#drag-file-list')?'#drag-file-list':'#file-list');if(!el)return;
 if(!entries.length){el.innerHTML=`<div class="empty">${icon(state.filter?'search':root()==='inbox'?'inbox':'folder')}<h3>${state.filter?'No results':root()==='inbox'?'Inbox is empty':'Library is empty'}</h3><p>${state.filter?'Try another name.':root()==='inbox'?'Download music from Soulseek or add an album to the Inbox folder.':'Add music to the Library folder or import an album from Inbox.'}</p>${!state.filter?`<button class="primary" data-nav="${root()==='inbox'?'soulseek':'inbox'}">${icon(root()==='inbox'?'search':'inbox')}${root()==='inbox'?'Search Soulseek':'Open Inbox'}</button>`:''}</div>`;return;}
 let html='';
 if(dirs.length){html=`<div class="section-heading"><h2>Folders <span> / ${dirs.length}</span></h2><div class="view-switch"><button class="${state.view==='grid'?'active':''}" data-action="view-grid" aria-label="Grid">${icon('grid')}</button><button class="${state.view==='list'?'active':''}" data-action="view-list" aria-label="List">${icon('list')}</button></div></div>`;
  if(state.view==='grid')html+=`<div class="folder-grid">${dirs.map(entry=>`<button class="folder-card ${state.selected?.path===entry.path?'selected':''}" draggable="true" data-select="${esc(entry.path)}" data-drop-path="${esc(entry.path)}" title="Click to select, double-click to open"><div class="folder-art color-${color(entry.name)}">${icon('folder')}</div>${state.selected?.path===entry.path?`<span class="selected-mark">${icon('check')}</span>`:''}<div class="folder-name">${esc(entry.name)}</div><div class="folder-meta">Folder · ${date(entry.modified)}</div></button>`).join('')}</div>`;
  else html+=fileTable(dirs);
 }
 if(files.length)html+=`<div class="section-heading"><h2>Files <span> / ${files.length}</span></h2></div>${fileTable(files)}`;
 el.innerHTML=html;
}
function fileTable(entries){return `<div class="table-wrap"><table><thead><tr><th>Name</th><th>Format</th><th>Size</th><th></th></tr></thead><tbody>${entries.map(entry=>`<tr class="file-row ${state.selected?.path===entry.path?'selected':''}" draggable="true" data-select="${esc(entry.path)}" ${entry.directory?`data-drop-path="${esc(entry.path)}"`:""} tabindex="0"><td><div class="file-title">${icon(entry.directory?'folder':entry.audio?'music':'file')}<div>${esc(entry.name)}<small>${date(entry.modified)}</small></div></div></td><td><span class="badge">${entry.directory?'FOLDER':esc(entry.name.split('.').pop().toUpperCase())}</span></td><td class="muted">${size(entry.size)}</td><td>${entry.directory?`<button class="quiet icon-button" data-open="${esc(entry.path)}" aria-label="Open folder">${icon('arrow')}</button>`:entry.audio?`<button class="quiet icon-button" data-play="${esc(entry.path)}" aria-label="Play">${icon('play')}</button>`:''}</td></tr>`).join('')}</tbody></table></div>`;}
function renderInspector(){
 const el=document.querySelector('#inspector');if(!el)return;const entry=state.selected;
 document.querySelector('.workspace')?.classList.toggle('has-selection',Boolean(entry));if(!entry){el.innerHTML='';return;}
 el.innerHTML=`<div class="inspector-header"><span>Details</span><button class="quiet icon-button" data-action="deselect" aria-label="Close details">${icon('close')}</button></div><div class="inspector-body"><h3>${esc(entry.name)}</h3><div class="detail-row"><span>Type</span><strong>${entry.directory?'Folder':entry.audio?'Audio':'File'}</strong></div><div class="detail-row"><span>Modified</span><strong>${date(entry.modified)}</strong></div>${!entry.directory?`<div class="detail-row"><span>Size</span><strong>${size(entry.size)}</strong></div>`:''}<div id="audio-metadata"></div>${entry.audio?`<button data-play="${esc(entry.path)}">${icon('play')}Play</button>`:''}</div>`;
 if(entry.audio){const selected=entry.path;api(`/metadata?${query({root:root(),path:entry.path})}`).then(tags=>{if(state.selected?.path!==selected)return;const metadata=document.querySelector('#audio-metadata');if(metadata)metadata.innerHTML=[['Artist',tags.artist],['Album',tags.album],['Duration',tags.duration?`${Math.floor(tags.duration/60)}:${String(tags.duration%60).padStart(2,'0')}`:''],['Bitrate',tags.bitrate?`${tags.bitrate} kbps`:'']].filter(([,value])=>value).map(([label,value])=>`<div class="detail-row"><span>${label}</span><strong>${esc(value)}</strong></div>`).join('');}).catch(()=>{});}
}
function selectFile(path){state.selected=state.entries.find(e=>e.path===path);document.querySelectorAll('[data-select]').forEach(el=>el.classList.toggle('selected',el.dataset.select===path));renderInspector();renderFileActions();}
async function openFolder(path){state.path=path;state.selected=null;state.filter='';await loadFiles();}
function showModal(title,body,footer=''){modal.innerHTML=`<div class="modal-header"><h2 id="modal-title">${title}</h2><button class="quiet icon-button" data-action="close-modal" aria-label="Close">${icon('close')}</button></div><div class="modal-content">${body}</div>${footer?`<div class="modal-footer">${footer}</div>`:''}`;if(!modal.open)modal.showModal();}
function fileDialog(mode){
 const entry=state.selected;
 if(mode!=='new-folder'&&!entry)return;
 if(mode==='move'){state.moveSource={...entry,root:root()};openMovePicker(state.path).catch(error=>toast(error.message,true));return;}
 const title=mode==='new-folder'?'New folder':mode==='rename'?'Rename':'Move';
 const value=mode==='new-folder'?'':mode==='rename'?entry.name:entry.path;
 showModal(title,`<form id="file-operation" data-mode="${mode}"><p class="field-help">${mode==='move'?'Select a destination folder.':mode==='rename'?'Enter a new name.':'Create a folder here.'}</p><label for="file-value">${mode==='move'?'Destination':'Name'}</label><input id="file-value" name="value" value="${esc(value)}" required autocomplete="off" maxlength="4096"><p class="field-help" id="file-operation-error" role="alert"></p></form>`,`<button data-action="close-modal">Cancel</button><button class="primary" type="submit" form="file-operation">${title}</button>`);
 modal.querySelector('input').focus();modal.querySelector('input').select();
}
function renderSoulseek(){
 document.querySelector('#content').innerHTML=heading('03','Soulseek','Search Soulseek and download to Inbox.')+`<section class="search-box"><form id="soulseek-form" class="search-form"><label class="visually-hidden" for="soulseek-query">Search Soulseek</label><input id="soulseek-query" name="query" placeholder="Artist, album or track…" value="${esc(state.searchQuery)}" minlength="2" maxlength="300" required><button class="primary" type="submit">${icon('search')}Search</button>${state.searchId&&!state.searchDone?'<button type="button" data-action="stop-search">Stop</button>':''}</form><p class="search-help">Results are grouped by user and folder.</p></section><div id="search-results"></div>`;
 renderSearchResults();
}
function normalizeResults(responses){
 const groups=new Map();
 for(const response of Array.isArray(responses)?responses:[]){for(const file of response.files||[]){if(!file.filename)continue;const split=file.filename.lastIndexOf('\\');const folder=split>=0?file.filename.slice(0,split):'';const key=JSON.stringify([response.username,folder]);if(!groups.has(key))groups.set(key,{username:response.username,folder,files:[],speed:response.uploadSpeed,queue:response.queueLength,slot:response.hasFreeUploadSlot});groups.get(key).files.push(file);}}
 return [...groups.values()].sort((a,b)=>Number(b.slot)-Number(a.slot)||(b.speed||0)-(a.speed||0));
}
function renderSearchResults(){
 const el=document.querySelector('#search-results');if(!el)return;
 if(!state.searchId){el.innerHTML=`<div class="empty">${icon('search')}<h3>Search for music</h3><p>Enter an artist, album or track name.</p></div>`;return;}
 el.innerHTML=`<div class="search-summary">${!state.searchDone?'<span class="spinner"></span>Searching… ':''}${state.groups.length} folders found${state.searchDone?' · search complete':''}</div>`+state.groups.slice(0,100).map((group,index)=>`<article class="search-group"><div class="search-group-head"><div><h3>${esc(basename(group.folder)||'Individual files')}</h3><p>${esc(group.username)} · ${group.files.length} files · ${size(group.files.reduce((sum,f)=>sum+f.size,0))} · ${group.slot?'slot available':`queue: ${group.queue||0}`} ${group.speed?`· ${size(group.speed)}/s`:''}</p></div><button class="small primary" data-download-group="${index}">${icon('download')}Download folder</button></div><details><summary>Show files</summary>${group.files.map((file,fileIndex)=>`<div class="search-file"><span>${esc(basename(file.filename))} <span class="muted">· ${size(file.size)} ${file.bitRate?`· ${file.bitRate} kbps`:''}${file.bitDepth?` · ${file.bitDepth} bit / ${file.sampleRate||'?'} Hz`:''}</span></span><button class="quiet icon-button" data-download-file="${index}:${fileIndex}" aria-label="Download file">${icon('download')}</button></div>`).join('')}</details></article>`).join('')+(state.searchDone&&!state.groups.length?'<div class="empty"><h3>No results yet.</h3><p>Try another query or check your Soulseek connection.</p></div>':'');
}
async function pollSearch(){
 const id=state.searchId;if(!id)return;
 try{const data=await api(`/soulseek/searches/${id}`);if(id!==state.searchId)return;state.groups=normalizeResults(data.responses);state.searchDone=Boolean(data.search.isComplete)||String(data.search.state||'').includes('Completed');if(state.searchDone)clearInterval(state.searchTimer);if(state.page==='soulseek')renderSearchResults();}
 catch(error){clearInterval(state.searchTimer);state.searchDone=true;toast(error.message,true);}
}
async function downloadFiles(group,files){await api('/soulseek/downloads',{method:'POST',body:{username:group.username,files:files.map(f=>({filename:f.filename,size:f.size}))}});toast(`${files.length} files queued. See Downloads for progress.`);}
async function loadTransfers(){
 const data=await api('/soulseek/downloads');if(state.page!=='transfers')return;
 const transfers=[];
 for(const user of Array.isArray(data)?data:[])for(const directory of user.directories||[])for(const file of directory.files||[])transfers.push({...file,username:user.username});
 document.querySelector('#content').innerHTML=heading('04','Downloads','Download queue',`<button data-nav="inbox">${icon('inbox')}Inbox</button>`)+(transfers.length?`<div class="table-wrap">${transfers.map(file=>{const progress=file.size?Math.max(0,Math.min(100,(file.bytesTransferred||0)/file.size*100)):0;return `<div class="transfer"><div><h3>${esc(basename(file.filename))}</h3><p>${esc(file.username)} · ${size(file.size)} · ${esc(file.state)}</p></div><div><progress max="100" value="${progress}"></progress><span class="progress-label">${progress.toFixed(0)}%${file.averageSpeed?` · ${size(file.averageSpeed)}/s`:''}</span></div>${!String(file.state).includes('Completed')?`<button class="quiet small" data-cancel-user="${esc(file.username)}" data-cancel-id="${esc(file.id)}">Cancel</button>`:'<span class="badge">COMPLETED</span>'}</div>`;}).join('')}</div>`:`<div class="empty">${icon('download')}<h3>No downloads</h3><p>Download music from Soulseek to see it here.</p><button class="primary" data-nav="soulseek">Search music ${icon('arrow')}</button></div>`);
}
function matchDialog(){
 const entry=state.selected;if(!entry?.directory||root()!=='inbox')return;state.matchPath=entry.path;state.matchGeneration=(state.matchGeneration||0)+1;
 showModal('Match album',`<p>${esc(entry.name)}</p><form id="release-form"><div class="match-fields"><div><label for="match-artist">Album artist</label><input id="match-artist" name="artist" maxlength="300" placeholder="Read from tags"></div><div><label for="match-album">Album</label><input id="match-album" name="album" maxlength="300" placeholder="Read from tags"></div></div><button class="primary" type="submit">${icon('search')}Find matches</button></form><div id="release-results"></div><details class="manual-release"><summary>Use a MusicBrainz release ID</summary><form id="mbid-form" class="search-form"><input name="mbid" aria-label="MusicBrainz release ID" placeholder="Release MBID or URL" required><button type="submit">Preview</button></form></details>`);searchReleases();
}
async function searchReleases(artist,album){
 const el=document.querySelector('#release-results'),generation=++state.matchGeneration;el.innerHTML='<div class="loading"><span class="spinner"></span>Matching tags and tracks…</div>';
 try{const result=await api('/musicbrainz/match',{method:'POST',body:{path:state.matchPath,...(artist===undefined?{}:{artist,album})}});
  if(generation!==state.matchGeneration||!el.isConnected||!modal.open)return;
  if(artist===undefined){const a=document.querySelector('#match-artist'),b=document.querySelector('#match-album');if(a&&!a.value)a.value=result.artist;if(b&&!b.value)b.value=result.album;}
  el.innerHTML=`<p class="field-help">${result.tracks} local tracks · ${result.has_tags?'Uses embedded tags, track names and durations.':'No album tags found. Confirm the artist and album above.'}</p>`+(result.candidates.length?`<div class="release-list">${result.candidates.map(release=>`<article class="release-card"><div><h3>${esc(release.artist)} — ${esc(release.title)}</h3><p>${[release.date,release.country,release.format,`${release.tracks} tracks`,release.disambiguation].filter(Boolean).map(esc).join(' · ')}</p>${release.extra_files||release.missing_tracks?`<p class="warning-text">${release.extra_files} extra files · ${release.missing_tracks} missing tracks</p>`:''}</div><div class="release-actions"><span class="match-score">${release.confidence}%</span><a href="https://musicbrainz.org/release/${esc(release.id)}" target="_blank" rel="noreferrer" aria-label="Open release in MusicBrainz">${icon('external')}</a><button class="small" data-release="${esc(release.id)}">Preview</button></div></article>`).join('')}</div>`:'<p class="search-help">No matches. Check the artist and album, or use a release MBID.</p>');
 }catch(error){if(generation===state.matchGeneration&&el.isConnected)el.innerHTML=`<div class="error-box">${esc(error.message)}</div>`;}
}
async function makePreview(releaseId){
 const job=await api('/imports/preview',{method:'POST',body:{path:state.matchPath,release_id:releaseId}});
 showModal('Preparing preview',`<div class="loading"><span class="spinner"></span>Matching tracks and preparing tags…</div><p>You can close this window. Find the preview in Tasks.</p>`);
 watchJob(job.id);
}
async function watchJob(id){
 let job;
 try{job=await api(`/jobs/${id}`);}catch(error){toast(error.message,true);return;}
 if(job.status==='queued'||job.status==='running'){setTimeout(()=>watchJob(id),1800);return;}
 if(job.status==='ready'){if(modal.open)renderPlan(job);else toast('Preview ready. Open it in Tasks.');}
 else if(job.status==='done'){if(modal.open)modal.close();toast(`Album imported: ${job.result.files} files. Original files remain in Inbox.`);if(['inbox','library'].includes(state.page))loadFiles().catch(()=>{});}
 else{if(modal.open)showModal('Review needed',`<div class="error-box">${esc(job.error||'Task interrupted')}</div>`);else toast(job.error||'Task interrupted',true);}
 if(state.page==='jobs')loadJobs().catch(()=>{});
}
function renderPlan(job){
 const plan=job.result, weak=plan.confidence<90;
 showModal('Import preview',`<div class="plan-banner${weak?' warning':''}"><strong>${esc(plan.artist)} — ${esc(plan.album)}</strong><br>Match: ${plan.confidence}%. ${weak?'Check the track mapping carefully.':'Review the track mapping.'}<br>Create ${plan.rows.length} copies in Library. Original files remain in Inbox.</div>${plan.rows.map(row=>`<div class="plan-track"><span class="muted">${esc(row.source)}</span><div class="tag-diff"><dl>${diffTags(row.before)}</dl><div class="tag-arrow">${icon('arrow')}</div><dl>${diffTags(row.after)}</dl></div><code>${esc(row.destination)}</code></div>`).join('')}<p class="field-help">Preview valid for one hour. Artwork and non-audio files are not copied automatically.</p>`,`<button data-action="close-modal">Back</button><button class="primary" data-apply-plan="${esc(job.id)}">${icon('check')}Import album</button>`);
}
function diffTags(tags){return [['Track',tags.title],['Artist',tags.artist],['Album',tags.album],['Year',tags.year||'—'],['Track number',`${tags.disc||1}.${tags.track||'—'} / ${tags.tracktotal||'—'}`]].map(([key,value])=>`<dt>${key}</dt><dd>${esc(value||'—')}</dd>`).join('');}
const statusLabels={queued:'Queued',running:'Running',ready:'Ready for review',done:'Done',applied:'Applied',applying:'Importing',failed:'Error',interrupted:'Interrupted'};
async function loadJobs(){
 const jobs=await api('/jobs');state.jobs=jobs;if(state.page!=='jobs')return;
 document.querySelector('#content').innerHTML=heading('05','Tasks','Previews and imports')+(jobs.length?`<div class="jobs-list">${jobs.map(job=>`<article class="job-card"><div><h3>${job.kind==='preview'?'Match and preview':'Import to Library'}${job.result?.album?` · ${esc(job.result.artist)} — ${esc(job.result.album)}`:''}</h3><p>${date(job.created)}${job.error?`<br>${esc(job.error)}`:''}${job.result?.files?` · ${job.result.files} files copied`:''}</p></div><div class="job-actions"><span class="badge${['failed','interrupted'].includes(job.status)?' warm':''}">${esc(statusLabels[job.status]||job.status)}</span>${job.kind==='preview'&&job.status==='ready'?`<button class="small primary" data-show-plan="${esc(job.id)}">Check</button>`:''}</div></article>`).join('')}</div>`:`<div class="empty">${icon('jobs')}<h3>No tasks</h3><p>Match an album in Inbox to create a preview.</p></div>`);
}
async function loadHistory(){
 const events=await api('/history');if(state.page!=='history')return;
 document.querySelector('#content').innerHTML=heading('06','History','Recent operations')+(events.length?`<div class="jobs-list">${events.map(event=>{const d=event.data;const titles={move:'Move / rename',mkdir:'Create folder',delete:'Delete',import:'Import album',download:'Soulseek download'};const detail=event.kind==='move'?`${d.source} → ${d.destination}`:['mkdir','delete'].includes(event.kind)?d.path:event.kind==='import'?`${d.artist} — ${d.album} · ${d.files} files`:`${d.username} · ${d.files} files`;return `<article class="job-card"><div><h3>${titles[event.kind]||esc(event.kind)}</h3><p>${esc(detail)}<br>${date(event.created)}${d.root?` · ${d.root==='inbox'?'Inbox':'Library'}`:''}</p></div>${event.undone?'<span class="badge">Undone</span>':['move','mkdir'].includes(event.kind)?`<button class="small" data-undo="${event.id}">${icon('history')}Undo</button>`:''}</article>`;}).join('')}</div>`:`<div class="empty">${icon('history')}<h3>No history</h3><p>File operations will appear here.</p></div>`);
}
function renderSettings(){
 const system=state.system;
 document.querySelector('#content').innerHTML=heading('07','Settings','Accounts and services',`<button data-action="refresh-system">${icon('refresh')}Check</button>`)+`
 <section id="soulseek-account" class="account-panel" aria-label="Soulseek account"></section>
 <div class="connection-grid"><article class="connection">${icon('search')}<h2>slskd</h2><p>Search and downloads</p><span class="badge${system?.slskd?'':' warm'}">${system?.slskd?'API available':'API unavailable'}</span></article><article class="connection">${icon('disc')}<h2>beets + MusicBrainz</h2><p>Album matching and tagging</p><span class="badge">beets installed</span></article><article class="connection">${icon('music')}<h2>Navidrome</h2><p>Point Navidrome at the Library folder.</p>${system?.navidrome_url?`<a href="${esc(system.navidrome_url)}" target="_blank" rel="noreferrer">Open Navidrome ↗</a>`:'<p>Set NAVIDROME_URL to add a shortcut.</p>'}</article></div>`;
 renderAccountPanel();
}
function renderAccountPanel(){
 const el=document.querySelector('#soulseek-account');if(!el)return;
 const account=state.account;
 if(!account){el.innerHTML='<div class="loading"><span class="spinner"></span>Checking Soulseek…</div>';return;}
 const online=account.logged_in, register=state.authMode==='register';
 el.innerHTML=`<div class="section-heading"><h2>Soulseek · Account</h2><button class="quiet small" data-action="soulseek-refresh">${icon('refresh')}Refresh status</button></div><div class="account-status"><span class="badge${online?'':' warm'}">${online?'Signed in':account.busy||account.transitioning?'Connecting…':account.api_reachable?'Not signed in to Soulseek':'slskd unavailable'}</span>${online?`<span>Signed in as <strong>${esc(account.username)}</strong></span>`:''}</div>
 ${account.error?`<div class="error-box"><strong>${esc(account.error)}</strong>${account.failure?`<p class="field-help">Code: ${esc(account.failure.code)} · Stage: ${esc(({login:'Soulseek login',proxy:'Proxy',slskd_api:'API slskd',network:'Network',configuration:'Configuration'})[account.failure.stage]||account.failure.stage)}</p><p>${esc(account.failure.hint||'')}</p>`:''}</div>`:''}
 <p class="field-help">${account.proxy?.error?`Proxy: ${esc(account.proxy.error.message)} ${esc(account.proxy.error.hint)}`:account.proxy?.enabled?`SOCKS5 proxy: ${esc(account.proxy.host)}:${esc(account.proxy.port)} · Soulseek and MusicBrainz`:'Connection: direct'}</p>
 ${online?`<p class="field-help">${account.saved?'Reconnect automatically after a restart.':'Sign in again after a restart.'}</p><div class="account-actions"><button data-action="soulseek-logout">${icon('logout')}Sign out of Soulseek</button>${account.saved?'<button class="quiet" data-action="soulseek-forget">Forget account</button>':''}</div>`:!account.managed?'<p class="field-help">Managed login requires the supplied Compose configuration. See the README for external slskd setup.</p>':`
 <div class="tabbar"><button class="${register?'':'active'}" data-action="soulseek-auth-login">Sign in</button><button class="${register?'active':''}" data-action="soulseek-auth-register">Create account</button></div>
 <p class="field-help">${register?'Choose an available username and a password.':'Use your Soulseek network account.'}</p>
 <form id="soulseek-account-form" data-mode="${register?'register':'login'}" class="account-form"><div><label for="soulseek-username">Soulseek username</label><input id="soulseek-username" name="username" autocomplete="username" required maxlength="255"></div><div><label for="soulseek-password">Password</label><input id="soulseek-password" name="soulseek_password" type="password" autocomplete="${register?'new-password':'current-password'}" required maxlength="512"></div>${register?'<div><label for="soulseek-confirm">Confirm password</label><input id="soulseek-confirm" name="password_confirm" type="password" autocomplete="new-password" required maxlength="512"></div>':''}<label class="checkbox-label"><input type="checkbox" name="remember" checked>Remember account</label><p class="login-error account-full" id="soulseek-account-error" role="status" aria-live="polite"></p><div class="account-actions account-full"><button class="primary" type="submit" ${account.busy?'disabled':''}>${icon('arrow')}${register?'Create account':'Sign in'}</button>${account.saved?'<button type="button" data-action="soulseek-reconnect">Reconnect</button><button type="button" class="quiet" data-action="soulseek-forget">Forget account</button>':''}</div></form>`}`;
}
function playFile(path){
 const entry=state.entries.find(e=>e.path===path);state.player={path,root:root()};
 document.querySelector('#player-slot').innerHTML=`<section class="player" aria-label="Player"><div class="player-title">${icon('disc')}<div><strong>${esc(entry?.name||basename(path))}</strong><small>Library audio</small></div></div><audio id="audio-player" controls autoplay src="${esc(mediaURL(path))}"></audio><button class="quiet icon-button" data-action="close-player" aria-label="Close player">${icon('close')}</button></section>`;
 const player=document.querySelector('#audio-player');player.addEventListener('error',()=>toast('Your browser cannot play this audio format.',true));player.play().catch(()=>{});
}
document.addEventListener('input',event=>{if(event.target.id==='file-filter'){state.filter=event.target.value;renderFileList();}});
document.addEventListener('dblclick',event=>{const row=event.target.closest('[data-select]');if(!row)return;const entry=state.entries.find(e=>e.path===row.dataset.select);if(entry?.directory)openFolder(entry.path).catch(error=>toast(error.message,true));else if(entry?.audio)playFile(entry.path);});
document.addEventListener('keydown',event=>{if(event.key==='Enter'&&event.target.matches('tr[data-select]')){selectFile(event.target.dataset.select);}});
async function openMovePicker(path){
 const source=state.moveSource;if(!source)return;
 const generation=state.pickerGeneration=(state.pickerGeneration||0)+1;
 const data=await api(`/files?${query({root:source.root,path})}`);
 if(generation!==state.pickerGeneration)return;
 state.moveDestination=data.path==='.'?'':data.path;
 const destination=state.moveDestination,chunks=destination?destination.split('/'):[];
 const crumbs=`<button class="quiet" data-picker-path="">${source.root==='inbox'?'Inbox':'Library'}</button>`+chunks.map((name,i)=>`<span>/</span><button class="quiet" data-picker-path="${esc(chunks.slice(0,i+1).join('/'))}">${esc(name)}</button>`).join('');
 const dirs=data.entries.filter(entry=>entry.directory&&entry.path!==source.path&&!entry.path.startsWith(source.path+'/'));
 const valid=join(destination,source.name)!==source.path&&!(source.directory&&(destination===source.path||destination.startsWith(source.path+'/')));
 showModal('Move '+esc(source.name),`<div class="breadcrumbs picker-breadcrumbs">${crumbs}</div><div class="folder-picker">${destination?`<button data-picker-path="${esc(chunks.slice(0,-1).join('/'))}">${icon('arrow')}<span>Parent folder</span></button>`:''}${dirs.map(entry=>`<button data-picker-path="${esc(entry.path)}">${icon('folder')}<span>${esc(entry.name)}</span>${icon('arrow')}</button>`).join('')}${!dirs.length?'<p class="field-help">No subfolders</p>':''}</div>`,`<button data-action="close-modal">Cancel</button><button class="primary" data-action="confirm-move" ${valid?'':'disabled'}>Move here</button>`);
}
async function confirmMove(button){
 const source=state.moveSource,destination=join(state.moveDestination,source.name);button.disabled=true;
 await api('/move',{method:'POST',body:{root:source.root,source:source.path,destination}});
 modal.close();state.selected=null;await loadFiles();toast('Moved '+source.name);
}
function deleteDialog(){
 if(!state.selected)return;state.deleteSource={...state.selected,root:root()};const entry=state.deleteSource;
 showModal('Delete '+(entry.directory?'folder':'file')+'?',`<p><strong>${esc(entry.name)}</strong></p><p>${entry.directory?'The folder and everything inside it will be permanently deleted.':'This file will be permanently deleted.'} This cannot be undone.</p>`,`<button data-action="close-modal">Cancel</button><button class="danger" data-action="confirm-delete">${icon('trash')}Delete</button>`);
}
async function confirmDelete(button){
 const entry=state.deleteSource;button.disabled=true;
 await api('/files',{method:'DELETE',body:{root:entry.root,path:entry.path}});
 modal.close();state.selected=null;await loadFiles();toast('Deleted '+entry.name);
}
function cancelDragHover(){clearTimeout(state.dragHoverTimer);state.dragHoverKey=null;document.querySelectorAll('.drop-target').forEach(el=>el.classList.remove('drop-target'));}
function finishDrag(render=true){
 cancelDragHover();state.drag=null;document.body.classList.remove('is-dragging');
 if(render&&['library','inbox'].includes(state.page))renderFiles();
}
function dragCanVisit(path){const source=state.drag;return source&&root()===source.root&&path!==source.path&&!(source.directory&&path.startsWith(source.path+'/'));}
function validDrop(path){return dragCanVisit(path)&&join(path,state.drag.name)!==state.drag.path;}
document.addEventListener('dragstart',event=>{
 const element=event.target.closest('[data-select][draggable="true"]');if(!element||!['library','inbox'].includes(state.page))return;
 const entry=state.entries.find(item=>item.path===element.dataset.select);if(!entry)return;
 state.drag={...entry,root:root()};event.dataTransfer.effectAllowed='move';
 event.dataTransfer.setData('application/x-meandmymusic',JSON.stringify({root:root(),path:entry.path}));event.dataTransfer.setData('text/plain',entry.name);
 document.body.classList.add('is-dragging');
});
document.addEventListener('dragover',event=>{
 if(!state.drag)return;const target=event.target.closest('[data-drop-path]');
 if(!target||!dragCanVisit(target.dataset.dropPath)){cancelDragHover();return;}
 event.preventDefault();event.dataTransfer.dropEffect=validDrop(target.dataset.dropPath)?'move':'none';
 const path=target.dataset.dropPath,key=path;
 if(state.dragHoverKey===key)return;
 cancelDragHover();state.dragHoverKey=key;target.classList.add('drop-target');
 if(path!==state.path){state.dragHoverTimer=setTimeout(()=>{
  if(state.drag&&state.dragHoverKey===key){cancelDragHover();openFolder(path).catch(error=>{toast(error.message,true);finishDrag();});}
 },650);}
});
document.addEventListener('dragleave',event=>{
 if(!state.drag)return;const target=event.target.closest('[data-drop-path]');
 if(!target||target.dataset.dropPath!==state.dragHoverKey)return;
 const next=document.elementFromPoint?.(event.clientX,event.clientY)?.closest('[data-drop-path]');
 if(next?.dataset.dropPath===state.dragHoverKey)return;
 if(!event.relatedTarget||!target.contains(event.relatedTarget))cancelDragHover();
});
document.addEventListener('drop',async event=>{
 if(!state.drag)return;const target=event.target.closest('[data-drop-path]');event.preventDefault();
 if(!target||!validDrop(target.dataset.dropPath)){finishDrag();return;}
 const source=state.drag,destination=join(target.dataset.dropPath,source.name);finishDrag(false);
 try{toast('Moving '+source.name+'…');await api('/move',{method:'POST',body:{root:source.root,source:source.path,destination}});state.selected=null;await loadFiles();toast('Moved '+source.name);}
 catch(error){renderFiles();toast(error.message,true);}
});
document.addEventListener('dragend',()=>{if(state.drag)finishDrag();});
document.addEventListener('change',event=>{
 if(event.target.matches('[data-theme-picker]')){const value=event.target.value;if(!themes[value])return;applyMusicTheme(value);try{localStorage.setItem('mmm-theme',value);}catch{}document.querySelectorAll('[data-theme-picker]').forEach(select=>select.value=value);}
});
document.addEventListener('keydown',event=>{
 if(modal.open||event.target.matches('input,textarea,select'))return;
 if(event.key==='Delete'&&state.selected&&['library','inbox'].includes(state.page)){event.preventDefault();deleteDialog();}
});
document.addEventListener('click',async event=>{
 const button=event.target.closest('button,[data-select]');if(!button)return;
 try{
  if(button.hasAttribute('data-picker-path')){await openMovePicker(button.dataset.pickerPath);return;}
  if(button.dataset.nav){await navigate(button.dataset.nav);return;}
  if(button.hasAttribute('data-path')){await openFolder(button.dataset.path);return;}
  if(button.dataset.open){await openFolder(button.dataset.open);return;}
  if(button.dataset.play){playFile(button.dataset.play);return;}
  if(button.dataset.select){selectFile(button.dataset.select);return;}
  if(button.dataset.downloadGroup){button.disabled=true;const group=state.groups[Number(button.dataset.downloadGroup)];await downloadFiles(group,group.files);return;}
  if(button.dataset.downloadFile){button.disabled=true;const [g,f]=button.dataset.downloadFile.split(':').map(Number);await downloadFiles(state.groups[g],[state.groups[g].files[f]]);return;}
  if(button.dataset.cancelId){await api(`/soulseek/downloads/${encodeURIComponent(button.dataset.cancelUser)}/${button.dataset.cancelId}`,{method:'DELETE'});await loadTransfers();return;}
  if(button.dataset.release){button.disabled=true;await makePreview(button.dataset.release);return;}
  if(button.dataset.applyPlan){button.disabled=true;const job=await api(`/imports/${button.dataset.applyPlan}/apply`,{method:'POST'});showModal('Importing album',`<div class="loading"><span class="spinner"></span>Writing tags and copying files…</div><p>You can close this window and follow progress in Tasks.</p>`);watchJob(job.id);return;}
  if(button.dataset.showPlan){const job=await api(`/jobs/${button.dataset.showPlan}`);if(job.status==='ready')renderPlan(job);else toast('This preview was already applied or has expired.',true);return;}
  if(button.dataset.undo){button.disabled=true;await api(`/history/${button.dataset.undo}/undo`,{method:'POST'});toast('Operation undone');await loadHistory();return;}
  const action=button.dataset.action;
  if(['new-folder','rename','move'].includes(action))fileDialog(action);
  else if(action==='delete')deleteDialog();
  else if(action==='confirm-delete')await confirmDelete(button);
  else if(action==='confirm-move')await confirmMove(button);
  else if(action==='deselect'){state.selected=null;renderFileActions();renderInspector();document.querySelectorAll('.selected').forEach(el=>el.classList.remove('selected'));}
  else if(action==='match')matchDialog();
  else if(action==='close-modal')modal.close();
  else if(action==='reload')await navigate(state.page);
  else if(action==='view-grid'||action==='view-list'){state.view=action==='view-grid'?'grid':'list';renderFileList();}
  else if(action==='logout'){await api('/logout',{method:'POST'});showLogin();}
  else if(action==='close-player'){document.querySelector('#player-slot').innerHTML='';state.player=null;}
  else if(action==='refresh-system'){button.disabled=true;state.system=await api('/system');await refreshAccount();updateStatus();renderSettings();}
  else if(action==='soulseek-auth-login'||action==='soulseek-auth-register'){state.authMode=action.endsWith('register')?'register':'login';renderAccountPanel();}
  else if(action==='soulseek-refresh'){await refreshAccount();}
  else if(action==='soulseek-logout'){button.disabled=true;await api('/soulseek/auth/logout',{method:'POST'});await refreshAccount();toast('Signed out of Soulseek.');}
  else if(action==='soulseek-forget'){button.disabled=true;await api('/soulseek/auth/saved',{method:'DELETE'});await refreshAccount();toast('Saved account removed.');}
  else if(action==='soulseek-reconnect'){button.disabled=true;state.accountBusy=true;try{await api('/soulseek/auth/reconnect',{method:'POST'});}finally{state.accountBusy=false;await refreshAccount();}}
  else if(action==='stop-search'){clearInterval(state.searchTimer);await api(`/soulseek/searches/${state.searchId}`,{method:'DELETE'});state.searchDone=true;renderSoulseek();}
 }catch(error){toast(error.message,true);}finally{if(button.isConnected)button.disabled=false;}
});
document.addEventListener('submit',async event=>{
 event.preventDefault();const form=event.target;const values=new FormData(form);const submit=document.querySelector(`[form="${form.id}"]`)||form.querySelector('button[type="submit"]');if(submit)submit.disabled=true;
 try{
  if(form.id==='login-form'){await api('/login',{method:'POST',body:{password:values.get('password')}});shell();}
  else if(form.id==='soulseek-account-form'){
   const mode=form.dataset.mode;const body={username:String(values.get('username')).trim(),password:String(values.get('soulseek_password')),remember:values.get('remember')==='on'};
   if(mode==='register'){body.password_confirm=String(values.get('password_confirm'));if(body.password!==body.password_confirm)throw new Error('Passwords do not match');}
   state.accountBusy=true;document.querySelector('#soulseek-account-error').textContent='Connecting to Soulseek. This may take up to a minute…';
   try{const result=await api(`/soulseek/auth/${mode}`,{method:'POST',body});if(result.logged_in!==true)throw new Error('The server did not confirm login');toast(result.message||'Soulseek login confirmed.');}
   finally{state.accountBusy=false;form.querySelectorAll('input[type="password"]').forEach(input=>input.value='');await refreshAccount();}
  }
  else if(form.id==='file-operation'){
   const value=String(values.get('value')).trim(),mode=form.dataset.mode;
   if(!value)throw new Error('Enter a name');
   if(mode!=='move'&&(value.includes('/')||value.includes('\\')||value==='.'||value==='..'))throw new Error('A name cannot contain path separators');
   if(mode==='new-folder')await api('/folders',{method:'POST',body:{root:root(),path:join(state.path,value)}});
   else await api('/move',{method:'POST',body:{root:root(),source:state.selected.path,destination:mode==='rename'?join(state.path,value):value}});
   modal.close();state.selected=null;await loadFiles();toast('Done.');
  }
  else if(form.id==='soulseek-form'){
   clearInterval(state.searchTimer);if(state.searchId&&!state.searchDone)await api(`/soulseek/searches/${state.searchId}`,{method:'DELETE'}).catch(()=>{});
   state.searchQuery=String(values.get('query')).trim();const result=await api('/soulseek/searches',{method:'POST',body:{query:state.searchQuery}});
   state.searchId=result.id;state.groups=[];state.searchDone=false;
   if(!state.searchId)throw new Error('slskd did not return a search ID');
   renderSoulseek();await pollSearch();if(!state.searchDone)state.searchTimer=setInterval(pollSearch,2000);
  }
  else if(form.id==='release-form')await searchReleases(String(values.get('artist')).trim(),String(values.get('album')).trim());
  else if(form.id==='mbid-form'){const match=String(values.get('mbid')).match(/(?:^|release\/)([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})(?:$|[?#/])/i);if(!match)throw new Error('Enter a valid release MBID or MusicBrainz release URL');await makePreview(match[1]);}
 }catch(error){if(form.id==='login-form'){const el=document.querySelector('#login-error');if(el)el.textContent=error.message;}else if(form.id==='soulseek-account-form'){const el=document.querySelector('#soulseek-account-error');if(el)el.textContent=error.message+(error.connection?` [${error.connection.code}] ${error.connection.hint||''}`:'');toast(error.message,true);}else if(form.id==='file-operation'){const el=document.querySelector('#file-operation-error');if(el)el.textContent=error.message;}else toast(error.message,true);}
 finally{if(submit?.isConnected)submit.disabled=false;}
});
api('/session').then(shell).catch(()=>showLogin());
