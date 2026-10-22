'use strict';
const $ = id => document.getElementById(id);
const token = document.querySelector('meta[name="newsjockey-token"]').content;
let page = 1, total = 0, sourceRows = [], watching = null, catalogRows = [], suggestionIndex = -1, discovered = [];
let articleRequest = 0, catalogRequest = 0, currentArticle = null, publisherRows = [];
let portalTimezone='Asia/Calcutta';
const date = value => value ? new Date(value).toLocaleString([], {dateStyle:'medium', timeStyle:'short',timeZone:portalTimezone}) : '—';
const node = (tag, value, cls) => { const n = document.createElement(tag); if(value !== undefined) n.textContent = value; if(cls) n.className = cls; return n; };
const safeURL = url => { try { const u=new URL(url); return ['http:','https:'].includes(u.protocol)?u.href:'#'; } catch { return '#'; } };
const link = (url, label, cls) => { const a = node('a', label, cls); a.href=safeURL(url); a.target='_blank'; a.rel='noopener noreferrer'; return a; };
function notice(message, bad=false) { $('notice').textContent=message; $('notice').classList.toggle('bad',bad); if($('analysis-dialog')?.open){$('analysis-dialog-notice').textContent=message;$('analysis-dialog-notice').classList.toggle('bad',bad);} }
async function api(path, method='GET', body) {
  const res = await fetch(path, {method, headers:method==='GET'?{}:{'Content-Type':'application/json','X-NewsJockey-Token':token}, body:body===undefined?undefined:JSON.stringify(body)});
  let data; try { data = await res.json(); } catch { throw new Error('The local server returned an unreadable response.'); }
  if(!res.ok) throw new Error(data.error || `Request failed (${res.status}).`);
  return data;
}
function handle(fn) { return async (...args) => { try { await fn(...args); } catch(e) { notice(e.message,true); } }; }
function busy(on) { $('collect').disabled=on; $('discover-button').disabled=on; }
function showView(view) {
  for(const name of ['articles','sources','recipes']){$(name+'-view').hidden=view!==name;$(name+'-tab').setAttribute('aria-pressed',String(view===name));}
}
async function loadSources() {
  const [data,publishers]=await Promise.all([api('/api/sources'),api('/api/publishers')]); sourceRows=data.sources; publisherRows=publishers.publishers; renderPublishers();
  const selected=$('source').value, category=$('category').value;
  $('source').replaceChildren(new Option('All publishers',''));
  for(const s of publisherRows.filter(s=>!category||s.category===category)) $('source').add(new Option(s.name,String(s.id)));
  const channelSelected=$('channel-publisher').value;$('channel-publisher').replaceChildren(new Option('All publishers',''));
  for(const p of publisherRows)$('channel-publisher').add(new Option(p.name,String(p.id)));$('channel-publisher').value=channelSelected;
  $('source').value=selected;
  $('source-count').textContent=`${publisherRows.length} publishers · ${sourceRows.length} discovery channels · ${sourceRows.filter(s=>s.enabled).length} enabled`;
  // Keep an interval being edited intact during background status refreshes.
  if($('sources-body').querySelector('.interval[data-dirty="true"]')) return;
  const tbody=$('sources-body'); tbody.replaceChildren();
  for(const s of sourceRows.filter(s=>!$('channel-publisher').value||String(s.publisher_id)===$('channel-publisher').value)) {
    const tr=node('tr'), name=node('td'); name.append(link(s.website,s.name),node('div',s.channel_type+' · '+s.section,'preview'),link(s.feed_url,s.feed_url,'feed-link')); tr.append(name);
    const cat=node('select'); cat.className='source-category'; cat.setAttribute('aria-label',`Category for ${s.name}`);
    for(const c of data.categories) cat.add(new Option(c,c)); cat.value=s.category;
    cat.addEventListener('change',handle(async()=>{await api(`/api/sources/${s.id}`,'PATCH',{category:cat.value}); await loadSources(); await loadArticles();}));
    const catCell=node('td');catCell.append(cat);tr.append(catCell);
    const enabled=node('input'); enabled.type='checkbox'; enabled.checked=Boolean(s.enabled);enabled.setAttribute('aria-label',`Enable ${s.name}`);
    enabled.addEventListener('change',handle(async()=>{const value=enabled.checked; await api(`/api/sources/${s.id}`,'PATCH',{enabled:value}); await loadSources();notice(`${s.name} ${value?'enabled':'disabled'}.`);}));
    const enableCell=node('td');enableCell.append(enabled);tr.append(enableCell);
    const interval=node('input');interval.type='number';interval.min=1;interval.max=1440;interval.value=s.interval_minutes;interval.className='interval';interval.setAttribute('aria-label',`Polling interval for ${s.name}`);
    interval.addEventListener('input',()=>{interval.dataset.dirty='true';});
    const saveInterval=node('button','Save');saveInterval.setAttribute('aria-label',`Save interval for ${s.name}`);
    saveInterval.addEventListener('click',handle(async()=>{await api(`/api/sources/${s.id}`,'PATCH',{interval_minutes:Number(interval.value)});delete interval.dataset.dirty;notice(`Saved interval for ${s.name}.`);await loadSources();}));
    const intervalCell=node('td');intervalCell.append(interval,saveInterval);tr.append(intervalCell);
    const status=node('td');status.append(node('span',s.status,'status'));
    if(s.error) status.append(node('div',s.error,'error')); if(s.warning) status.append(node('div',s.warning,'warning'));
    tr.append(status,node('td',date(s.last_success),'date'),node('td',s.enabled?date(s.next_check):'Paused','date'),node('td',date(s.latest_published),'date'),node('td',s.article_count));
    const refresh=node('button','Collect');refresh.disabled=Boolean(data.active_job)||!s.enabled;refresh.addEventListener('click',handle(async()=>{const job=await api('/api/collect','POST',{source_id:s.id});watchJob(job.job_id);}));
    const action=node('td');action.append(refresh);tr.append(action);tbody.append(tr);
  }
  if(data.active_job && !watching) watchJob(data.active_job);
}
async function loadArticles() {
  const generation=++articleRequest;
  const params=new URLSearchParams({page:String(page),size:$('size').value});
  for(const id of ['search','category','source','content_type','media_format','extraction_status','body_quality','date_group','kind','since','from','to','sort','direction']) if($(id).value) params.set(id==='search'?'q':id==='source'?'publisher':id,$(id).value);
  const data=await api('/api/articles?'+params);
  if(generation!==articleRequest) return;
  total=data.total;
  const pages=Math.max(1,Math.ceil(total/data.size));
  if(page>pages){page=pages;return loadArticles();}
  $('article-count').textContent=`${total.toLocaleString()} articles`;
  $('page-info').textContent=`Page ${page} of ${pages}`;
  $('prev').disabled=page===1;$('next').disabled=page>=pages;
  const tbody=$('articles-body');tbody.replaceChildren();
  if(!data.articles.length){const tr=node('tr'),td=node('td',sourceRows.some(s=>s.last_success)?'No articles match these filters.':'Waiting for the first collection. Source status shows progress.','empty');td.colSpan=8;tr.append(td);tbody.append(tr);return;}
  for(const a of data.articles){
    const tr=node('tr'),title=node('td'),button=node('button',a.title,'title-button');
    button.addEventListener('click',handle(()=>openArticle(a.id))); title.append(button);
    if(a.preview) title.append(node('div',a.preview+(a.content_length>240?'…':''),'preview'));
    const types=node('td');types.append(node('div',a.content_type),node('div',JSON.parse(a.media_formats).join(', '),'preview'));
    tr.append(title,node('td',a.source),node('td',a.source_category),types,node('td',date(a.published_at),'date'),node('td',date(a.first_seen),'date'));
    const author=node('td');author.append(node('div',a.author||'—'));if(a.tags)author.append(node('div',a.tags,'preview'));const text=node('td');text.append(node('div',`${a.body_quality} · ${a.body_origin}`,'status'),node('div','Latest job: '+a.extraction_status),node('div','Fetch: '+a.fetch_outcome,'preview'));if(a.extraction_error)text.append(node('div',a.extraction_error,'error'));tr.append(author,text);tbody.append(tr);
  }
}
async function openArticle(id) {
  const a=await api(`/api/articles/${id}`); currentArticle=a.id;
  $('detail-title').textContent=a.title;$('original').href=a.url;
  const dl=$('detail-metadata');dl.replaceChildren();
  let delay='Unavailable';
  if(a.observed_delay_seconds!==null){delay=a.observed_delay_seconds<0?'Publisher time is after first-seen time':`${Math.round(a.observed_delay_seconds/60).toLocaleString()} minutes (includes any backlog on first collection)`;}
  for(const [key,value] of [['Source',a.source],['Source category',a.source_category],['Content type',a.content_type],['Publisher label',a.publisher_content_label||'—'],['Label basis',a.content_type_basis||'—'],['Media formats',a.media_formats.join(', ')],['Language',a.language||'Unknown'],['Extraction',a.extraction_status],['Extractor',a.extraction_method||'—'],['Extraction attempted',date(a.extraction_attempted_at)],['Extracted',date(a.extracted_at)],['Extraction notes',a.extraction_notes||'—'],['Extraction error',a.extraction_error||'—'],['Transcript',a.transcript_status],['Author',a.author||'—'],['Tags',a.tags||'—'],['Published',date(a.published_at)],['Publisher date (raw)',a.published_raw||'—'],['Publisher updated',date(a.publisher_updated_at)],['Update date (raw)',a.updated_raw||'—'],['First seen',date(a.first_seen)],['Last seen',date(a.last_seen)],['Content changed',date(a.content_changed_at)],['Observed delay',delay],['Feed URL',a.feed_url],['Article URL',a.url]]){dl.append(node('dt',key),node('dd',value));}
  $('detail-content-type').value=a.content_type;
  for(const [key,value] of [['Saved body quality',a.body_quality],['Body origin',a.body_origin],['Body quality notes',a.body_notes||'—'],['Body saved',date(a.body_saved_at)],['Publisher access marker',a.publisher_access||'unknown'],['Fetch outcome',a.fetch_outcome],['HTTP status',a.http_status||'—'],['Next retry / probe',date(a.next_extraction)],['Recipe',a.recipe_id?`${a.recipe_id} v${a.recipe_version}`:'Generic local parsers']])dl.append(node('dt',key),node('dd',value));
  $('detail-kind').textContent=a.content_kind;showBody(a.body_html,a.content,a.source,a.url);
  $('detail-feed-content').textContent=a.feed_content||'No feed body supplied.';
  $('retry-article').disabled=a.extraction_status==='extracting';
  const media=$('detail-media');media.replaceChildren();
  if(a.media_json.length||a.transcript){media.append(node('h3','Video / audio / transcript'));for(const m of a.media_json){const p=node('p');p.append(link(m.url,m.kind+': '+(m.title||m.url)));if(m.duration)p.append(node('span',' · '+m.duration));if(m.description)p.append(node('div',m.description,'preview'));media.append(p);}if(a.transcript)media.append(node('p',a.transcript,'transcript'));}
  const images=$('detail-images');images.replaceChildren();
  if(a.images_json.length){images.append(node('h3',`${a.images_json.length} publisher images / thumbnails`));for(const im of a.images_json){const figure=node('figure'),image=node('img');image.src=safeURL(im.url);image.alt=im.caption||a.title;image.loading='lazy';image.referrerPolicy='no-referrer';image.addEventListener('error',()=>{image.hidden=true;});figure.append(image);const caption=node('figcaption',im.caption||'');caption.append(node('div',[a.source,im.credit].filter(Boolean).join(' · ')),link(im.source_url||a.url,'Source article'),node('span',' · '),link(im.url,'Image URL'));figure.append(caption);images.append(figure);}}
  const history=$('detail-history');history.replaceChildren();for(const c of a.channels)history.append(link(c.feed_url,c.channel_type+': '+c.name,'feed-link'));
  for(const v of a.versions){const b=node('button',date(v.observed_at)+' · '+v.origin);b.addEventListener('click',handle(async()=>{const saved=await api(`/api/articles/${a.id}/versions/${v.id}`);$('detail-kind').textContent='Saved version: '+date(saved.observed_at);showBody(saved.snapshot.body_html,saved.snapshot.content,a.source,a.url);}));history.append(b);}
  
  if(!$('article-dialog').open)$('article-dialog').showModal();
}
async function watchJob(id) {
  if(watching) return;
  watching=id; busy(true);
  try {
    while(watching===id){
      const job=await api('/api/jobs/'+id);
      notice(`${job.kind==='collect'?'Collecting sources':'Discovering feeds'}: ${job.completed} / ${job.total}`);
      if(job.status!=='running'){
        if(job.status==='error')throw new Error(job.error||'Job failed.');
        if(job.kind==='discover')renderDiscovery(job.results);
        const errors=job.results.filter(x=>x.error).length;
        const added=job.results.reduce((n,x)=>n+(x.new||0),0);
        notice(job.kind==='collect'?`Collection finished: ${added} new articles; ${errors} source errors.${job.total===0?' No sources are eligible yet; disabled sources and error backoff are respected.':''}`:`Discovery finished: ${job.results.filter(x=>x.feeds?.length).length} URLs returned feeds; ${errors} need attention.`,errors>0);
        break;
      }
      if(job.kind==='collect'&&!document.hidden&&!$('raw-workspace').hidden){await loadSources();if(!$('articles-view').hidden)await loadArticles();}
      await new Promise(resolve=>setTimeout(resolve,1500));
    }
  } catch(e){notice(e.message,true);}
  finally {watching=null;busy(false);if(!document.hidden&&!$('raw-workspace').hidden){await loadSources();await loadArticles();}}
}
async function loadCatalog() {
  const generation=++catalogRequest;
  const q=$('catalog-search').value.trim();
  const data=await api('/api/catalog?q='+encodeURIComponent(q));
  if(generation!==catalogRequest)return;
  catalogRows=data.results;suggestionIndex=-1;
  const box=$('catalog-results');box.replaceChildren();box.hidden=false;$('catalog-search').setAttribute('aria-expanded','true');
  if(!catalogRows.length){box.append(node('p','No catalog match. Use bulk website import below.','muted'));return;}
  for(let i=0;i<catalogRows.length;i++){
    const s=catalogRows[i],button=node('button',undefined,'catalog-row');button.id='suggestion-'+i;button.setAttribute('role','option');button.setAttribute('aria-selected','false');button.disabled=s.added;
    const text=node('span',s.name);text.append(node('small',s.category+' · '+s.website));button.append(text,node('span',s.added?'Already added':'Add'));
    button.addEventListener('click',handle(()=>addCatalog(s)));box.append(button);
  }
}
async function addCatalog(s) {
  const result=await api('/api/sources','POST',{sources:[{catalog_id:s.id}]});
  notice(`${s.name} ${result.results[0].added?'added. It will be collected on the next background check.':'is already added.'}`);
  await loadSources();await loadCatalog();
}
function renderDiscovery(results) {
  discovered=[];
  const container=$('discovery-results');container.replaceChildren();
  for(const result of results){
    const group=node('div',undefined,'discovery-group');group.append(node('strong',result.url));
    if(result.error)group.append(node('p',result.error,'error'));
    for(let i=0;i<result.feeds.length;i++){
      const feed=result.feeds[i],label=node('label',undefined,'feed-choice'),input=node('input');input.type='checkbox';input.checked=i===0;
      const existing=sourceRows.some(s=>s.feed_url===feed.feed_url);if(existing){input.checked=false;input.disabled=true;}
      const span=node('span',feed.name+(existing?' (already added)':''));span.append(node('small',feed.feed_url+' · '+feed.entries+' current entries'));
      label.append(input,span);group.append(label);discovered.push({feed,input});
    }
    container.append(group);
  }
  $('add-discovered').hidden=!discovered.some(x=>!x.input.disabled);
}
$('articles-tab').onclick=()=>showView('articles');$('sources-tab').onclick=handle(async()=>{showView('sources');await loadSources();});
$('collect').onclick=handle(async()=>{const job=await api('/api/collect','POST',{});watchJob(job.job_id);});
$('filters').onsubmit=handle(async e=>{e.preventDefault();page=1;await loadArticles();});
$('category').onchange=handle(async()=>{ $('source').value='';await loadSources();page=1;await loadArticles(); });
for(const id of ['source','content_type','media_format','extraction_status','body_quality','date_group','kind','since','sort','direction','size'])$(id).onchange=handle(async()=>{page=1;await loadArticles();});
$('reset').onclick=handle(async()=>{$('filters').reset();page=1;await loadSources();await loadArticles();});
$('prev').onclick=handle(async()=>{page--;await loadArticles();});$('next').onclick=handle(async()=>{page++;await loadArticles();});
$('close-dialog').onclick=()=>$('article-dialog').close();
let catalogTimer;
$('catalog-search').oninput=()=>{clearTimeout(catalogTimer);catalogTimer=setTimeout(handle(loadCatalog),150);};
$('catalog-search').onfocus=handle(loadCatalog);
$('catalog-search').onkeydown=handle(async e=>{
  if(e.key==='Escape'){$('catalog-results').hidden=true;$('catalog-search').setAttribute('aria-expanded','false');return;}
  if(!['ArrowDown','ArrowUp','Enter'].includes(e.key))return;
  const available=catalogRows.map((s,i)=>s.added?-1:i).filter(i=>i>=0);if(!available.length)return;
  e.preventDefault();
  if(e.key==='Enter'){if(suggestionIndex>=0)await addCatalog(catalogRows[suggestionIndex]);return;}
  const current=available.indexOf(suggestionIndex);suggestionIndex=available[(current+(e.key==='ArrowDown'?1:-1)+available.length)%available.length];
  for(const button of $('catalog-results').querySelectorAll('button')){const selected=button.id==='suggestion-'+suggestionIndex;button.classList.toggle('selected',selected);button.setAttribute('aria-selected',String(selected));}
  const active=$('suggestion-'+suggestionIndex);active.scrollIntoView({block:'nearest'});$('catalog-search').setAttribute('aria-activedescendant',active.id);
});
$('discover-form').onsubmit=handle(async e=>{e.preventDefault();const urls=$('bulk-urls').value.split(/[\n\r,]+/).map(x=>x.trim()).filter(Boolean);const job=await api('/api/discover','POST',{urls});watchJob(job.job_id);});
$('add-discovered').onclick=handle(async()=>{
  const sources=discovered.filter(x=>x.input.checked&&!x.input.disabled).map(x=>({...x.feed,category:$('import-category').value}));
  if(!sources.length)throw new Error('Select at least one feed.');
  $('add-discovered').disabled=true;
  try {const result=await api('/api/sources','POST',{sources});notice(`${result.results.filter(x=>x.added).length} sources added. Background collection will pick them up.`);await loadSources();$('add-discovered').hidden=true;for(const x of discovered){if(x.input.checked)x.input.disabled=true;}}
  finally{$('add-discovered').disabled=false;}
});
handle(async()=>{await loadExtractionControls();await loadSources();await loadArticles();await loadProcessing();if(!watching)notice('Ready. Background collection checks enabled sources automatically.');})();
let rawRefreshBusy=false;
setInterval(handle(async()=>{if(document.hidden||$('raw-workspace').hidden||rawRefreshBusy)return;rawRefreshBusy=true;try{if(!watching){await loadSources();if(!$('articles-view').hidden)await loadArticles();}await loadProcessing();}finally{rawRefreshBusy=false;}}),15000);

function renderPublishers(){
  const body=$('publishers-body');body.replaceChildren();
  for(const p of publisherRows){
    const tr=node('tr'),name=node('td');name.append(link(p.website,p.name));tr.append(name);
    const cat=node('select');cat.setAttribute('aria-label',`Source category for ${p.name}`);for(const c of ['Politics/National','Entertainment','Sports','Tech','Business'])cat.add(new Option(c,c));cat.value=p.category;
    cat.onchange=handle(async()=>{await api(`/api/publishers/${p.id}`,'PATCH',{category:cat.value});await loadSources();await loadArticles();});const catcell=node('td');catcell.append(cat);tr.append(catcell);
    tr.append(node('td',`${p.enabled_channels} / ${p.channel_count} enabled`));
    const coverage=node('td');coverage.append(node('div',p.coverage_status,'status'));const details=node('details');details.append(node('summary','Coverage details'),node('p',p.coverage_notes||'Initial coverage scan is queued.'));coverage.append(details,node('div','Last scan: '+date(p.last_discovery),'preview'));tr.append(coverage);
    tr.append(node('td',`${p.complete_count} / ${p.article_count} complete`));
    const cell=node('td'),diagnose=node('button','Export samples');diagnose.onclick=handle(()=>exportDiagnostics(p.id,p.name));cell.append(diagnose);tr.append(cell);
    const action=node('td'),scan=node('button','Scan'),retry=node('button','Retry gaps');scan.onclick=handle(async()=>{await api('/api/coverage/expand','POST',{publisher_id:p.id});notice(`Queued coverage scan for ${p.name}.`);await loadSources();});retry.onclick=handle(async()=>{const r=await api('/api/extraction/retry','POST',{publisher_id:p.id});notice(`${r.queued} extraction gaps queued for ${p.name}.`);await loadProcessing();});action.append(scan,retry);tr.append(action);body.append(tr);
  }
}
async function loadProcessing(){
  const p=await api('/api/processing'),body=$('processing-body');body.replaceChildren();
  const statuses=[
    ['pending','Pending','Waiting for extraction/recheck. Current-month articles run automatically; previous months and unknown dates require a manual job.'],
    ['extracting','Extracting','An article-page request is in progress.'],
    ['complete','Complete','Public text passed our structural and length checks; completeness is not publisher-verified.'],
    ['partial','Partial','Text found, but completeness is uncertain: missing structure, truncation, low word count, or more text in the feed.'],
    ['blocked','Blocked','Access refused (HTTP 401/403), rate limited (429), or a challenge page returned. Feed text is retained.'],
    ['failed','Failed','The request or processing raised an error, such as a timeout or another HTTP error. Feed text is retained.'],
    ['paywalled','Paywalled','The page identifies restricted content. Public excerpts/feed text are retained.'],
    ['unsupported','Unsupported','No readable article body found, or the page/file format is unsupported.'],
    ['not_applicable','Not applicable','Video/audio item without an article body. Transcript availability is tracked separately.']
  ];
  for(const [key,label,meaning] of statuses){
    const tr=node('tr'),heading=node('th',label);heading.scope='row';
    tr.append(heading,node('td',(p.extraction[key]||0).toLocaleString(),'count'),node('td',meaning));body.append(tr);
  }
  $('processing-total').textContent=Object.values(p.extraction).reduce((n,count)=>n+count,0).toLocaleString();
  $('processing-versions').textContent=p.versions.toLocaleString();
  $('processing-workers').textContent=p.running?`${p.workers} extraction workers running`:'Background workers stopped';
  await loadExtractionControls();
}
$('channel-publisher').onchange=handle(loadSources);
$('expand-coverage').onclick=handle(async()=>{await api('/api/coverage/expand','POST',{});notice('Coverage scans queued for all enabled publishers.');});
$('retry-gaps').onclick=handle(async()=>{const r=await api('/api/extraction/retry','POST',{});notice(`${r.queued} extraction gaps queued.`);await loadProcessing();});
$('retry-article').onclick=handle(async()=>{if(!currentArticle)return;const r=await api('/api/extraction/retry','POST',{article_id:currentArticle});notice(`${r.queued} article queued for extraction.`);await openArticle(currentArticle);await loadProcessing();});
$('save-content-type').onclick=handle(async()=>{if(!currentArticle)return;await api(`/api/articles/${currentArticle}`,'PATCH',{content_type:$('detail-content-type').value});notice('Content type saved.');await openArticle(currentArticle);await loadArticles();});

function showBody(html,text,publisher,url){
  const frame=$('detail-rich');frame.hidden=!html;$('detail-content').hidden=Boolean(html);
  $('detail-content').textContent=text||'No article body available. See media and extraction status.';
  if(!html){frame.srcdoc='';return;}
  const credit=node('p','Source: '+publisher+' · ');credit.append(link(url,'Original article'));
  // HTML is allowlist-sanitized by the server; the iframe independently disallows scripts.
  frame.srcdoc='<!doctype html><html><head><meta charset="utf-8"><style>body{font:16px/1.65 system-ui;color:#182332;margin:12px}img{max-width:100%;height:auto}table{border-collapse:collapse}td,th{border:1px solid #ccc;padding:6px}a{color:#174f87}figure{margin:12px 0}figcaption{font-size:13px}pre{white-space:pre-wrap}</style></head><body>'+credit.outerHTML+html+'</body></html>';
  frame.onload=()=>{try{frame.style.height=Math.min(2400,Math.max(300,frame.contentDocument.body.scrollHeight+30))+'px';}catch{frame.style.height='600px';}};
}
async function loadExtractionControls(){
  const [settings,jobs,coverage,metrics]=await Promise.all([api('/api/extraction/settings'),api('/api/extraction/jobs'),api('/api/extraction/coverage?group='+$('coverage-group').value),api('/api/extraction/metrics')]);
  portalTimezone=settings.window.timezone;
  if(document.activeElement!==$('portal-timezone'))$('portal-timezone').value=portalTimezone;
  $('extraction-schedule').textContent=`Automatic: published in ${settings.window.label} (${portalTimezone}). Previous months and unknown dates require a manual job. Chrome runs only when requested.`;
  const jb=$('extraction-jobs-body');jb.replaceChildren();
  if(!jobs.jobs.length){const tr=node('tr'),td=node('td','No requested extraction jobs.');td.colSpan=9;tr.append(td);jb.append(tr);}
  for(const job of jobs.jobs){
    const tr=node('tr');tr.append(node('td',`${job.transport==='browser'?'Chrome':'Ordinary'} · ${job.scope==='older'?'All previous months':job.scope==='recent'?'Current month':job.scope==='undated'?'Unknown date':job.scope} · ${date(job.created_at)}`),node('td',job.status),node('td',job.total.toLocaleString()),node('td',job.processed.toLocaleString()),node('td',(job.reused_results||0).toLocaleString()),node('td',job.remaining.toLocaleString()+(job.waiting_for_host?` · ${job.waiting_for_host.toLocaleString()} waiting for host`:'')),node('td',job.in_flight),node('td',job.counts.failed||0));
    const actions=node('td');if(['queued','running','paused'].includes(job.status)){for(const action of [job.status==='paused'?'resume':'pause','cancel']){const button=node('button',action);button.onclick=handle(async()=>{await api('/api/extraction/jobs/'+job.id,'PATCH',{action});await loadExtractionControls();});actions.append(button);}}tr.append(actions);jb.append(tr);
  }
  $('extraction-job-policy').textContent='Historical backfill runs once per article version. Repeated clicks add newly discovered articles; completed attempts are reused, including unsuccessful results. Use an article’s retry control for an intentional recheck.'+(jobs.merged_jobs?` ${jobs.merged_jobs} duplicate job(s) consolidated; original job history retained.`:'');
  const cb=$('coverage-body');cb.replaceChildren();
  for(const row of coverage.publishers){const tr=node('tr');for(const value of [row.name,row.total,row.full,row.partial,row.unknown,row.no_body,row.pending,`${row.blocked} / ${row.failed} / ${row.paywalled}`,row.media_only,row.undated,`${row.feed} / ${row.page} / ${row.browser}`])tr.append(node('td',value));cb.append(tr);}
  const t=coverage.totals;
  $('coverage-summary').textContent=`${t.total.toLocaleString()} items · ${t.full.toLocaleString()} full · ${t.partial.toLocaleString()} partial · ${t.unknown.toLocaleString()} unknown · ${t.no_body.toLocaleString()} without text · ${t.pending.toLocaleString()} pending. Full-body coverage: ${coverage.full_body_percent===null?'—':coverage.full_body_percent+'%'} of text-bearing items.`;
  $('host-cooldowns').textContent=coverage.cooldowns.length?`${coverage.cooldowns.length} hosts cooling down: `+coverage.cooldowns.slice(0,8).map(h=>h.host+' until '+date(h.next_allowed)).join('; '):'No publisher cooldowns.';
  const attempts=$('attempts-body');attempts.replaceChildren();
  for(const row of metrics.transports){const tr=node('tr');for(const value of [row.transport,row.attempts,row.average_seconds,row.full_results||0,row.unavailable||0])tr.append(node('td',value));attempts.append(tr);}
}
async function startExtraction(transport,scope){const job=await api('/api/extraction/jobs','POST',{transport,scope});notice(`${job.existing?'Using existing':'Created'} ${transport==='browser'?'Chrome':'ordinary'} extraction job: ${job.total.toLocaleString()} items, ${(job.added||0).toLocaleString()} newly added. ${job.status==='completed'?'Backfill complete; no new extraction needed.':job.status==='paused'?'Resume it in the job table.':''}`);await loadExtractionControls();}
$('extract-old').onclick=handle(()=>startExtraction('http','older'));
$('chrome-recent').onclick=handle(()=>startExtraction('browser',$('extraction-month').value));
$('extract-month').onclick=handle(()=>startExtraction('http',$('extraction-month').value));
$('chrome-older').onclick=handle(()=>startExtraction('browser','older'));
$('coverage-group').onchange=handle(loadExtractionControls);
$('save-timezone').onclick=handle(async()=>{await api('/api/extraction/settings','PATCH',{timezone:$('portal-timezone').value.trim()});await loadExtractionControls();await loadArticles();notice('Portal timezone saved. Existing manual jobs retain their original date window.');});
$('recipes-tab').onclick=handle(async()=>{showView('recipes');await loadRecipes();});
async function exportDiagnostics(id,name){const data=await api(`/api/publishers/${id}/recipe-diagnostics`),blob=new Blob([JSON.stringify(data,null,2)],{type:'application/json'});const url=URL.createObjectURL(blob),anchor=document.createElement('a');anchor.href=url;anchor.download=name.replace(/[^a-z0-9]+/gi,'-')+'-recipe-diagnostics.json';anchor.click();setTimeout(()=>URL.revokeObjectURL(url),1000);notice(`Exported ${data.samples.length} cached examples for ${name}. No model call was made.`);}
async function loadRecipes(){
 const data=await api('/api/recipes'),body=$('recipes-body');body.replaceChildren();
 for(const r of data.recipes){const tr=node('tr');tr.append(node('td',r.id),node('td',r.hosts.join(', ')+' · '+r.template),node('td','v'+r.version+(r.active?' · active':'')),node('td',r.validation.state||'unverified'));const cell=node('td'),edit=node('button','Edit');edit.onclick=()=>{$('recipe-json').value=JSON.stringify(Object.fromEntries(Object.entries(r).filter(([k])=>!['version','active','validation'].includes(k))),null,2);$('recipe-json').closest('details').open=true;};cell.append(edit);if(r.active&&r.version>1){const rollback=node('button','Rollback');rollback.onclick=handle(async()=>{await api('/api/recipes/'+encodeURIComponent(r.id)+'/rollback','POST',{});await loadRecipes();notice('Previous recipe restored; affected recent items queued.');});cell.append(rollback);}tr.append(cell);body.append(tr);}
 const failures=$('recipe-failures-body');failures.replaceChildren();for(const f of data.failures){const tr=node('tr');tr.append(node('td',f.publisher),node('td',f.template),node('td',f.count),node('td',date(f.last_seen)));const td=node('td'),button=node('button','Export samples');button.onclick=handle(()=>exportDiagnostics(f.publisher_id,f.publisher));td.append(button);tr.append(td);failures.append(tr);}
}
function recipePayload(){const ids=id=>$(id).value.split(/[ ,]+/).filter(Boolean).map(Number);return {definition:JSON.parse($('recipe-json').value),development_ids:ids('recipe-development'),validation_ids:ids('recipe-validation')};}
$('preview-recipe').onclick=handle(async()=>{const result=await api('/api/recipes/preview','POST',recipePayload());$('recipe-results').textContent=JSON.stringify(result.samples,null,2);notice(result.passed?'Validation samples passed.':'Some validation samples failed.',!result.passed);});
$('recipe-form').onsubmit=handle(async event=>{event.preventDefault();const result=await api('/api/recipes','POST',recipePayload());notice(`Activated ${result.id} v${result.version}. Recent affected items queued.`);await loadRecipes();});

async function loadCalendarMonths(){const d=await api('/api/extraction/months');for(const id of ['extraction-month','coverage-group','date_group','analysis-scope','corpus-month','stories-month']){const select=$(id);if(!select)continue;for(const o of [...select.options])if(o.dataset.month)o.remove();for(const m of d.months){const o=new Option(m.label+' · '+m.total.toLocaleString()+' articles',m.month);o.dataset.month='true';select.add(o);}const current=[...select.options].find(o=>o.value==='recent');if(current)current.textContent='Current month · '+d.current.label;if(id==='corpus-month'&&!select.dataset.initialized){select.value=d.current.month;select.dataset.initialized='1';}}}
handle(loadCalendarMonths)();
