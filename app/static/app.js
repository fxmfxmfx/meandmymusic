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
 file:'<path d="M5 3h9l5 5v13H5Zm9 0v6h5M8 14h8M8 17h5"/>'
};
const icon = name => `<svg class="icon" viewBox="0 0 24 24" aria-hidden="true">${paths[name] || paths.file}</svg>`;
const esc = value => String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
const size = bytes => bytes == null ? '—' : bytes < 1024 ? `${bytes} Б` : bytes < 1048576 ? `${(bytes/1024).toFixed(0)} КБ` : bytes < 1073741824 ? `${(bytes/1048576).toFixed(1)} МБ` : `${(bytes/1073741824).toFixed(1)} ГБ`;
const date = value => new Date(value*1000).toLocaleString('ru-RU',{day:'numeric',month:'short',hour:'2-digit',minute:'2-digit'});
const color = name => [...name].reduce((n,c)=>n+c.charCodeAt(0),0)%5;
const join = (a,b) => a && a !== '.' ? `${a}/${b}` : b;
const basename = path => path.split(/[\\/]/).pop();
const root = () => state.page === 'inbox' ? 'inbox' : 'library';
const query = values => new URLSearchParams(values).toString();
const mediaURL = (path, r=root()) => `/api/media?${query({root:r,path})}`;
let toastTimeout;
function toast(message,error=false){const el=document.querySelector('#toast');el.textContent=message;el.className=`visible${error?' error':''}`;clearTimeout(toastTimeout);toastTimeout=setTimeout(()=>el.className='',6500);}
async function api(url, options={}) {
 const response=await fetch(`/api${url}`,{...options,headers:{'Content-Type':'application/json','X-Requested-With':'meandmymusic',...options.headers},body:options.body===undefined?undefined:JSON.stringify(options.body)});
 const data=await response.json().catch(()=>({detail:`Ошибка HTTP ${response.status}`}));
 if(!response.ok){if(response.status===401 && url!=='/login')showLogin();const error=new Error(typeof data.detail==='string'?data.detail:JSON.stringify(data.detail));error.connection=data.error;throw error;}
 return data;
}
function brand(){return `<div class="brand"><div class="brand-mark">${icon('disc')}</div><div class="brand-type">me&my<span>MUSIC MANAGER</span></div></div>`;}
function showLogin(){
 clearInterval(state.searchTimer);clearInterval(state.jobTimer);clearInterval(state.transferTimer);clearInterval(state.accountTimer);if(modal.open)modal.close();
 app.innerHTML=`<main class="login-screen"><section class="login-story">${brand()}<div><div class="vinyl" aria-hidden="true"></div><h1>Твоя музыка.<br><span>Твои правила.</span></h1><p>От первого найденного трека до идеально собранного архива. Всё на своей стороне.</p></div><footer>SELF-HOSTED. SELF-CURATED. ALWAYS YOURS.</footer></section><section class="login-form-wrap"><form class="login-form" id="login-form"><div class="eyebrow">Личный музыкальный архив</div><h2>С возвращением.</h2><p>Твоя коллекция ждёт. Введи пароль, заданный при настройке сервера.</p><label for="password">Пароль</label><input id="password" name="password" type="password" autocomplete="current-password" required autofocus placeholder="Пароль от твоего архива"><button class="primary" type="submit">Открыть архив ${icon('arrow')}</button><p class="login-error" id="login-error" role="alert"></p></form></section></main>`;
}
function shell(){
 app.innerHTML=`<div class="shell"><aside class="sidebar">${brand()}<div class="nav-label">Твоё пространство</div><nav class="nav" aria-label="Основная навигация">${[['library','folder','Архив'],['inbox','inbox','Входящие'],['soulseek','search','Soulseek'],['transfers','download','Загрузки']].map(([id,i,title])=>`<button data-nav="${id}">${icon(i)}${title}</button>`).join('')}<div class="nav-separator"></div>${[['jobs','jobs','Задания'],['history','history','История'],['settings','settings','Подключения']].map(([id,i,title])=>`<button data-nav="${id}">${icon(i)}${title}</button>`).join('')}</nav><div class="sidebar-bottom"><div class="own-your-music">${icon('disc')}<p><strong>Собирай то, что любишь.</strong>Остальное мы поможем разложить по полочкам.</p></div><div class="version"><span>me&my music</span><span>v0.3.0 · alpha</span></div></div></aside><main class="main"><header class="topbar"><div class="topbar-context">Личное пространство <span>/</span> <strong id="context-title">Архив</strong></div><div class="status-group"><span class="status-pill" id="slskd-status"><i class="dot off"></i> slskd</span><span class="status-pill"><i class="dot"></i> beets</span><button class="quiet icon-button" data-action="logout" title="Выйти" aria-label="Выйти">${icon('logout')}</button></div></header><div id="content"></div></main></div><div id="player-slot"></div>`;
 navigate(state.page);
 api('/system').then(system=>{state.system=system;updateStatus();if(state.page==='settings')renderSettings();}).catch(error=>toast(error.message,true));
 refreshAccount().catch(()=>{});
 clearInterval(state.accountTimer);state.accountTimer=setInterval(()=>refreshAccount(false).catch(()=>{}),10000);
}
function updateStatus(){const el=document.querySelector('#slskd-status');if(el)el.innerHTML=`<i class="dot${state.account?.logged_in?'':' off'}"></i> ${state.account?.logged_in?'Soulseek подключён':state.system?.slskd?'Soulseek: нет входа':'slskd недоступен'}`;}
async function refreshAccount(render=true){
 if(state.accountBusy)return;
 state.account=await api('/soulseek/auth/status');updateStatus();
 if(state.page==='settings'&&(render||!document.querySelector('#soulseek-account-form')?.contains(document.activeElement)))renderAccountPanel();
}
function heading(number,title,subtitle,actions=''){return `<section class="page-heading"><div><div class="eyebrow">Твоя коллекция / ${number}</div><h1>${title}</h1><p class="subtitle">${subtitle}</p></div>${actions?`<div class="heading-actions">${actions}</div>`:''}</section>`;}
async function navigate(page){
 state.generation++;const generation=state.generation;
 clearInterval(state.transferTimer);clearInterval(state.jobTimer);
 if(state.page!==page){state.path='';state.selected=null;state.filter='';}
 state.page=page;
 document.querySelectorAll('[data-nav]').forEach(button=>button.classList.toggle('active',button.dataset.nav===page));
 const titles={library:'Архив',inbox:'Входящие',soulseek:'Soulseek',transfers:'Загрузки',jobs:'Задания',history:'История',settings:'Подключения'};
 document.querySelector('#context-title').textContent=titles[page];
 const content=document.querySelector('#content');content.innerHTML='<div class="loading"><span class="spinner"></span>Открываем пространство…</div>';
 try{
  if(page==='library'||page==='inbox')await loadFiles();
  else if(page==='soulseek')renderSoulseek();
  else if(page==='transfers'){await loadTransfers();state.transferTimer=setInterval(()=>loadTransfers().catch(()=>{}),4000);}
  else if(page==='jobs'){await loadJobs();state.jobTimer=setInterval(()=>loadJobs().catch(()=>{}),2500);}
  else if(page==='history')await loadHistory();
  else if(page==='settings')renderSettings();
 }catch(error){if(generation===state.generation && document.querySelector('#content'))content.innerHTML=`<div class="error-box">${esc(error.message)}<br><button class="small" data-action="reload">${icon('refresh')} Повторить</button></div>`;}
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
 document.querySelector('#content').innerHTML=heading(inbox?'02':'01',inbox?'Сначала — во входящие.':'Место для твоей музыки.',inbox?'Загруженные альбомы. Проверь издание и подготовь их к жизни в архиве.':'Папки, треки и любимые находки. Всё под твоим контролем.',`<button data-action="reload" class="icon-button" aria-label="Обновить" title="Обновить">${icon('refresh')}</button><button class="primary" data-action="new-folder">${icon('plus')} Новая папка</button>`)+
 (!state.path?`<section class="hero"><div><div class="eyebrow">${inbox?'Загрузка → проверка → архив':'Меньше хаоса. Больше музыки.'}</div><h2>${inbox?'Хорошему альбому — правильное место.':'Коллекция, собранная тобой.'}</h2><p>${inbox?'Открой папку альбома, выбери релиз MusicBrainz и посмотри изменения. beets сохранит готовую копию в архив.':'Находи музыку в Soulseek, приводи теги в порядок и раскладывай альбомы так, как нравится тебе.'}</p></div><div class="vinyl" aria-hidden="true"></div></section>`:'')+
 `<div class="workspace"><section class="browser"><div class="browser-toolbar"><div class="breadcrumbs">${breadcrumbs()}</div><input class="filter-input" id="file-filter" aria-label="Фильтр файлов" placeholder="Найти в этой папке…" value="${esc(state.filter)}"></div><div id="file-list"></div><div class="footer-line"><span>${state.entries.filter(e=>e.directory).length} папок · ${state.entries.filter(e=>!e.directory).length} файлов в этой папке</span><span>${inbox?'Оригиналы остаются на месте':'Твой сервер. Твои файлы.'}</span></div></section><aside class="inspector" id="inspector" aria-label="Свойства файла"></aside></div>`;
 renderFileList();renderInspector();
}
function breadcrumbs(){
 const chunks=state.path?state.path.split('/'):[];
 return `<button class="quiet" data-path="">${icon(root()==='inbox'?'inbox':'folder')}${root()==='inbox'?'Входящие':'Архив'}</button>`+chunks.map((part,i)=>`<span>/</span><button class="quiet" data-path="${esc(chunks.slice(0,i+1).join('/'))}">${esc(part)}</button>`).join('');
}
function renderFileList(){
 const entries=state.entries.filter(e=>e.name.toLowerCase().includes(state.filter.toLowerCase()));
 const dirs=entries.filter(e=>e.directory), files=entries.filter(e=>!e.directory);
 const el=document.querySelector('#file-list');if(!el)return;
 if(!entries.length){el.innerHTML=`<div class="empty">${icon(state.filter?'search':root()==='inbox'?'inbox':'folder')}<h3>${state.filter?'Ничего не нашлось':root()==='inbox'?'Здесь появятся новые находки':'Первый альбом — начало коллекции'}</h3><p>${state.filter?'Попробуй другое название.':root()==='inbox'?'Скачай музыку через Soulseek или положи папку альбома в подключённую директорию входящих.':'Подключи свою музыкальную папку при запуске сервера или импортируй первый альбом из входящих.'}</p>${!state.filter?`<button class="primary" data-nav="${root()==='inbox'?'soulseek':'inbox'}">${icon(root()==='inbox'?'search':'inbox')}${root()==='inbox'?'Искать в Soulseek':'Перейти во входящие'}</button>`:''}</div>`;return;}
 let html='';
 if(dirs.length){html=`<div class="section-heading"><h2>Папки <span> / ${dirs.length}</span></h2><div class="view-switch"><button class="${state.view==='grid'?'active':''}" data-action="view-grid" aria-label="Плитки">${icon('grid')}</button><button class="${state.view==='list'?'active':''}" data-action="view-list" aria-label="Список">${icon('list')}</button></div></div>`;
  if(state.view==='grid')html+=`<div class="folder-grid">${dirs.map(entry=>`<button class="folder-card ${state.selected?.path===entry.path?'selected':''}" data-select="${esc(entry.path)}" title="Нажми для выбора, дважды — открыть"><div class="folder-art color-${color(entry.name)}">${icon('folder')}</div>${state.selected?.path===entry.path?`<span class="selected-mark">${icon('check')}</span>`:''}<div class="folder-name">${esc(entry.name)}</div><div class="folder-meta">Папка · ${date(entry.modified)}</div></button>`).join('')}</div>`;
  else html+=fileTable(dirs);
 }
 if(files.length)html+=`<div class="section-heading"><h2>Файлы <span> / ${files.length}</span></h2></div>${fileTable(files)}`;
 el.innerHTML=html;
}
function fileTable(entries){return `<div class="table-wrap"><table><thead><tr><th>Название</th><th>Формат</th><th>Размер</th><th></th></tr></thead><tbody>${entries.map(entry=>`<tr class="file-row ${state.selected?.path===entry.path?'selected':''}" data-select="${esc(entry.path)}" tabindex="0"><td><div class="file-title">${icon(entry.directory?'folder':entry.audio?'music':'file')}<div>${esc(entry.name)}<small>${date(entry.modified)}</small></div></div></td><td><span class="badge">${entry.directory?'ПАПКА':esc(entry.name.split('.').pop().toUpperCase())}</span></td><td class="muted">${size(entry.size)}</td><td>${entry.directory?`<button class="quiet icon-button" data-open="${esc(entry.path)}" aria-label="Открыть папку">${icon('arrow')}</button>`:entry.audio?`<button class="quiet icon-button" data-play="${esc(entry.path)}" aria-label="Слушать">${icon('play')}</button>`:''}</td></tr>`).join('')}</tbody></table></div>`;}
function renderInspector(){
 const el=document.querySelector('#inspector');if(!el)return;
 const entry=state.selected;
 el.innerHTML=`<div class="inspector-header"><span>О выбранном</span>${icon('settings')}</div>`;
 if(!entry){el.innerHTML+=`<div class="small-empty">${icon('disc')}<p>Выбери папку или трек.<br>Здесь будут свойства и действия.</p></div>`;return;}
 el.innerHTML+=`<div class="inspector-body"><div class="folder-art inspector-art color-${color(entry.name)}">${icon(entry.directory?'folder':entry.audio?'disc':'file')}</div><h3>${esc(entry.name)}</h3><div class="type-label">${entry.directory?'ПАПКА':entry.audio?'АУДИОФАЙЛ':'ФАЙЛ'} · ${root()==='inbox'?'ВХОДЯЩИЕ':'АРХИВ'}</div><div class="detail-row"><span>Изменён</span><strong>${date(entry.modified)}</strong></div>${!entry.directory?`<div class="detail-row"><span>Размер</span><strong>${size(entry.size)}</strong></div>`:''}<div id="audio-metadata"></div><div class="inspector-actions">${entry.directory?`<button class="primary" data-open="${esc(entry.path)}">${icon('folder')}Открыть папку</button>`:entry.audio?`<button class="primary" data-play="${esc(entry.path)}">${icon('play')}Слушать</button>`:''}${entry.directory&&root()==='inbox'?`<button data-action="match">${icon('disc')}Подобрать релиз</button>`:''}<button data-action="rename">${icon('edit')}Переименовать</button><button data-action="move">${icon('move')}Переместить</button></div><p class="inspector-note">${entry.directory&&root()==='inbox'?'Выбери папку одного полного альбома. Перед импортом покажем новые теги и пути.':'Перемещения и переименования записываются в историю. Существующие файлы не перезаписываются.'}</p></div>`;
 if(entry.audio){const selected=entry.path;api(`/metadata?${query({root:root(),path:entry.path})}`).then(tags=>{if(state.selected?.path!==selected)return;const metadata=document.querySelector('#audio-metadata');if(metadata)metadata.innerHTML=[['Исполнитель',tags.artist],['Альбом',tags.album],['Длительность',tags.duration?`${Math.floor(tags.duration/60)}:${String(tags.duration%60).padStart(2,'0')}`:''],['Качество',tags.bitrate?`${tags.bitrate} кбит/с`:'']].filter(([,value])=>value).map(([label,value])=>`<div class="detail-row"><span>${label}</span><strong>${esc(value)}</strong></div>`).join('');}).catch(()=>{});}
}
function selectFile(path){state.selected=state.entries.find(e=>e.path===path);document.querySelectorAll('[data-select]').forEach(el=>el.classList.toggle('selected',el.dataset.select===path));renderInspector();}
async function openFolder(path){state.path=path;state.selected=null;state.filter='';await loadFiles();}
function showModal(title,body,footer=''){modal.innerHTML=`<div class="modal-header"><h2 id="modal-title">${title}</h2><button class="quiet icon-button" data-action="close-modal" aria-label="Закрыть">${icon('close')}</button></div><div class="modal-content">${body}</div>${footer?`<div class="modal-footer">${footer}</div>`:''}`;if(!modal.open)modal.showModal();}
function fileDialog(mode){
 const entry=state.selected;
 if(mode!=='new-folder'&&!entry)return;
 const title=mode==='new-folder'?'Новая папка':mode==='rename'?'Переименовать':'Переместить';
 const value=mode==='new-folder'?'':mode==='rename'?entry.name:entry.path;
 showModal(title,`<form id="file-operation" data-mode="${mode}"><p class="field-help">${mode==='move'?'Укажи полный новый путь относительно текущего раздела, включая имя. Папка назначения должна существовать.':mode==='rename'?'Новое имя файла или папки.':'Папка появится в текущей директории.'}</p><label for="file-value">${mode==='move'?'Новый путь':'Название'}</label><input id="file-value" name="value" value="${esc(value)}" required autocomplete="off" maxlength="4096"><p class="field-help" id="file-operation-error" role="alert"></p></form>`,`<button data-action="close-modal">Отмена</button><button class="primary" type="submit" form="file-operation">${title}</button>`);
 modal.querySelector('input').focus();modal.querySelector('input').select();
}
function renderSoulseek(){
 document.querySelector('#content').innerHTML=heading('03','Следующая любимая находка.','Поиск в сети Soulseek. Загрузки отправятся во входящие.')+`<section class="search-box"><form id="soulseek-form" class="search-form"><label class="visually-hidden" for="soulseek-query">Поиск в Soulseek</label><input id="soulseek-query" name="query" placeholder="Исполнитель, альбом или трек…" value="${esc(state.searchQuery)}" minlength="2" maxlength="300" required><button class="primary" type="submit">${icon('search')}Найти музыку</button>${state.searchId&&!state.searchDone?'<button type="button" data-action="stop-search">Остановить</button>':''}</form><p class="search-help">Результаты сгруппированы по пользователю и папке. Проверь формат и состав альбома перед скачиванием.</p></section><div id="search-results"></div>`;
 renderSearchResults();
}
function normalizeResults(responses){
 const groups=new Map();
 for(const response of Array.isArray(responses)?responses:[]){for(const file of response.files||[]){if(!file.filename)continue;const split=file.filename.lastIndexOf('\\');const folder=split>=0?file.filename.slice(0,split):'';const key=JSON.stringify([response.username,folder]);if(!groups.has(key))groups.set(key,{username:response.username,folder,files:[],speed:response.uploadSpeed,queue:response.queueLength,slot:response.hasFreeUploadSlot});groups.get(key).files.push(file);}}
 return [...groups.values()].sort((a,b)=>Number(b.slot)-Number(a.slot)||(b.speed||0)-(a.speed||0));
}
function renderSearchResults(){
 const el=document.querySelector('#search-results');if(!el)return;
 if(!state.searchId){el.innerHTML=`<div class="empty">${icon('search')}<h3>У музыки нет последней страницы.</h3><p>Начни с названия альбома или исполнителя. slskd найдёт пользователей, у которых есть нужные файлы.</p></div>`;return;}
 el.innerHTML=`<div class="search-summary">${!state.searchDone?'<span class="spinner"></span>Ищем… ':''}${state.groups.length} папок найдено${state.searchDone?' · поиск завершён':''}</div>`+state.groups.slice(0,100).map((group,index)=>`<article class="search-group"><div class="search-group-head"><div><h3>${esc(basename(group.folder)||'Отдельные файлы')}</h3><p>${esc(group.username)} · ${group.files.length} файлов · ${size(group.files.reduce((sum,f)=>sum+f.size,0))} · ${group.slot?'есть свободный слот':`очередь: ${group.queue||0}`} ${group.speed?`· ${size(group.speed)}/с`:''}</p></div><button class="small primary" data-download-group="${index}">${icon('download')}Скачать папку</button></div><details><summary>Показать файлы</summary>${group.files.map((file,fileIndex)=>`<div class="search-file"><span>${esc(basename(file.filename))} <span class="muted">· ${size(file.size)} ${file.bitRate?`· ${file.bitRate} кбит/с`:''}${file.bitDepth?` · ${file.bitDepth} бит / ${file.sampleRate||'?'} Гц`:''}</span></span><button class="quiet icon-button" data-download-file="${index}:${fileIndex}" aria-label="Скачать файл">${icon('download')}</button></div>`).join('')}</details></article>`).join('')+(state.searchDone&&!state.groups.length?'<div class="empty"><h3>Пока ничего не нашлось.</h3><p>Попробуй другое написание. Проверь подключение slskd к сети Soulseek.</p></div>':'');
}
async function pollSearch(){
 const id=state.searchId;if(!id)return;
 try{const data=await api(`/soulseek/searches/${id}`);if(id!==state.searchId)return;state.groups=normalizeResults(data.responses);state.searchDone=Boolean(data.search.isComplete)||String(data.search.state||'').includes('Completed');if(state.searchDone)clearInterval(state.searchTimer);if(state.page==='soulseek')renderSearchResults();}
 catch(error){clearInterval(state.searchTimer);state.searchDone=true;toast(error.message,true);}
}
async function downloadFiles(group,files){await api('/soulseek/downloads',{method:'POST',body:{username:group.username,files:files.map(f=>({filename:f.filename,size:f.size}))}});toast(`${files.length} файлов поставлено в очередь. Статус — в разделе «Загрузки».`);}
async function loadTransfers(){
 const data=await api('/soulseek/downloads');if(state.page!=='transfers')return;
 const transfers=[];
 for(const user of Array.isArray(data)?data:[])for(const directory of user.directories||[])for(const file of directory.files||[])transfers.push({...file,username:user.username});
 document.querySelector('#content').innerHTML=heading('04','На пути в коллекцию.','Очередь скачивания slskd. Прогресс обновляется автоматически.',`<button data-nav="inbox">${icon('inbox')}Входящие</button>`)+(transfers.length?`<div class="table-wrap">${transfers.map(file=>{const progress=file.size?Math.max(0,Math.min(100,(file.bytesTransferred||0)/file.size*100)):0;return `<div class="transfer"><div><h3>${esc(basename(file.filename))}</h3><p>${esc(file.username)} · ${size(file.size)} · ${esc(file.state)}</p></div><div><progress max="100" value="${progress}"></progress><span class="progress-label">${progress.toFixed(0)}%${file.averageSpeed?` · ${size(file.averageSpeed)}/с`:''}</span></div>${!String(file.state).includes('Completed')?`<button class="quiet small" data-cancel-user="${esc(file.username)}" data-cancel-id="${esc(file.id)}">Отменить</button>`:'<span class="badge">ЗАВЕРШЕНО</span>'}</div>`;}).join('')}</div>`:`<div class="empty">${icon('download')}<h3>Очередь свободна.</h3><p>Выбери музыку в Soulseek — здесь появятся загрузки.</p><button class="primary" data-nav="soulseek">Искать музыку ${icon('arrow')}</button></div>`);
}
function matchDialog(){
 const entry=state.selected;if(!entry?.directory||root()!=='inbox')return;
 state.matchPath=entry.path;
 showModal('Найдём правильное издание.',`<p>Альбом: <strong>${esc(entry.name)}</strong>. Найди релиз в MusicBrainz или вставь его MBID. beets сопоставит треки и подготовит изменения.</p><form id="release-form" class="search-form"><label class="visually-hidden" for="release-query">Исполнитель и альбом или MBID</label><input id="release-query" name="query" value="${esc(entry.name.replaceAll('_',' '))}" placeholder="Исполнитель и альбом или MBID" required><button class="primary" type="submit">${icon('search')}Найти</button></form><div id="release-results"></div>`);
}
async function searchReleases(value){
 const el=document.querySelector('#release-results');el.innerHTML='<div class="loading"><span class="spinner"></span>Ищем издания в MusicBrainz…</div>';
 try{if(/^[0-9a-f]{8}-[0-9a-f-]{27}$/i.test(value.trim())){await makePreview(value.trim());return;}
 const releases=await api(`/musicbrainz/releases?${query({query:value})}`);
 if(!document.querySelector('#release-results'))return;
 el.innerHTML=releases.length?`<div class="release-list">${releases.map(release=>`<article class="release-card"><div><h3>${esc(release.artist)} — ${esc(release.title)}</h3><p>${[release.date,release.country,release.format,release.tracks?`${release.tracks} треков`:'',release.disambiguation].filter(Boolean).map(esc).join(' · ')}</p></div><div class="release-actions"><a href="https://musicbrainz.org/release/${esc(release.id)}" target="_blank" rel="noreferrer" aria-label="Открыть релиз в MusicBrainz">${icon('external')}</a><button class="small" data-release="${esc(release.id)}">Проверить</button></div></article>`).join('')}</div>`:'<p class="search-help">Издания не найдены. Попробуй добавить исполнителя или вставить MBID релиза.</p>';
 }catch(error){el.innerHTML=`<div class="error-box">${esc(error.message)}</div>`;}
}
async function makePreview(releaseId){
 const job=await api('/imports/preview',{method:'POST',body:{path:state.matchPath,release_id:releaseId}});
 showModal('beets разбирается в деталях.',`<div class="loading"><span class="spinner"></span>Сопоставляем треки и готовим будущие теги…</div><p>Файлы остаются на месте. Можно закрыть это окно — предпросмотр сохранится в разделе «Задания».</p>`);
 watchJob(job.id);
}
async function watchJob(id){
 let job;
 try{job=await api(`/jobs/${id}`);}catch(error){toast(error.message,true);return;}
 if(job.status==='queued'||job.status==='running'){setTimeout(()=>watchJob(id),1800);return;}
 if(job.status==='ready'){if(modal.open)renderPlan(job);else toast('Предпросмотр готов. Открой его в разделе «Задания».');}
 else if(job.status==='done'){if(modal.open)modal.close();toast(`Альбом импортирован: ${job.result.files} файлов. Оригиналы сохранены во входящих.`);if(['inbox','library'].includes(state.page))loadFiles().catch(()=>{});}
 else{if(modal.open)showModal('Нужна твоя проверка.',`<div class="error-box">${esc(job.error||'Задание прервано')}</div>`);else toast(job.error||'Задание прервано',true);}
 if(state.page==='jobs')loadJobs().catch(()=>{});
}
function renderPlan(job){
 const plan=job.result, weak=plan.confidence<90;
 showModal('Вот что изменится.',`<div class="plan-banner${weak?' warning':''}"><strong>${esc(plan.artist)} — ${esc(plan.album)}</strong><br>Совпадение beets: ${plan.confidence}%. ${weak?'Проверь каждый трек: совпадение требует внимания.':'Проверь сопоставление перед импортом.'}<br>Создадим ${plan.rows.length} копий в архиве. Оригиналы во входящих сохранятся.</div>${plan.rows.map(row=>`<div class="plan-track"><span class="muted">${esc(row.source)}</span><div class="tag-diff"><dl>${diffTags(row.before)}</dl><div class="tag-arrow">${icon('arrow')}</div><dl>${diffTags(row.after)}</dl></div><code>${esc(row.destination)}</code></div>`).join('')}<p class="field-help">План действителен один час. Перед записью проверим контрольные суммы исходников и отсутствие файлов назначения. Обложки и прочие файлы в этой версии автоматически не копируются.</p>`,`<button data-action="close-modal">Вернуться</button><button class="primary" data-apply-plan="${esc(job.id)}">${icon('check')}Применить и скопировать</button>`);
}
function diffTags(tags){return [['Трек',tags.title],['Исполнитель',tags.artist],['Альбом',tags.album],['Год',tags.year||'—'],['Номер',`${tags.disc||1}.${tags.track||'—'} / ${tags.tracktotal||'—'}`]].map(([key,value])=>`<dt>${key}</dt><dd>${esc(value||'—')}</dd>`).join('');}
const statusLabels={queued:'В очереди',running:'В работе',ready:'Готов к проверке',done:'Выполнено',applied:'Применено',applying:'Импортируется',failed:'Ошибка',interrupted:'Прервано'};
async function loadJobs(){
 const jobs=await api('/jobs');state.jobs=jobs;if(state.page!=='jobs')return;
 document.querySelector('#content').innerHTML=heading('05','Работа за кадром.','Предпросмотры и импорт. Долгие операции не блокируют твой архив.')+(jobs.length?`<div class="jobs-list">${jobs.map(job=>`<article class="job-card"><div><h3>${job.kind==='preview'?'Сопоставление и предпросмотр':'Импорт в архив'}${job.result?.album?` · ${esc(job.result.artist)} — ${esc(job.result.album)}`:''}</h3><p>${date(job.created)}${job.error?`<br>${esc(job.error)}`:''}${job.result?.files?` · ${job.result.files} файлов скопировано`:''}</p></div><div class="job-actions"><span class="badge${['failed','interrupted'].includes(job.status)?' warm':''}">${esc(statusLabels[job.status]||job.status)}</span>${job.kind==='preview'&&job.status==='ready'?`<button class="small primary" data-show-plan="${esc(job.id)}">Проверить</button>`:''}</div></article>`).join('')}</div>`:`<div class="empty">${icon('jobs')}<h3>Всё спокойно.</h3><p>Выбери альбом во входящих и подбери релиз. Здесь появятся предпросмотр и ход импорта.</p></div>`);
}
async function loadHistory(){
 const events=await api('/history');if(state.page!=='history')return;
 document.querySelector('#content').innerHTML=heading('06','Всё, что ты изменил.','Последние 100 операций. Переименования и перемещения можно отменить.')+(events.length?`<div class="jobs-list">${events.map(event=>{const d=event.data;const titles={move:'Перемещение / переименование',mkdir:'Создание папки',import:'Импорт альбома',download:'Загрузка Soulseek'};const detail=event.kind==='move'?`${d.source} → ${d.destination}`:event.kind==='mkdir'?d.path:event.kind==='import'?`${d.artist} — ${d.album} · ${d.files} файлов`:`${d.username} · ${d.files} файлов`;return `<article class="job-card"><div><h3>${titles[event.kind]||esc(event.kind)}</h3><p>${esc(detail)}<br>${date(event.created)}${d.root?` · ${d.root==='inbox'?'Входящие':'Архив'}`:''}</p></div>${event.undone?'<span class="badge">Отменено</span>':['move','mkdir'].includes(event.kind)?`<button class="small" data-undo="${event.id}">${icon('history')}Отменить</button>`:''}</article>`;}).join('')}</div>`:`<div class="empty">${icon('history')}<h3>Чистая история.</h3><p>Здесь будут сохраняться твои изменения в архиве.</p></div>`);
}
function renderSettings(){
 const system=state.system;
 document.querySelector('#content').innerHTML=heading('07','Всё играет вместе.','Учётка Soulseek, состояние сервисов и твоя коллекция.',`<button data-action="refresh-system">${icon('refresh')}Проверить</button>`)+`
 <section id="soulseek-account" class="account-panel" aria-label="Учётная запись Soulseek"></section>
 <div class="connection-grid"><article class="connection">${icon('search')}<h2>slskd</h2><p>Поиск, пользователи и загрузки из Soulseek. Доступность API и вход в сеть проверяются отдельно.</p><span class="badge${system?.slskd?'':' warm'}">${system?.slskd?'API доступен':'API недоступен'}</span></article><article class="connection">${icon('disc')}<h2>beets + MusicBrainz</h2><p>Сопоставление треков, новые теги и структура папок. Импорт после твоей проверки.</p><span class="badge">beets установлен</span></article><article class="connection">${icon('music')}<h2>Navidrome</h2><p>Используй ту же музыкальную папку. Альбомы появятся после сканирования.</p>${system?.navidrome_url?`<a href="${esc(system.navidrome_url)}" target="_blank" rel="noreferrer">Открыть Navidrome ↗</a>`:'<p>Быстрая ссылка задаётся через NAVIDROME_URL.</p>'}</article></div><div class="info-box"><p><strong>Структура импорта</strong><br><code>${esc(system?.path_format||'$albumartist/$year - $album/$disc-$track $title')}</code></p><p><strong>Оригиналы сохраняются.</strong> beets записывает теги в копии. При конфликте имён импорт остановится.</p><p>Логи на сервере: <code>docker compose logs -f --tail=100 manager slskd</code>. Если страница недоступна, смотри раздел диагностики в README.</p><p><a href="https://github.com/fxmfxmfx/meandmymusic" target="_blank" rel="noreferrer">Исходный код · AGPL-3.0-only ↗</a></p></div>`;
 renderAccountPanel();
}
function renderAccountPanel(){
 const el=document.querySelector('#soulseek-account');if(!el)return;
 const account=state.account;
 if(!account){el.innerHTML='<div class="loading"><span class="spinner"></span>Проверяем Soulseek…</div>';return;}
 const online=account.logged_in, register=state.authMode==='register';
 el.innerHTML=`<div class="section-heading"><h2>Soulseek · твоя учётка</h2><button class="quiet small" data-action="soulseek-refresh">${icon('refresh')}Проверить статус</button></div><div class="account-status"><span class="badge${online?'':' warm'}">${online?'Вход подтверждён':account.busy||account.transitioning?'Подключение…':account.api_reachable?'Нет входа в Soulseek':'slskd недоступен'}</span>${online?`<span>Ты в сети как <strong>${esc(account.username)}</strong></span>`:''}</div>
 ${account.error?`<div class="error-box"><strong>${esc(account.error)}</strong>${account.failure?`<p class="field-help">Код: ${esc(account.failure.code)} · Этап: ${esc(({login:'Вход Soulseek',proxy:'Прокси',slskd_api:'API slskd',network:'Сеть',configuration:'Настройки'})[account.failure.stage]||account.failure.stage)}</p><p>${esc(account.failure.hint||'')}</p>`:''}</div>`:''}
 <p class="field-help">${account.proxy?.error?`Прокси: ${esc(account.proxy.error.message)} ${esc(account.proxy.error.hint)}`:account.proxy?.enabled?`Исходящий SOCKS5-прокси: ${esc(account.proxy.host)}:${esc(account.proxy.port)} · применяется к Soulseek и MusicBrainz`:'Исходящие подключения: напрямую. Прокси можно задать через OUTBOUND_PROXY в .env.'}</p>
 ${online?`<p class="field-help">${account.saved?'Учётка сохранена зашифрованной. После перезапуска подключимся снова.':'Учётка используется только в памяти. После перезапуска потребуется новый вход.'}</p><div class="account-actions"><button data-action="soulseek-logout">${icon('logout')}Выйти из Soulseek и забыть учётку</button>${account.saved?'<button class="quiet" data-action="soulseek-forget">Удалить сохранённую учётку</button>':''}</div>`:!account.managed?'<p class="field-help">Вход через менеджер требует штатной конфигурации с tmpfs. Обнови Compose или настрой внешний slskd по README.</p>':`
 <div class="tabbar"><button class="${register?'':'active'}" data-action="soulseek-auth-login">Вход</button><button class="${register?'active':''}" data-action="soulseek-auth-register">Новая учётка</button></div>
 <p class="field-help">${register?'Soulseek создаёт свободный логин при первом входе. Если имя занято, выбери другое. Отдельная регистрация на сайте не нужна.':'Используй учётку сети Soulseek, например из Nicotine+. Учётка форума Soulseek — отдельная.'}</p>
 <form id="soulseek-account-form" data-mode="${register?'register':'login'}" class="account-form"><div><label for="soulseek-username">Логин Soulseek</label><input id="soulseek-username" name="username" autocomplete="username" required maxlength="255"></div><div><label for="soulseek-password">Пароль</label><input id="soulseek-password" name="soulseek_password" type="password" autocomplete="${register?'new-password':'current-password'}" required maxlength="512"></div>${register?'<div><label for="soulseek-confirm">Повтори пароль</label><input id="soulseek-confirm" name="password_confirm" type="password" autocomplete="new-password" required maxlength="512"></div>':''}<label class="checkbox-label"><input type="checkbox" name="remember" checked>Сохранить зашифрованно на сервере</label><p class="field-help account-full">Пароль не возвращается в браузер и не сохраняется в .env. Рабочая копия конфига slskd находится в tmpfs. Для удалённого доступа используй HTTPS или VPN.</p><p class="login-error account-full" id="soulseek-account-error" role="status" aria-live="polite"></p><div class="account-actions account-full"><button class="primary" type="submit" ${account.busy?'disabled':''}>${icon('shield')}${register?'Создать / подключить и проверить':'Войти и проверить'}</button>${account.saved?'<button type="button" data-action="soulseek-reconnect">Войти сохранённой учёткой</button><button type="button" class="quiet" data-action="soulseek-forget">Забыть сохранённую</button>':''}</div></form>`}`;
}
function playFile(path){
 const entry=state.entries.find(e=>e.path===path);state.player={path,root:root()};
 document.querySelector('#player-slot').innerHTML=`<section class="player" aria-label="Проигрыватель"><div class="player-title">${icon('disc')}<div><strong>${esc(entry?.name||basename(path))}</strong><small>Прямо из твоего архива</small></div></div><audio id="audio-player" controls autoplay src="${esc(mediaURL(path))}"></audio><button class="quiet icon-button" data-action="close-player" aria-label="Закрыть проигрыватель">${icon('close')}</button></section>`;
 const player=document.querySelector('#audio-player');player.addEventListener('error',()=>toast('Браузер не смог воспроизвести этот формат. Файл остаётся доступен в архиве.',true));player.play().catch(()=>{});
}
document.addEventListener('input',event=>{if(event.target.id==='file-filter'){state.filter=event.target.value;renderFileList();}});
document.addEventListener('dblclick',event=>{const row=event.target.closest('[data-select]');if(!row)return;const entry=state.entries.find(e=>e.path===row.dataset.select);if(entry?.directory)openFolder(entry.path).catch(error=>toast(error.message,true));else if(entry?.audio)playFile(entry.path);});
document.addEventListener('keydown',event=>{if(event.key==='Enter'&&event.target.matches('tr[data-select]')){selectFile(event.target.dataset.select);}});
document.addEventListener('click',async event=>{
 const button=event.target.closest('button,[data-select]');if(!button)return;
 try{
  if(button.dataset.nav){await navigate(button.dataset.nav);return;}
  if(button.hasAttribute('data-path')){await openFolder(button.dataset.path);return;}
  if(button.dataset.open){await openFolder(button.dataset.open);return;}
  if(button.dataset.play){playFile(button.dataset.play);return;}
  if(button.dataset.select){selectFile(button.dataset.select);return;}
  if(button.dataset.downloadGroup){button.disabled=true;const group=state.groups[Number(button.dataset.downloadGroup)];await downloadFiles(group,group.files);return;}
  if(button.dataset.downloadFile){button.disabled=true;const [g,f]=button.dataset.downloadFile.split(':').map(Number);await downloadFiles(state.groups[g],[state.groups[g].files[f]]);return;}
  if(button.dataset.cancelId){await api(`/soulseek/downloads/${encodeURIComponent(button.dataset.cancelUser)}/${button.dataset.cancelId}`,{method:'DELETE'});await loadTransfers();return;}
  if(button.dataset.release){button.disabled=true;await makePreview(button.dataset.release);return;}
  if(button.dataset.applyPlan){button.disabled=true;const job=await api(`/imports/${button.dataset.applyPlan}/apply`,{method:'POST'});showModal('Собираем альбом в архив.',`<div class="loading"><span class="spinner"></span>Проверяем исходники, записываем теги и копируем файлы…</div><p>Можно закрыть окно. Ход операции сохранится в разделе «Задания».</p>`);watchJob(job.id);return;}
  if(button.dataset.showPlan){const job=await api(`/jobs/${button.dataset.showPlan}`);if(job.status==='ready')renderPlan(job);else toast('Предпросмотр уже применён или устарел.',true);return;}
  if(button.dataset.undo){button.disabled=true;await api(`/history/${button.dataset.undo}/undo`,{method:'POST'});toast('Операция отменена');await loadHistory();return;}
  const action=button.dataset.action;
  if(['new-folder','rename','move'].includes(action))fileDialog(action);
  else if(action==='match')matchDialog();
  else if(action==='close-modal')modal.close();
  else if(action==='reload')await navigate(state.page);
  else if(action==='view-grid'||action==='view-list'){state.view=action==='view-grid'?'grid':'list';renderFileList();}
  else if(action==='logout'){await api('/logout',{method:'POST'});showLogin();}
  else if(action==='close-player'){document.querySelector('#player-slot').innerHTML='';state.player=null;}
  else if(action==='refresh-system'){button.disabled=true;state.system=await api('/system');await refreshAccount();updateStatus();renderSettings();}
  else if(action==='soulseek-auth-login'||action==='soulseek-auth-register'){state.authMode=action.endsWith('register')?'register':'login';renderAccountPanel();}
  else if(action==='soulseek-refresh'){await refreshAccount();}
  else if(action==='soulseek-logout'){button.disabled=true;await api('/soulseek/auth/logout',{method:'POST'});await refreshAccount();toast('Сессия Soulseek отключена, сохранённая учётка удалена.');}
  else if(action==='soulseek-forget'){button.disabled=true;await api('/soulseek/auth/saved',{method:'DELETE'});await refreshAccount();toast('Сохранённая учётка удалена.');}
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
   if(mode==='register'){body.password_confirm=String(values.get('password_confirm'));if(body.password!==body.password_confirm)throw new Error('Пароли не совпадают');}
   state.accountBusy=true;document.querySelector('#soulseek-account-error').textContent='Проверяем маршрут и вход в сеть Soulseek. Ответ может занять до минуты…';
   try{const result=await api(`/soulseek/auth/${mode}`,{method:'POST',body});if(result.logged_in!==true)throw new Error('Сервер не подтвердил вход');toast(result.message||'Вход в Soulseek подтверждён.');}
   finally{state.accountBusy=false;form.querySelectorAll('input[type="password"]').forEach(input=>input.value='');await refreshAccount();}
  }
  else if(form.id==='file-operation'){
   const value=String(values.get('value')).trim(),mode=form.dataset.mode;
   if(!value)throw new Error('Укажи название');
   if(mode!=='move'&&(value.includes('/')||value.includes('\\')||value==='.'||value==='..'))throw new Error('Название не должно содержать разделители пути');
   if(mode==='new-folder')await api('/folders',{method:'POST',body:{root:root(),path:join(state.path,value)}});
   else await api('/move',{method:'POST',body:{root:root(),source:state.selected.path,destination:mode==='rename'?join(state.path,value):value}});
   modal.close();state.selected=null;await loadFiles();toast('Готово. Операция сохранена в истории.');
  }
  else if(form.id==='soulseek-form'){
   clearInterval(state.searchTimer);if(state.searchId&&!state.searchDone)await api(`/soulseek/searches/${state.searchId}`,{method:'DELETE'}).catch(()=>{});
   state.searchQuery=String(values.get('query')).trim();const result=await api('/soulseek/searches',{method:'POST',body:{query:state.searchQuery}});
   state.searchId=result.id;state.groups=[];state.searchDone=false;
   if(!state.searchId)throw new Error('slskd не вернул идентификатор поиска');
   renderSoulseek();await pollSearch();if(!state.searchDone)state.searchTimer=setInterval(pollSearch,2000);
  }
  else if(form.id==='release-form')await searchReleases(String(values.get('query')).trim());
 }catch(error){if(form.id==='login-form'){const el=document.querySelector('#login-error');if(el)el.textContent=error.message;}else if(form.id==='soulseek-account-form'){const el=document.querySelector('#soulseek-account-error');if(el)el.textContent=error.message+(error.connection?` [${error.connection.code}] ${error.connection.hint||''}`:'');toast(error.message,true);}else if(form.id==='file-operation'){const el=document.querySelector('#file-operation-error');if(el)el.textContent=error.message;}else toast(error.message,true);}
 finally{if(submit?.isConnected)submit.disabled=false;}
});
api('/session').then(shell).catch(()=>showLogin());
