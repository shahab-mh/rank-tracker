/* رنک ترکر - نسخه مرورگری. همه داده‌ها در localStorage همین مرورگر ذخیره می‌شود. */
'use strict';
const $ = id => document.getElementById(id);
const fa = n => n == null ? '' : Number(n).toLocaleString('fa-IR', {maximumFractionDigits: 1});
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
const nowIso = () => new Date().toISOString();
const devName = d => d === 'mobile' ? 'موبایل' : d === 'desktop' ? 'دسکتاپ' : d;
const sleep = ms => new Promise(r => setTimeout(r, ms));

// ============================================================ storage
const KEY = 'rank_tracker_v1';
const DEFAULT = {projects: [], runs: [], checks: [], gsc: [], nextId: 1,
  settings: {provider: 'demo', samples: 3, depth: 100, dfs_login: '', dfs_password: '', gsc_client_id: ''}};
let S;
function load() {
  try { S = JSON.parse(localStorage.getItem(KEY)) || structuredClone(DEFAULT); }
  catch { S = structuredClone(DEFAULT); }
  S.settings = {...DEFAULT.settings, ...S.settings};
  for (const k of ['projects', 'runs', 'checks', 'gsc']) S[k] ||= [];
}
function save() {
  try { localStorage.setItem(KEY, JSON.stringify(S)); }
  catch (e) { toast('حافظه مرورگر پر شده؛ از تنظیمات فایل پشتیبان بگیرید و پروژه‌های قدیمی را حذف کنید.', 8000); throw e; }
}
const newId = () => S.nextId++;
load();

// ============================================================ helpers
function normDomain(d) {
  d = String(d || '').trim().toLowerCase();
  if (d.includes('://')) { try { d = new URL(d).hostname; } catch { /* ignore */ } }
  return d.split('/')[0].split(':')[0].replace(/^www\./, '');
}
function urlMatches(url, domain, subs = true) {
  const h = normDomain(url), d = normDomain(domain);
  return h === d || (subs && h.endsWith('.' + d));
}
function median(a) {
  if (!a.length) return null;
  const s = [...a].sort((x, y) => x - y), m = s.length >> 1;
  return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2;
}
function toast(msg, ms = 3500) {
  const t = document.createElement('div'); t.className = 'toast'; t.textContent = msg;
  document.body.appendChild(t); setTimeout(() => t.remove(), ms);
}
const locLabel = l => l.label || l.name || `${l.lat.toFixed(4)},${l.lng.toFixed(4)}`;

// ============================================================ providers
function hash(str) { let h = 2166136261; for (const c of str) { h ^= c.charCodeAt(0); h = Math.imul(h, 16777619); } return h >>> 0; }
const demoProvider = {
  name: 'demo',
  async search(kw, loc, lang, country, device, depth, domain) {
    await sleep(40);
    const seed = hash(`${kw}|${locLabel(loc)}|${device}`), base = 1 + seed % 35;
    const pos = Math.max(1, base + [-1, 0, 0, 1][Math.floor(Math.random() * 4)]);
    const res = [{type: 'featured_snippet', organic_rank: null, absolute_rank: 1, url: 'https://wiki.example/x'}];
    for (let i = 1; i <= Math.min(depth, 50); i++)
      res.push({type: 'organic', organic_rank: i, absolute_rank: i + 1,
        url: i === pos ? `https://${domain}/p/${1 + seed % 3}` : `https://site${i}.example/`});
    return res;
  },
};
const dfsProvider = {
  name: 'dataforseo',
  async search(kw, loc, lang, country, device, depth) {
    const s = S.settings;
    const task = {keyword: kw, language_code: lang, device, os: device === 'mobile' ? 'android' : 'windows', depth};
    if (loc.lat != null) task.location_coordinate = `${loc.lat},${loc.lng}`;
    else task.location_name = loc.name;
    let r;
    try {
      r = await fetch('https://api.dataforseo.com/v3/serp/google/organic/live/advanced', {
        method: 'POST', body: JSON.stringify([task]),
        headers: {'Authorization': 'Basic ' + btoa(unescape(encodeURIComponent(`${s.dfs_login}:${s.dfs_password}`))), 'Content-Type': 'application/json'}});
    } catch (e) { throw new Error('اتصال به DataForSEO برقرار نشد (اینترنت، فیلتر یا مسدود بودن درخواست از مرورگر)'); }
    if (r.status === 401) throw new Error('Login یا Password اشتباه است');
    const d = await r.json();
    const t = d.tasks?.[0];
    if (!t || t.status_code !== 20000) throw new Error(`DataForSEO: ${t?.status_message || d.status_message || r.status}`);
    return (t.result?.[0]?.items || []).map(it => ({
      type: it.type, organic_rank: it.type === 'organic' ? it.rank_group : null,
      absolute_rank: it.rank_absolute, url: it.url || ''}));
  },
};
async function testDfs() {
  saveSettings(true);
  if (!S.settings.dfs_login || !S.settings.dfs_password) return toast('Login و Password را وارد کنید');
  try {
    const r = await fetch('https://api.dataforseo.com/v3/appendix/user_data', {
      headers: {'Authorization': 'Basic ' + btoa(unescape(encodeURIComponent(`${S.settings.dfs_login}:${S.settings.dfs_password}`)))}});
    const d = await r.json();
    if (d.status_code !== 20000) throw new Error(d.status_message);
    const bal = d.tasks?.[0]?.result?.[0]?.money?.balance;
    toast(`اتصال برقرار است ✓${bal != null ? ` · موجودی: ${bal}$` : ''}`, 6000);
  } catch (e) { toast('اتصال ناموفق: ' + e.message, 8000); }
}

// ============================================================ navigation
let current = null, editing = null, locs = [], map, markers = [], lastRows = [], chart, running = false, stopRun = false;
function show(tab) {
  ['projects', 'view', 'editor', 'settings', 'help'].forEach(t => $('tab-' + t).classList.toggle('hidden', t !== tab));
  document.querySelectorAll('nav button').forEach(b => b.classList.toggle('active',
    b.dataset.tab === tab || (b.dataset.tab === 'projects' && (tab === 'view' || tab === 'editor'))));
  if (tab === 'projects') renderProjects();
  if (tab === 'settings') loadSettings();
  window.scrollTo(0, 0);
}
document.querySelectorAll('nav button').forEach(b => b.onclick = () => show(b.dataset.tab));

// ============================================================ projects
function renderProjects() {
  $('first-banner').classList.toggle('hidden', S.projects.length > 0);
  $('plist').innerHTML = S.projects.length ? S.projects.map(p => `
    <div class="pitem" onclick="openProject(${p.id})"><b>${esc(p.name)}</b>
      <span class="muted ltr" style="display:block">${esc(p.domain)}</span>
      <span class="hint">${fa(p.keywords.length)} کلمه · ${fa(p.locations.length)} لوکیشن · ${p.devices.map(devName).join('، ')}</span></div>`).join('')
    : '<p class="muted">هنوز پروژه‌ای ندارید.</p>';
}
function openProject(id) {
  current = S.projects.find(p => p.id === id);
  if (!current) return show('projects');
  $('v-title').textContent = current.name; $('v-domain').textContent = current.domain;
  $('chart-card').style.display = 'none';
  $('demo-banner').classList.toggle('hidden', S.settings.provider !== 'demo');
  if (!running) $('job').classList.add('hidden');
  show('view'); loadResults();
}

// ============================================================ editor
function openEditor(p = null) {
  editing = p;
  $('e-title').textContent = p ? 'ویرایش پروژه' : 'پروژه جدید';
  $('e-name').value = p?.name || ''; $('e-domain').value = p?.domain || '';
  $('e-lang').value = p?.language || 'fa'; $('e-country').value = p?.country || 'ir';
  $('e-mobile').checked = p ? p.devices.includes('mobile') : true;
  $('e-desktop').checked = p ? p.devices.includes('desktop') : false;
  $('e-subs').checked = p ? p.include_subdomains : true;
  $('e-gsc').value = p?.gsc_property || '';
  $('e-keywords').value = (p?.keywords || []).join('\n'); kwCount();
  $('e-delete').classList.toggle('hidden', !p);
  locs = structuredClone(p?.locations || []);
  show('editor');
  setTimeout(initMap, 50);
}
function cancelEdit() { editing ? openProject(editing.id) : show('projects'); }
function kwCount() { $('kw-count').textContent = `${fa($('e-keywords').value.split('\n').filter(s => s.trim()).length)} کلمه`; }
$('e-keywords').oninput = kwCount;

function initMap() {
  if (typeof L === 'undefined') { $('map').classList.add('hidden'); $('map-off').classList.remove('hidden'); renderLocs(); return; }
  if (!map) {
    L.Icon.Default.imagePath = 'vendor/images/';
    map = L.map('map').setView([32.4, 53.7], 5);
    L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {maxZoom: 19, attribution: '© OpenStreetMap'}).addTo(map);
    map.on('click', e => {
      locs.push({label: $('l-label').value.trim() || `نقطه ${locs.length + 1}`, lat: +e.latlng.lat.toFixed(6), lng: +e.latlng.lng.toFixed(6)});
      $('l-label').value = ''; renderLocs();
    });
  }
  map.invalidateSize(); renderLocs();
  const pts = locs.filter(l => l.lat != null);
  if (pts.length) map.fitBounds(L.latLngBounds(pts.map(l => [l.lat, l.lng])).pad(0.5), {maxZoom: 12});
}
function renderLocs() {
  markers.forEach(m => m.remove()); markers = [];
  if (map) locs.forEach(l => { if (l.lat != null) markers.push(L.marker([l.lat, l.lng]).addTo(map).bindTooltip(l.label)); });
  $('e-locs').innerHTML = locs.map((l, i) => `<span class="chip">${esc(l.label)}
    <span class="muted ltr">${l.lat != null ? l.lat.toFixed(3) + ', ' + l.lng.toFixed(3) : esc(l.name)}</span>
    <button onclick="locs.splice(${i},1);renderLocs()">×</button></span>`).join('') || '<span class="muted">هنوز نقطه‌ای اضافه نشده</span>';
}
function toLatinDigits(s) { return s.replace(/[۰-۹]/g, d => '۰۱۲۳۴۵۶۷۸۹'.indexOf(d)).replace(/[٠-٩]/g, d => '٠١٢٣٤٥٦٧٨٩'.indexOf(d)); }
function addCoordLoc() {
  const m = toLatinDigits($('l-coord').value).match(/(-?\d+(?:\.\d+)?)\s*[,،\s]\s*(-?\d+(?:\.\d+)?)/);
  if (!m || Math.abs(+m[1]) > 90 || Math.abs(+m[2]) > 180) return toast('مختصات معتبر نیست. مثال: 35.7575, 51.4106');
  locs.push({label: $('l-label').value.trim() || `نقطه ${locs.length + 1}`, lat: +(+m[1]).toFixed(6), lng: +(+m[2]).toFixed(6)});
  $('l-coord').value = ''; $('l-label').value = ''; renderLocs();
  if (map) map.setView([+m[1], +m[2]], 11);
}
function addNamedLoc() {
  const name = $('l-name').value.trim(); if (!name) return;
  locs.push({label: $('l-label').value.trim() || name.split(',')[0], name});
  $('l-name').value = ''; $('l-label').value = ''; renderLocs();
}
function saveProject() {
  const devices = [$('e-mobile').checked && 'mobile', $('e-desktop').checked && 'desktop'].filter(Boolean);
  const name = $('e-name').value.trim(), domain = normDomain($('e-domain').value);
  if (!name || !domain) return toast('نام و دامنه الزامی است');
  if (!devices.length) return toast('حداقل یک دستگاه انتخاب کنید');
  const data = {name, domain, language: $('e-lang').value.trim() || 'fa', country: $('e-country').value.trim().toLowerCase() || 'ir',
    devices, include_subdomains: $('e-subs').checked, gsc_property: $('e-gsc').value.trim(),
    keywords: [...new Set($('e-keywords').value.split('\n').map(s => s.trim()).filter(Boolean))], locations: locs};
  let p;
  if (editing) { p = Object.assign(editing, data); } else { p = {id: newId(), ...data}; S.projects.push(p); }
  save(); toast('ذخیره شد'); openProject(p.id);
}
function deleteProject() {
  if (!confirm(`پروژه «${editing.name}» و همه تاریخچه آن حذف شود؟`)) return;
  const id = editing.id;
  S.projects = S.projects.filter(p => p.id !== id);
  S.runs = S.runs.filter(r => r.project_id !== id);
  S.checks = S.checks.filter(c => c.project_id !== id);
  S.gsc = S.gsc.filter(g => g.project_id !== id);
  save(); show('projects');
}

// ============================================================ run
async function runCheck() {
  if (running) return;
  const proj = current;
  if (!proj.keywords.length || !proj.locations.length) return toast('حداقل یک کلمه کلیدی و یک لوکیشن لازم است');
  const s = S.settings;
  const provider = s.provider === 'dataforseo' ? dfsProvider : demoProvider;
  if (provider === dfsProvider && (!s.dfs_login || !s.dfs_password)) { toast('اطلاعات DataForSEO را در تنظیمات وارد کنید'); return show('settings'); }
  const samples = Math.max(1, +s.samples || 1), depth = Math.min(100, Math.max(10, +s.depth || 100));
  const jobs = [];
  for (const loc of proj.locations) for (const dev of proj.devices) for (const kw of proj.keywords)
    for (let smp = 1; smp <= samples; smp++) jobs.push({loc, dev, kw, smp});
  if (provider === dfsProvider && !confirm(`${fa(jobs.length)} جستجو انجام می‌شود (هزینه از حساب DataForSEO شما کسر می‌شود). ادامه؟`)) return;

  running = true; stopRun = false;
  const run = {id: newId(), project_id: proj.id, started: nowIso(), finished: null, provider: provider.name};
  S.runs.push(run); save();
  const errors = []; let done = 0;
  $('job').classList.remove('hidden'); $('run-btn').disabled = true; $('stop-btn').classList.remove('hidden');
  const update = cur => {
    $('job-text').textContent = `در حال بررسی ${fa(done)} از ${fa(jobs.length)}${cur ? ' — ' + cur : ''}`;
    $('job-bar').style.width = (100 * done / jobs.length) + '%';
    $('job-errors').innerHTML = errors.slice(-5).map(esc).join('<br>') + (errors.length > 5 ? `<br>… و ${fa(errors.length - 5)} خطای دیگر` : '');
  };
  update();
  let idx = 0;
  const worker = async () => {
    while (idx < jobs.length && !stopRun) {
      const {loc, dev, kw, smp} = jobs[idx++];
      const label = locLabel(loc);
      update(`${kw} | ${label} | ${devName(dev)}`);
      const c = {run_id: run.id, project_id: proj.id, ts: nowIso(), keyword: kw, location: label, device: dev, sample: smp,
        organic_rank: null, absolute_rank: null, ranked_url: null, serp_features: [], error: null};
      try {
        const res = await provider.search(kw, loc, proj.language, proj.country, dev, depth, proj.domain);
        const hit = res.find(r => r.type === 'organic' && urlMatches(r.url, proj.domain, proj.include_subdomains));
        if (hit) Object.assign(c, {organic_rank: hit.organic_rank, absolute_rank: hit.absolute_rank, ranked_url: hit.url});
        c.serp_features = [...new Set(res.filter(r => r.type !== 'organic').map(r => r.type))].sort();
      } catch (e) {
        c.error = String(e.message || e).slice(0, 300); errors.push(`${kw} @ ${label} [${devName(dev)}]: ${c.error}`);
        if (/Login یا Password|اتصال به DataForSEO/.test(c.error)) stopRun = true;
      }
      S.checks.push(c); done++; update();
    }
  };
  try {
    await Promise.all(Array.from({length: provider === demoProvider ? 4 : 3}, worker));
    run.finished = nowIso(); save();
    $('job-text').textContent = stopRun ? `متوقف شد (${fa(done)} از ${fa(jobs.length)})` : 'تمام شد ✓';
  } catch (e) {
    $('job-text').textContent = 'خطا: ' + e.message;
  } finally {
    running = false; $('run-btn').disabled = false; $('stop-btn').classList.add('hidden');
    if (current?.id === proj.id) loadResults();
  }
}

// ============================================================ results
function aggregate(checks) {
  const g = {};
  for (const c of checks) (g[`${c.keyword}\u0001${c.location}\u0001${c.device}`] ||= []).push(c);
  const out = {};
  for (const [k, rs] of Object.entries(g)) {
    const org = rs.map(r => r.organic_rank).filter(x => x != null), ab = rs.map(r => r.absolute_rank).filter(x => x != null);
    const urls = rs.map(r => r.ranked_url).filter(Boolean);
    const cnt = {}; urls.forEach(u => cnt[u] = (cnt[u] || 0) + 1);
    out[k] = {median: median(org), min: org.length ? Math.min(...org) : null, max: org.length ? Math.max(...org) : null,
      absolute: median(ab), found: org.length, samples: rs.length, errors: rs.filter(r => r.error).length,
      url: urls.length ? Object.keys(cnt).sort((a, b) => cnt[b] - cnt[a])[0] : null,
      features: rs.find(r => !r.error)?.serp_features || [], ts: rs.map(r => r.ts).sort().pop()};
  }
  return out;
}
function gscSummary(pid) {
  const since = new Date(Date.now() - 31 * 864e5).toISOString().slice(0, 10), g = {};
  for (const r of S.gsc) if (r.project_id === pid && r.day >= since) {
    const k = `${r.query.toLowerCase()}\u0001${r.device}`, a = g[k] ||= {i: 0, p: 0, c: 0};
    a.i += r.impressions; a.p += r.position * r.impressions; a.c += r.clicks;
  }
  const out = {}; for (const [k, a] of Object.entries(g)) out[k] = {position: a.i ? a.p / a.i : null, impressions: a.i, clicks: a.c};
  return out;
}
function loadResults() {
  const runs = S.runs.filter(r => r.project_id === current.id && r.finished).sort((a, b) => b.id - a.id);
  const run = runs[0];
  const prevRun = run && runs.slice(1).find(r => r.provider === run.provider);  // فقط مقایسه با همان سرویس
  const cur = run ? aggregate(S.checks.filter(c => c.run_id === run.id)) : {};
  const prev = prevRun ? aggregate(S.checks.filter(c => c.run_id === prevRun.id)) : {};
  const g = gscSummary(current.id);
  lastRows = Object.entries(cur).sort(([a], [b]) => a.localeCompare(b, 'fa')).map(([k, v]) => {
    const [keyword, location, device] = k.split('\u0001');
    return {keyword, location, device, ...v, previous: prev[k]?.median ?? null, hasPrev: !!prevRun, gsc: g[`${keyword.toLowerCase()}\u0001${device}`]};
  });
  $('empty').classList.toggle('hidden', lastRows.length > 0);
  $('run-info').textContent = run ? `آخرین بررسی: ${new Date(run.finished).toLocaleString('fa-IR')} · سرویس: ${run.provider}${run.provider === 'demo' ? ' (ساختگی)' : ''}` : '';
  const opts = (sel, vals, all, fmt = v => v) => { const c = $(sel).value;
    $(sel).innerHTML = `<option value="">${all}</option>` + [...new Set(vals)].map(v => `<option value="${esc(v)}">${esc(fmt(v))}</option>`).join('');
    $(sel).value = [...$(sel).options].some(o => o.value === c) ? c : ''; };
  opts('f-loc', lastRows.map(r => r.location), 'همه لوکیشن‌ها');
  opts('f-dev', lastRows.map(r => r.device), 'همه دستگاه‌ها', devName);
  renderRows();
}
const FEAT = {featured_snippet: 'Featured snippet', people_also_ask: 'PAA', local_pack: 'Local pack', answer_box: 'Answer box',
  top_stories: 'Top stories', images: 'Images', video: 'Video', ai_overview: 'AI Overview', knowledge_graph: 'Knowledge panel',
  related_searches: 'Related searches', shopping: 'Shopping', paid: 'Ads', map: 'Map'};
function filteredRows() {
  const loc = $('f-loc').value, dev = $('f-dev').value, q = $('f-q').value.trim().toLowerCase();
  return lastRows.filter(r => (!loc || r.location === loc) && (!dev || r.device === dev) && (!q || r.keyword.toLowerCase().includes(q)));
}
function renderRows() {
  $('results').innerHTML = filteredRows().map(r => {
    let delta = '';
    if (r.median != null && r.previous != null && r.median !== r.previous) {
      const diff = r.previous - r.median;
      delta = `<span class="delta ${diff > 0 ? 'up' : 'down'}">${diff > 0 ? '▲' : '▼'} ${fa(Math.abs(diff))}</span>`;
    } else if (r.median != null && r.previous == null && r.hasPrev) delta = '<span class="delta up">جدید</span>';
    else if (r.median == null && r.previous != null) delta = '<span class="delta down">خارج شد</span>';
    const rank = r.median != null ? `<span class="num">${fa(r.median)}</span>` + (r.found < r.samples ? ` <span class="tag" title="در بعضی نمونه‌ها پیدا نشد">${fa(r.found)}/${fa(r.samples)}</span>` : '')
      : r.errors === r.samples ? '<span class="tag">خطا</span>' : `<span class="muted">+${fa(S.settings.depth)}</span>`;
    const spread = r.min != null && r.min !== r.max ? `${fa(r.min)}–${fa(r.max)}` : r.min != null ? '<span class="muted">ثابت</span>' : '';
    const g = r.gsc ? `${fa(r.gsc.position)} <span class="hint">(${fa(r.gsc.impressions)} نمایش)</span>` : '<span class="muted">—</span>';
    const feats = r.features.map(f => `<span class="tag" style="background:var(--primary-weak);color:var(--primary)">${esc(FEAT[f] || f)}</span>`).join(' ');
    return `<tr onclick="showHistory(${lastRows.indexOf(r)})"><td><b>${esc(r.keyword)}</b></td><td>${esc(r.location)}</td><td>${devName(r.device)}</td>
      <td>${rank}</td><td>${delta}</td><td>${spread}</td><td>${fa(r.absolute)}</td><td>${g}</td>
      <td>${r.url ? `<a class="url" href="${esc(r.url)}" target="_blank" rel="noopener" onclick="event.stopPropagation()">${esc(r.url.replace(/^https?:\/\//, ''))}</a>` : ''}</td>
      <td>${feats}</td></tr>`;
  }).join('');
}
['f-loc', 'f-dev', 'f-q'].forEach(id => $(id).oninput = renderRows);

function showHistory(i) {
  const r = lastRows[i];
  const runs = {}, latest = S.runs.filter(x => x.project_id === current.id && x.finished).sort((a, b) => b.id - a.id)[0];
  const okRuns = new Set(S.runs.filter(x => x.project_id === current.id && x.provider === latest?.provider).map(x => x.id));
  for (const c of S.checks) if (okRuns.has(c.run_id) && c.project_id === current.id && c.keyword === r.keyword && c.location === r.location && c.device === r.device)
    (runs[c.run_id] ||= {ts: c.ts, ranks: []}).ranks.push(c.organic_rank);
  const serp = Object.values(runs).map(v => ({x: new Date(v.ts).getTime(), y: median(v.ranks.filter(x => x != null))}));
  const days = {};
  for (const g of S.gsc) if (g.project_id === current.id && g.device === r.device && g.query.toLowerCase() === r.keyword.toLowerCase()) {
    const a = days[g.day] ||= {i: 0, p: 0}; a.i += g.impressions; a.p += g.position * g.impressions;
  }
  const gsc = Object.entries(days).sort().map(([d, a]) => ({x: new Date(d).getTime(), y: a.p / a.i}));
  $('chart-card').style.display = ''; $('chart-title').textContent = `${r.keyword} — ${r.location} — ${devName(r.device)}`;
  const css = getComputedStyle(document.documentElement), c = v => css.getPropertyValue(v).trim();
  const xs = [...serp, ...gsc].map(p => p.x), DAY = 864e5;
  chart?.destroy();
  chart = new Chart($('chart'), {
    type: 'line',
    data: {datasets: [
      {label: 'رتبه (بررسی SERP)', data: serp, borderColor: c('--primary'), backgroundColor: c('--primary'), tension: .2, spanGaps: true},
      {label: 'میانگین Search Console', data: gsc, borderColor: c('--good'), backgroundColor: c('--good'), borderDash: [4, 4], pointRadius: 2, tension: .2}]},
    options: {maintainAspectRatio: false, interaction: {mode: 'nearest', intersect: false},
      scales: {y: {reverse: true, min: 1, ticks: {color: c('--muted'), precision: 0}, grid: {color: c('--line')}, title: {display: true, text: 'رتبه', color: c('--muted')}},
        x: {type: 'linear', min: Math.min(...xs) - DAY / 2, max: Math.max(...xs) + DAY / 2,
          ticks: {color: c('--muted'), callback: v => new Date(v).toLocaleDateString('fa-IR')}, grid: {display: false}}},
      plugins: {legend: {labels: {color: c('--text'), font: {family: 'Vazirmatn'}}},
        tooltip: {callbacks: {title: it => new Date(it[0].parsed.x).toLocaleString('fa-IR'), label: it => `${it.dataset.label}: ${fa(it.parsed.y)}`}}}}});
  $('chart-card').scrollIntoView({behavior: 'smooth', block: 'nearest'});
}
function download(name, text, type) {
  const a = document.createElement('a'); a.href = URL.createObjectURL(new Blob([text], {type})); a.download = name; a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}
function exportCsv() {
  const head = ['keyword', 'location', 'device', 'rank', 'previous', 'min', 'max', 'absolute_rank', 'gsc_position', 'gsc_impressions', 'url', 'serp_features', 'checked_at'];
  const q = v => `"${String(v ?? '').replace(/"/g, '""')}"`;
  const lines = filteredRows().map(r => [r.keyword, r.location, r.device, r.median, r.previous, r.min, r.max, r.absolute,
    r.gsc?.position?.toFixed(1), r.gsc?.impressions, r.url, r.features.join(' '), r.ts].map(q).join(','));
  download(`${current.name}-ranks.csv`, '﻿' + [head.join(','), ...lines].join('\n'), 'text/csv');
}

// ============================================================ Search Console (OAuth in browser)
const COUNTRY3 = {ir: 'irn', us: 'usa', de: 'deu', gb: 'gbr', tr: 'tur', ae: 'are', ca: 'can', fr: 'fra', iq: 'irq', af: 'afg', au: 'aus', nl: 'nld', se: 'swe'};
let gscToken = null, gscTokenExp = 0;
function gscClientId() { return S.settings.gsc_client_id || window.RT_CONFIG?.GSC_CLIENT_ID || ''; }
function getGscToken() {
  return new Promise((resolve, reject) => {
    if (gscToken && Date.now() < gscTokenExp) return resolve(gscToken);
    if (!window.google?.accounts?.oauth2) return reject(new Error('کتابخانه ورود گوگل بارگذاری نشد (اینترنت یا فیلتر)'));
    if (!gscClientId()) return reject(new Error('OAuth Client ID برای Search Console تنظیم نشده (تنظیمات ← Google Search Console)'));
    const client = google.accounts.oauth2.initTokenClient({
      client_id: gscClientId(), scope: 'https://www.googleapis.com/auth/webmasters.readonly',
      callback: r => { if (r.error) return reject(new Error(r.error_description || r.error));
        gscToken = r.access_token; gscTokenExp = Date.now() + (r.expires_in - 60) * 1000; resolve(gscToken); },
      error_callback: e => reject(new Error(e.message || e.type || 'ورود لغو شد')),
    });
    client.requestAccessToken();
  });
}
async function gscFetch(path, body) {
  const r = await fetch('https://www.googleapis.com/webmasters/v3/' + path, {
    method: body ? 'POST' : 'GET', body: body ? JSON.stringify(body) : undefined,
    headers: {'Authorization': 'Bearer ' + gscToken, 'Content-Type': 'application/json'}});
  const d = await r.json();
  if (!r.ok) throw Object.assign(new Error(d.error?.message || r.statusText), {status: r.status});
  return d;
}
async function syncGsc() {
  const proj = current;
  $('gsc-btn').disabled = true; $('gsc-btn').textContent = 'در حال دریافت…';
  try {
    await getGscToken();
    let site = proj.gsc_property;
    if (!site) {
      const sites = (await gscFetch('sites')).siteEntry || [];
      const match = sites.find(s => s.siteUrl === `sc-domain:${proj.domain}`) || sites.find(s => s.siteUrl.startsWith('http') && normDomain(s.siteUrl) === proj.domain);
      if (!match) throw new Error(`حساب گوگل شما به ${proj.domain} در Search Console دسترسی ندارد. پراپرتی‌های موجود: ${sites.map(s => s.siteUrl).join('، ') || 'هیچ'}`);
      site = match.siteUrl;
    }
    const end = new Date(Date.now() - 3 * 864e5), start = new Date(end - 89 * 864e5);
    const wanted = new Set(proj.keywords.map(k => k.toLowerCase()));
    const country = COUNTRY3[proj.country];
    const fresh = [];
    for (const dev of proj.devices) {
      const filters = [{dimension: 'device', operator: 'equals', expression: dev.toUpperCase()}];
      if (country) filters.push({dimension: 'country', operator: 'equals', expression: country});
      for (let startRow = 0; ; startRow += 25000) {
        const d = await gscFetch(`sites/${encodeURIComponent(site)}/searchAnalytics/query`, {
          startDate: start.toISOString().slice(0, 10), endDate: end.toISOString().slice(0, 10),
          dimensions: ['date', 'query', 'page'], rowLimit: 25000, startRow, dimensionFilterGroups: [{filters}]});
        const rows = d.rows || [];
        for (const r of rows) if (wanted.has(r.keys[1].toLowerCase()))
          fresh.push({project_id: proj.id, day: r.keys[0], query: r.keys[1], page: r.keys[2], device: dev,
            clicks: r.clicks, impressions: r.impressions, position: r.position});
        if (rows.length < 25000) break;
      }
    }
    S.gsc = S.gsc.filter(g => g.project_id !== proj.id).concat(fresh); save();
    toast(`${fa(fresh.length)} ردیف از ${site} دریافت شد`);
    if (current?.id === proj.id) loadResults();
  } catch (e) {
    toast(e.status === 403 ? `دسترسی رد شد: حساب گوگل شما به این پراپرتی دسترسی ندارد. (${e.message})` : e.message, 9000);
  } finally { $('gsc-btn').disabled = false; $('gsc-btn').textContent = 'دریافت از Search Console'; }
}

// ============================================================ settings & backup
function loadSettings() {
  const s = S.settings;
  $('s-provider').value = s.provider; $('s-samples').value = s.samples; $('s-depth').value = s.depth;
  $('s-dfs-login').value = s.dfs_login; $('s-dfs-pass').value = s.dfs_password; $('s-gsc-client').value = s.gsc_client_id;
  $('s-gsc-state').textContent = gscClientId() ? (s.gsc_client_id ? 'از Client ID شما استفاده می‌شود.' : 'از Client ID پیش‌فرض سایت استفاده می‌شود.')
    : 'Client ID تنظیم نشده؛ دریافت از Search Console تا وقتی تنظیم نشود کار نمی‌کند.';
  const kb = new Blob([localStorage.getItem(KEY) || '']).size / 1024;
  $('storage-size').textContent = `حجم داده ذخیره‌شده: ${fa(kb > 1024 ? kb / 1024 : kb)} ${kb > 1024 ? 'مگابایت' : 'کیلوبایت'} (حداکثر حدود ۵ مگابایت)`;
}
function saveSettings(silent = false) {
  Object.assign(S.settings, {provider: $('s-provider').value, samples: Math.min(10, Math.max(1, +$('s-samples').value || 3)),
    depth: Math.min(100, Math.max(10, +$('s-depth').value || 100)), dfs_login: $('s-dfs-login').value.trim(),
    dfs_password: $('s-dfs-pass').value.trim(), gsc_client_id: $('s-gsc-client').value.trim()});
  if (S.settings.provider === 'dataforseo' && (!S.settings.dfs_login || !S.settings.dfs_password) && !silent)
    toast('برای استفاده از DataForSEO، Login و Password را وارد کنید');
  save(); if (!silent) { toast('تنظیمات ذخیره شد'); loadSettings(); }
}
function exportBackup() {
  const data = structuredClone(S); data.settings = {...data.settings, dfs_password: ''};
  download(`rank-tracker-backup-${new Date().toISOString().slice(0, 10)}.json`, JSON.stringify(data), 'application/json');
}
async function importBackup(file) {
  if (!file) return;
  try {
    const d = JSON.parse(await file.text());
    if (!Array.isArray(d.projects)) throw new Error('فایل پشتیبان معتبر نیست');
    if (!confirm('داده‌های فعلی این مرورگر با فایل پشتیبان جایگزین شود؟')) return;
    const keep = {dfs_password: S.settings.dfs_password};
    S = d; S.settings = {...DEFAULT.settings, ...d.settings, ...(d.settings?.dfs_password ? {} : keep)};
    save(); toast('بازگردانی شد'); show('projects');
  } catch (e) { toast(e.message); }
}

renderProjects();
