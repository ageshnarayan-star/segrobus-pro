'use strict';
const $ = (s, r = document) => r.querySelector(s);
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const app = $('#app');
const S = { me: null, portal: 'college', orgs: [], tab: 'live', timer: null };

async function api(path, body) {
  try {
    const r = await fetch(path, body === undefined ? {} : { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body) });
    let j = {}; try { j = await r.json(); } catch (e) {}
    return [r.status, j];
  } catch (e) { return [0, { message: 'No connection to the server' }]; }
}
function toast(msg, bad) {
  const t = document.createElement('div'); t.className = 'toast' + (bad ? ' bad' : ''); t.textContent = msg;
  $('#toasts').append(t); setTimeout(() => t.remove(), 4500);
}
const ago = iso => { if (!iso) return null; const m = Math.round((Date.now() - new Date(iso).getTime()) / 60000); return m < 1 ? 'just now' : m < 60 ? m + ' min ago' : Math.round(m / 60) + ' h ago'; };
const online = iso => iso && (Date.now() - new Date(iso).getTime()) < 10 * 60000;
const hhmm = iso => iso ? iso.slice(11, 16) : '';
const origin = r => String(r || '').split('-')[0].replace(/\s*\(via\)/i, '').trim();
const tel = p => p ? `<a href="tel:${esc(p)}">${esc(p)}</a>` : '';
function stopTimer() { clearInterval(S.timer); S.timer = null; }
function poll(fn, ms) { stopTimer(); S.timer = setInterval(() => { if (!document.hidden) fn(); }, ms); }

/* ---------- seat map: taken seats fill from the front; counts, not seat numbers ---------- */
function seatMap(capacity, taken, mine) {
  const cols = capacity > 24 ? 4 : 4; const cells = [];
  let n = 0;
  for (let i = 0; i < capacity; i++) {
    const col = i % cols; if (col === 2 && i % cols === 2) cells.push('<div class="seat gap" aria-hidden="true"></div>');
    const cls = i < taken ? (mine && i === taken - 1 ? 'seat me' : 'seat t') : 'seat';
    cells.push(`<div class="${cls}"></div>`); n++;
  }
  return `<div class="bus" role="img" aria-label="${taken} of ${capacity} seats taken"><div class="seats" style="grid-template-columns:repeat(2,var(--s)) calc(var(--s)*.6) repeat(2,var(--s))">${cells.join('')}</div></div>`;
}
const legend = mine => `<div class="legend"><span><i style="background:var(--free)"></i>Free</span><span><i style="background:var(--taken)"></i>Taken</span>${mine ? '<span><i style="background:var(--amber)"></i>You</span>' : ''}</div>`;

/* ---------- sign in and create account (one sign-in for everyone) ---------- */
async function showAuth() {
  stopTimer();
  const [, demo] = await api('/api/demo');
  const seats = seatMap(24, 0, false);
  let mode = 'in', admin = false;
  app.innerHTML = `<main class="auth">
    <section class="hero">
      <div class="brand" style="color:#EAF0F6"><span class="dot"><svg viewBox="0 0 16 16"><rect x="2" y="2" width="12" height="9" rx="2" fill="#0F1B2D"/><rect x="4" y="12.5" width="8" height="2" rx="1" fill="#0F1B2D"/></svg></span>Segro Bus</div>
      <div><h1><span>One student.</span><span>One bus.</span><span>One seat.</span></h1>
      <p style="margin-top:1rem">Tap your ID card as you board. Your seat is counted, the bus is never over capacity, and your college sees the real demand on every route.</p></div>
      <div id="demoSeats">${seats}</div>
    </section>
    <section class="panel"><div style="max-width:410px;width:100%;margin:0 auto">
      <h2 id="ttl"></h2>
      <p class="muted small" id="sub" style="margin:.4rem 0 1rem"></p>
      <div class="seg two" id="seg" role="group" aria-label="Account"><button type="button" data-m="in">Sign in</button><button type="button" data-m="up">Create account</button></div>
      <form id="af" autocomplete="on">
        <label class="field hide" id="nameF"><span>Full name</span><input id="name" autocomplete="name"></label>
        <label class="field"><span id="idl">Email or ID</span><input id="lid" autocomplete="username" required></label>
        <label class="field"><span>Password</span><input id="pw" type="password" autocomplete="current-password" minlength="8" required></label>
        <button class="btn amber" id="go" style="width:100%"></button>
      </form>
      <p class="small muted" style="margin-top:1rem"><a href="#" id="swap"></a></p>
      ${demo.enabled ? `<div class="demo"><b>Prototype demo</b><p class="small muted">Sample data. Password for every demo account: <code>${esc(demo.password)}</code></p>
        <div class="row">${demo.accounts.map(a => `<button type="button" class="btn small ghost" data-d="${esc(a.login_id)}" data-p="${esc(a.portal)}" title="${esc(a.note)}">${esc(a.label)}</button>`).join('')}</div></div>` : ''}
    </div></section></main>`;
  const ds = $('#demoSeats'); const st = [...ds.querySelectorAll('.seat:not(.gap)')]; let k = 0;
  if (!matchMedia('(prefers-reduced-motion:reduce)').matches) {
    const iv = setInterval(() => { if (!$('#demoSeats')) return clearInterval(iv); if (k < 17) st[k++].classList.add('t'); else clearInterval(iv); }, 140);
  } else st.slice(0, 17).forEach(x => x.classList.add('t'));
  const render = () => {
    $('#ttl').textContent = admin ? 'Admin sign-in' : mode === 'up' ? 'Create your account' : 'Welcome back';
    $('#sub').textContent = admin ? 'For transport office administrators and door staff.' : mode === 'up' ? 'Free for riders. You choose your college or organisation after this.' : 'Sign in to see your bus and seat.';
    $('#seg').classList.toggle('hide', admin);
    for (const b of $('#seg').querySelectorAll('button')) b.setAttribute('aria-pressed', b.dataset.m === mode);
    $('#nameF').classList.toggle('hide', admin || mode !== 'up'); $('#name').required = !admin && mode === 'up';
    $('#go').textContent = admin ? 'Sign in as admin' : mode === 'up' ? 'Create account' : 'Sign in';
    $('#pw').autocomplete = mode === 'up' && !admin ? 'new-password' : 'current-password';
    $('#swap').textContent = admin ? 'Back to rider sign-in' : 'Administrator or door staff? Admin sign-in';
  };
  $('#seg').onclick = e => { const b = e.target.closest('button'); if (b) { mode = b.dataset.m; render(); } };
  $('#swap').onclick = e => { e.preventDefault(); admin = !admin; mode = 'in'; render(); };
  const submit = async (lid, pw, portal) => {
    const [s, d] = await api('/api/login', { login_id: lid, password: pw, portal });
    if (s === 200) return boot(); toast(d.message || 'Could not sign in', true);
  };
  $('#af').onsubmit = async e => {
    e.preventDefault(); const btn = $('#go'); btn.disabled = true;
    if (!admin && mode === 'up') {
      const [s, d] = await api('/api/register', { name: $('#name').value, login_id: $('#lid').value, password: $('#pw').value });
      if (s === 201) { toast('Welcome to Segro Bus'); await boot(); return; } toast(d.message, true);
    } else await submit($('#lid').value, $('#pw').value, admin ? 'admin' : 'member');
    btn.disabled = false;
  };
  app.querySelectorAll('[data-d]').forEach(b => b.onclick = () => submit(b.dataset.d, demo.password, b.dataset.p));
  render();
}

/* ---------- shell ---------- */
const LOGO = '<span class="dot"><svg viewBox="0 0 16 16"><rect x="2" y="2" width="12" height="9" rx="2" fill="#0F1B2D"/><rect x="4" y="12.5" width="8" height="2" rx="1" fill="#0F1B2D"/></svg></span>';
function shell(inner) {
  const m = S.me;
  app.innerHTML = `<header class="top"><div class="brand">${LOGO}Segro</div>
    <div class="org"><b>${esc(m.org ? m.org.name : 'No organisation yet')}</b><span class="muted small">${esc(m.name)} · ${esc(m.role === 'admin' ? 'administrator' : m.role)}</span></div>
    <button class="btn ghost small" id="out">Sign out</button></header><div class="wrap" id="view">${inner}</div>`;
  $('#out').onclick = async () => { await api('/api/logout', {}); S.me = null; stopTimer(); showAuth(); };
}
const TYPE = { college: 'College', private: 'Organisation', government: 'Public transport (sandbox)' };

/* ---------- onboarding: choose or add your college / organisation ---------- */
function showOnboarding() {
  stopTimer();
  shell(`<div class="stack" style="max-width:640px;margin:0 auto"><div><h2>Which college or organisation are you in?</h2>
    <p class="muted">Your buses, seats and notices come from your organisation. You can change this later.</p></div>
    <div class="card"><input id="oq" type="search" placeholder="Search by name, e.g. Sathyabama" autofocus><div id="olist" style="margin-top:.8rem"></div></div>
    <div class="card"><h3>Not listed? Add it</h3><p class="muted small">Add your college or organisation. You become its administrator and can then add buses and riders.</p>
      <form id="cf"><label class="field"><span>Name</span><input id="cn" required minlength="3" maxlength="80" placeholder="Greenfield Engineering College"></label>
      <label class="field"><span>Type</span><select id="ct"><option value="college">College or university</option><option value="private">Company, school or private shuttle</option><option value="government">Public transport (sandbox)</option></select></label>
      <button class="btn">Add and manage it</button></form></div></div>`);
  let t;
  const load = async () => {
    const [, list] = await api('/api/orgs?q=' + encodeURIComponent($('#oq').value));
    $('#olist').innerHTML = list.length ? list.map(o => `<div class="org-row" data-c="${esc(o.code)}"><div class="grow"><b>${esc(o.name)}</b><br><span class="muted small">${esc(TYPE[o.org_type])} · ${o.buses} ${o.buses === 1 ? 'bus' : 'buses'}</span></div>
      <div class="row" style="flex-wrap:nowrap">${o.needs_code ? `<input class="jc" placeholder="Join code" style="width:130px;min-height:38px" aria-label="Join code for ${esc(o.name)}">` : ''}<button class="btn small amber" data-join="${esc(o.code)}">Join</button></div></div>`).join('')
      : '<div class="empty">No match. Add your organisation below.</div>';
  };
  $('#oq').oninput = () => { clearTimeout(t); t = setTimeout(load, 250); };
  $('#olist').onclick = async e => { const b = e.target.closest('[data-join]'); if (!b) return;
    const jc = b.closest('.org-row').querySelector('.jc');
    const [s, d] = await api('/api/me/org/join', { org_code: b.dataset.join, join_code: jc ? jc.value : '' });
    toast(d.message, s >= 400); if (s < 400) boot(); };
  $('#cf').onsubmit = async e => { e.preventDefault(); const [s, d] = await api('/api/me/org/create', { name: $('#cn').value, org_type: $('#ct').value }); toast(d.message, s >= 400); if (s < 400) boot(); };
  load();
}

/* ---------- rider ---------- */
async function showRider() {
  let choosing = false;
  const draw = async () => {
    const [s, m] = await api('/api/me'); if (s !== 200) return boot(); S.me = m;
    if (!m.org) return showOnboarding();
    const [[, hist], [, notes]] = await Promise.all([api('/api/me/history'), api('/api/announcements')]);
    const b = m.bus, gov = m.org.type === 'government'; let body;
    const nb = Array.isArray(notes) && notes.length ? `<div class="card notice"><h3>Notices</h3><ul class="feed">${notes.map(n => `<li><span class="plate" style="font-size:.95rem">${n.bus_number ? esc(n.bus_number) : 'All'}</span><span>${esc(n.body)}</span><span class="muted small" style="margin-left:auto;white-space:nowrap">${esc(n.created_at.slice(5, 10))}</span></li>`).join('')}</ul></div>` : '';
    if (gov) {
      body = `<div class="card"><h2>Pass holder</h2><p class="muted">Open ridership: tap in on any registered bus until it is full.</p><p class="stat">${m.boardings_total}</p><p class="muted">boardings recorded</p></div>`;
    } else if (!b || choosing) {
      const [, list] = await api('/api/org/buses');
      body = `<div class="card"><h3>${b ? 'Change your bus' : 'Choose your bus'}</h3><p class="muted small">Pick the route you ride every day. Your transport office can change it later.</p>
        <input id="bq2" type="search" placeholder="Search ${list.length} routes by number or place" style="margin:.7rem 0"><div id="blist" class="blist"></div>${b ? '<button class="btn ghost small" id="cancelc" style="margin-top:.6rem">Keep my current bus</button>' : ''}</div>`;
      setTimeout(() => {
        const paint = () => { const q = ($('#bq2').value || '').toLowerCase();
          $('#blist').innerHTML = list.filter(x => !q || (x.bus_number + ' ' + (x.route || '') + ' ' + (x.vehicle_no || '')).toLowerCase().includes(q)).map(x => `<div class="bus-row"><span class="plate">${esc(x.bus_number)}</span><div class="grow"><b>${esc(origin(x.route) || 'Bus ' + x.bus_number)}</b><br><span class="muted small">${esc(x.vehicle_no || '')} · ${x.seats_left} of ${x.capacity} seats free now</span></div><button class="btn small amber" data-pick="${esc(x.bus_number)}">Select</button></div>`).join('') || '<div class="empty">No route matches.</div>'; };
        $('#bq2').oninput = paint; paint();
        $('#blist').onclick = async e => { const p = e.target.closest('[data-pick]'); if (!p) return; const [st2, d] = await api('/api/me/bus', { bus: p.dataset.pick }); toast(d.message, st2 >= 400); if (st2 < 400) { choosing = false; draw(); } };
        const c = $('#cancelc'); if (c) c.onclick = () => { choosing = false; draw(); };
      }, 0);
    } else {
      const left = b.seats_left, taken = b.capacity - left;
      body = `<div class="card board"><div><span class="muted small">Your bus</span><br><span class="plate big">${esc(b.bus_number)}</span></div>
        <div><p class="stat">${left}</p><p style="font-weight:600">${left === 1 ? 'seat' : 'seats'} left of ${b.capacity}</p>
        ${b.route ? `<p class="small muted" style="margin-top:.5rem">${esc(b.vehicle_no || '')} · ${esc(b.route.replace(/\s*-?\(via\)-?\s*/i, ' · via ').replace(/-/g, ' › '))}</p>` : ''}
        <p style="margin-top:.6rem"><span class="pill ${b.boarded_this_trip ? 'ok' : 'warn'}">${b.boarded_this_trip ? 'You are boarded for this trip' : 'Not boarded yet. Tap your card at the door.'}</span></p>
        <p style="margin-top:.6rem"><button class="btn small ghost" id="chg">Change bus</button></p></div></div>
        <div class="card"><h3>Seat map</h3><p class="muted small">Seats fill in tap order. They are counted, not numbered.</p><div style="margin-top:1rem">${seatMap(b.capacity, taken, b.boarded_this_trip)}</div>${legend(b.boarded_this_trip)}</div>`;
    }
    const cardInfo = gov ? '' : `<p class="small ${m.card_linked ? '' : 'muted'}" style="margin-top:.6rem"><span class="pill ${m.card_linked ? 'ok' : ''}">${m.card_linked ? 'ID card linked' : 'No ID card linked'}</span> ${m.card_linked ? '' : 'Ask your transport office to link your ID card.'}</p>`;
    const h = Array.isArray(hist) && hist.length ? `<div class="card"><h3>Recent rides</h3><p class="muted small">${m.rides_total} rides recorded</p><ul class="feed">${hist.map(x => `<li><span class="plate" style="font-size:1rem">${esc(x.bus_number)}</span><span>${esc(x.trip.slice(0, 10))} ${esc(x.trip.slice(11))}</span><span class="muted small" style="margin-left:auto">${esc(hhmm(x.boarded_at))}</span></li>`).join('')}</ul></div>` : '';
    shell(`<div class="stack"><h2>Hi, ${esc(m.name.split(' ')[0])}</h2>${nb}${body}${h}
      <div class="card"><h3>Your organisation</h3><p><b>${esc(m.org.name)}</b><br><span class="muted small">${esc(TYPE[m.org.type])}</span></p>${cardInfo}
      <button class="btn small ghost" id="leave" style="margin-top:.8rem">Change organisation</button></div></div>`);
    const chg = $('#chg'); if (chg) chg.onclick = () => { choosing = true; draw(); };
    $('#leave').onclick = async () => { if (!confirm('Leave ' + m.org.name + '? Your bus choice will be cleared.')) return; const [s2, d] = await api('/api/me/org/leave', {}); toast(d.message, s2 >= 400); if (s2 < 400) boot(); };
  };
  await draw(); poll(() => { if (!choosing) draw(); }, 8000);
}

/* ---------- staff + admin ---------- */
const TABS = [['live', 'Live'], ['buses', 'Buses'], ['riders', 'Riders'], ['notices', 'Notices'], ['insights', 'Insights'], ['setup', 'Setup']];
async function showStaff() {
  const admin = S.me.role === 'admin';
  const paint = async () => {
    const tabs = admin ? `<div class="tabs" role="tablist">${TABS.map(([k, l]) => `<button role="tab" aria-selected="${S.tab === k}" data-t="${k}">${l}</button>`).join('')}</div>` : '';
    shell(`${tabs}<div id="pane"></div>`);
    for (const b of app.querySelectorAll('.tabs button')) b.onclick = () => { S.tab = b.dataset.t; paint(); };
    if (!admin) S.tab = 'live';
    const fn = { live: tabLive, buses: tabBuses, riders: tabRiders, notices: tabNotices, insights: tabInsights, setup: tabSetup }[S.tab];
    await fn();
    if (S.tab === 'live') poll(refreshLive, 4000); else stopTimer();
  };
  await paint();
}

async function liveData() {
  const admin = S.me.role === 'admin';
  const [[, d], [, f], [, sm]] = await Promise.all([api('/api/dashboard'), api('/api/feed'), admin ? api('/api/admin/summary') : Promise.resolve([0, null])]);
  return [d, Array.isArray(f) ? f : [], sm];
}
function busCards(d) {
  const q = (S.q || '').toLowerCase();
  const list = d.buses.filter(b => !q || (b.bus_number + ' ' + (b.vehicle_no || '') + ' ' + (b.route || '')).toLowerCase().includes(q));
  if (!d.buses.length) return `<div class="empty">No buses yet. ${S.me.role === 'admin' ? 'Add one in the Buses tab.' : 'Ask an administrator to add buses.'}</div>`;
  if (!list.length) return '<div class="empty">No bus matches that search.</div>';
  return list.map(b => {
    const pct = b.capacity ? Math.round(100 * b.boarded / b.capacity) : 0;
    return `<div class="card"><div class="row"><span class="plate">${esc(b.bus_number)}</span><span class="small muted grow" style="min-width:0">${esc(b.vehicle_no || '')}<br>${esc(origin(b.route))}</span><span class="pill ${b.seats_left === 0 ? 'bad' : ''}">${b.seats_left === 0 ? 'Full' : b.seats_left + ' left'}</span></div>
      <div class="meter ${b.seats_left === 0 ? 'full' : ''}" style="margin:.9rem 0 .4rem"><i style="width:${pct}%"></i></div>
      <p class="muted small">${b.boarded} of ${b.capacity} boarded · ${esc(d.trip.slice(11))} trip</p></div>`;
  }).join('');
}
function liveHtml(d, feed, sm) {
  const on = online(d.scanner_last_seen), admin = S.me.role === 'admin';
  const tile = (n, l) => `<div class="tile"><b>${n}</b><span>${l}</span></div>`;
  const tiles = sm ? `<div class="tiles">${tile(sm.boarded, 'boarded this trip')}${tile(sm.riders, 'riders')}${tile(sm.buses, 'buses')}${tile(sm.cards, 'ID cards linked')}</div>` : '';
  const fe = feed.length ? `<ul class="feed">${feed.map(x => `<li><span class="plate" style="font-size:1rem">${esc(x.bus_number)}</span><span><b>${esc(x.name)}</b><br><span class="muted small">${esc(x.login_id)}</span></span><span class="muted small" style="margin-left:auto">${esc(hhmm(x.boarded_at))}</span></li>`).join('')}</ul>` : '<div class="empty">No boardings yet. Taps appear here as they happen.</div>';
  return `<div class="row" style="margin-bottom:1rem"><h2 class="grow">Live boarding</h2><span class="status ${on ? 'on' : ''}"><i></i>${d.scanner_last_seen ? 'Scanner ' + (on ? 'online' : 'quiet') + ', last seen ' + esc(ago(d.scanner_last_seen)) : 'Scanner has not connected yet'}</span></div>
    ${tiles}
    <div class="row" style="margin-bottom:1rem"><input id="bq" class="grow" type="search" placeholder="Search ${d.buses.length} buses by route number, vehicle or place" value="${esc(S.q || '')}">${admin && S.demo ? '<button class="btn small" id="sim" title="Demo: board random riders as if they tapped their cards">Simulate 8 taps</button>' : ''}</div>
    <div class="grid" id="busgrid">${busCards(d)}</div>
    <div class="grid" style="margin-top:1rem;grid-template-columns:repeat(auto-fit,minmax(300px,1fr))">
      <div class="card"><h3>Latest taps</h3>${fe}</div>
      <div class="card"><h3>Board a rider by ID</h3><p class="muted small">Use this when a card is lost or the scanner is down.</p>
        <form id="mf"><label class="field"><span>Rider email or ID</span><input id="mid" required></label>
        <label class="field"><span>Bus number</span><input id="mbus" required></label>
        <button class="btn">Board rider</button></form></div></div>`;
}
async function tabLive() {
  if (S.demo === undefined) { const [, dm] = await api('/api/demo'); S.demo = !!dm.enabled; }
  const [d, f, sm] = await liveData(); S.last = d; $('#pane').innerHTML = liveHtml(d, f, sm); bindManual();
}
async function refreshLive() {
  if (!$('#pane') || S.tab !== 'live' || (document.activeElement && ['INPUT'].includes(document.activeElement.tagName))) return;
  const [d, f, sm] = await liveData(); S.last = d; const p = $('#pane'); if (p) { p.innerHTML = liveHtml(d, f, sm); bindManual(); }
}
function bindManual() {
  const q = $('#bq'); if (q) q.oninput = () => { S.q = q.value; $('#busgrid').innerHTML = busCards(S.last); };
  const sim = $('#sim'); if (sim) sim.onclick = async () => { sim.disabled = true; const [s, d] = await api('/api/demo/simulate', { count: 8 }); toast(d.message, s >= 400); tabLive(); };
  const f = $('#mf'); if (!f) return;
  f.onsubmit = async e => { e.preventDefault(); const [s, d] = await api('/api/staff/board', { login_id: $('#mid').value, bus: $('#mbus').value });
    toast(d.result === 'OK' ? d.message : `${d.result.replace('_', ' ').toLowerCase()}: ${d.message}`, s >= 400); if (s < 400) { $('#mid').value = ''; tabLive(); } };
}

async function tabBuses() {
  const [, d] = await api('/api/dashboard');
  const q = (S.bq || '').toLowerCase();
  const rows = d.buses.filter(b => !q || (b.bus_number + ' ' + (b.vehicle_no || '') + ' ' + (b.route || '') + ' ' + (b.driver_name || '')).toLowerCase().includes(q));
  $('#pane').innerHTML = `<div class="stack"><div class="grid" style="grid-template-columns:repeat(auto-fit,minmax(300px,1fr))">
    <div class="card"><h3>Add or change a bus</h3><p class="muted small">Using an existing number updates its seat count.</p>
      <form id="bf"><label class="field"><span>Route or bus number</span><input id="bn" required maxlength="20"></label>
      <label class="field"><span>Seats</span><input id="bc" type="number" min="1" max="200" required></label><button class="btn amber">Save bus</button></form></div>
    <div class="card"><h3>Import route list</h3><p class="muted small">Paste CSV with the header <code>route_no,vehicle_no,route,driver_name,driver_phone,capacity</code>. Existing routes are updated.</p>
      <textarea id="bcsv" placeholder="route_no,vehicle_no,route,driver_name,driver_phone,capacity"></textarea><button class="btn" id="bimp" style="margin-top:.6rem">Import buses</button></div>
    <div class="card"><h3>Seats on every bus</h3><p class="muted small">Route lists rarely include seat counts. Set one number for all buses, then adjust single buses as needed.</p>
      <form id="allf"><label class="field"><span>Seats</span><input id="allc" type="number" min="1" max="200" required></label><button class="btn">Apply to all ${d.buses.length} buses</button></form></div></div>
    <div class="card"><div class="row"><h3 class="grow">Buses (${d.buses.length})</h3><input id="bsq" type="search" placeholder="Search route, vehicle, place or driver" value="${esc(S.bq || '')}" style="max-width:300px"></div>
    ${d.buses.length ? `<div class="scroll"><table><thead><tr><th>Route</th><th>Vehicle</th><th>Starts at</th><th>Seats</th><th>Driver</th></tr></thead><tbody id="brows"></tbody></table></div>` : '<div class="empty" style="margin-top:.8rem">No buses yet. Add one or import your route list.</div>'}</div></div>`;
  const draw = () => { const t = $('#brows'); if (!t) return; const qq = (S.bq || '').toLowerCase();
    t.innerHTML = d.buses.filter(b => !qq || (b.bus_number + ' ' + (b.vehicle_no || '') + ' ' + (b.route || '') + ' ' + (b.driver_name || '')).toLowerCase().includes(qq)).map(b =>
      `<tr title="${esc(b.route || '')}"><td><span class="plate" style="font-size:1rem">${esc(b.bus_number)}</span></td><td>${esc(b.vehicle_no || '')}</td><td>${esc(origin(b.route))}</td><td>${b.capacity}</td><td>${esc(b.driver_name || '')}<br>${tel(b.driver_phone)}</td></tr>`).join(''); };
  draw(); if ($('#bsq')) $('#bsq').oninput = e => { S.bq = e.target.value; draw(); };
  $('#bf').onsubmit = async e => { e.preventDefault(); const [s, r] = await api('/api/admin/buses', { bus_number: $('#bn').value, capacity: $('#bc').value }); toast(r.message, s >= 400); if (s < 400) tabBuses(); };
  $('#bimp').onclick = async () => { const [s, r] = await api('/api/admin/buses/import', { csv: $('#bcsv').value }); if (s !== 200) return toast(r.message, true);
    toast(`${r.added} added, ${r.updated} updated` + (r.errors.length ? '. ' + r.errors.slice(0, 3).map(e => `Line ${e.line}: ${e.error}`).join('; ') : ''), r.errors.length > 0 && !r.added && !r.updated); tabBuses(); };
  $('#allf').onsubmit = async e => { e.preventDefault(); if (!confirm('Set every bus to ' + $('#allc').value + ' seats?')) return;
    const [s, r] = await api('/api/admin/buses/capacity-all', { capacity: $('#allc').value }); toast(r.message, s >= 400); tabBuses(); };
}

async function tabRiders() {
  const [[, rs], [, d]] = await Promise.all([api('/api/admin/riders'), api('/api/dashboard')]);
  const buses = d.buses;
  const opts = cur => `<option value="">None</option>` + buses.map(b => `<option ${b.bus_number === cur ? 'selected' : ''}>${esc(b.bus_number)}</option>`).join('');
  const gov = S.me.org.type === 'government';
  $('#pane').innerHTML = `<div class="stack"><div class="grid" style="grid-template-columns:repeat(auto-fit,minmax(300px,1fr))">
    <div class="card"><h3>Add a rider</h3><form id="af2"><label class="field"><span>Full name</span><input id="rn" required></label>
      <label class="field"><span>ID (roll number or employee ID)</span><input id="rl" required></label>
      ${gov ? '' : `<label class="field"><span>Bus</span><select id="rb">${opts('')}</select></label>`}
      <button class="btn amber">Add rider</button></form></div>
    <div class="card"><h3>Import from a list</h3><p class="muted small">Paste CSV with the header <code>login_id,name,bus</code>. Up to 1000 rows.</p>
      <textarea id="csv" placeholder="login_id,name,bus&#10;21CS001,Asha,12"></textarea><button class="btn" id="imp" style="margin-top:.6rem">Import riders</button></div></div>
    <div class="card" id="secret" hidden></div>
    <div class="card"><div class="row"><h3 class="grow">Riders (${rs.length})</h3><input id="q" placeholder="Search by name or ID" style="max-width:260px"></div>
    <div class="scroll"><table><thead><tr><th>ID</th><th>Name</th>${gov ? '' : '<th>Bus</th>'}<th>Card</th><th></th></tr></thead><tbody id="rows"></tbody></table></div>
    ${rs.length ? '' : '<div class="empty" style="margin-top:.8rem">No riders yet. Add one above or import a list.</div>'}</div></div>`;
  const secret = html => { const s = $('#secret'); s.hidden = false; s.innerHTML = html; s.scrollIntoView({behavior: 'smooth', block: 'nearest'}); };
  const draw = () => {
    const q = $('#q').value.toLowerCase();
    $('#rows').innerHTML = rs.filter(r => (r.login_id + r.name).toLowerCase().includes(q)).map(r => `<tr><td>${esc(r.login_id)}</td><td>${esc(r.name)}</td>
      ${gov ? '' : `<td><select data-a="bus" data-id="${esc(r.login_id)}" style="min-height:34px;padding:.2rem .4rem">${opts(r.bus)}</select></td>`}
      <td>${r.card_linked ? '<span class="pill ok">Linked</span>' : '<span class="pill">None</span>'}</td>
      <td style="white-space:nowrap"><button class="btn small" data-a="link" data-id="${esc(r.login_id)}">${r.card_linked ? 'Replace card' : 'Link card'}</button>
      ${r.card_linked ? `<button class="btn small ghost" data-a="unlink" data-id="${esc(r.login_id)}">Unlink</button>` : ''}
      <button class="btn small ghost" data-a="reset" data-id="${esc(r.login_id)}">Reset password</button></td></tr>`).join('');
  };
  draw(); $('#q').oninput = draw;
  $('#rows').onchange = async e => { const el = e.target; if (el.dataset.a !== 'bus') return;
    const [s, r] = await api('/api/admin/riders/assign', { login_id: el.dataset.id, bus: el.value }); toast(r.message, s >= 400); };
  $('#rows').onclick = async e => { const el = e.target.closest('button'); if (!el) return; const id = el.dataset.id;
    if (el.dataset.a === 'link') { if (rs.find(r => r.login_id === id).card_linked) await api('/api/admin/riders/unlink', { login_id: id });
      const [s, r] = await api('/api/admin/riders/enrol', { login_id: id }); toast(r.message, s >= 400);
      if (s < 400) secret(`<h3>Waiting for a card</h3><p>Tap the new card on the scanner now. It is linked to <b>${esc(id)}</b> and the scanner beeps twice. This window closes after 2 minutes.</p>`);
      const t0 = Date.now(); const iv = setInterval(async () => { const [, l] = await api('/api/admin/riders'); if (Array.isArray(l) && l.find(r => r.login_id === id)?.card_linked) { clearInterval(iv); toast('Card linked to ' + id); tabRiders(); } else if (Date.now() - t0 > 125000) clearInterval(iv); }, 2500); }
    if (el.dataset.a === 'unlink') { const [s, r] = await api('/api/admin/riders/unlink', { login_id: id }); toast(r.message, s >= 400); tabRiders(); }
    if (el.dataset.a === 'reset') { const [s, r] = await api('/api/admin/riders/reset', { login_id: id }); if (s < 400) secret(`<h3>New password for ${esc(id)}</h3><p>Shown once. Share it privately.</p><code class="key">${esc(r.temp_password)}</code>`); else toast(r.message, true); } };
  const importCsv = async csv => {
    const [s, r] = await api('/api/admin/riders/import', { csv }); if (s !== 200) return toast(r.message, true);
    if (r.created.length) secret(`<h3>${r.created.length} rider${r.created.length > 1 ? 's' : ''} added</h3><p>Temporary passwords are shown once. Share each privately.</p><div class="scroll"><table><tbody>${r.created.map(c => `<tr><td>${esc(c.login_id)}</td><td><code>${esc(c.temp_password)}</code></td></tr>`).join('')}</tbody></table></div>`);
    if (r.errors.length) toast(r.errors.map(e => `Line ${e.line}: ${e.error}`).join('; '), true);
    if (r.created.length) { const keep = $('#secret').innerHTML; await tabRiders(); secret(keep); }
  };
  $('#imp').onclick = () => importCsv($('#csv').value);
  $('#af2').onsubmit = e => { e.preventDefault(); const q = v => '"' + v.replace(/"/g, '""') + '"'; importCsv(`login_id,name,bus\n${q($('#rl').value)},${q($('#rn').value)},${gov ? '' : q($('#rb').value)}`); };
}

async function tabInsights() {
  const [[, a], [, dl]] = await Promise.all([api('/api/admin/analytics'), api('/api/admin/daily')]);
  const max = Math.max(1, ...dl.map(x => x.boardings));
  const chart = dl.length ? `<div class="bars" role="img" aria-label="Boardings per day">${dl.map(x => `<div title="${esc(x.day)}: ${x.boardings}"><span>${x.boardings}</span><i style="height:${Math.round(100 * x.boardings / max * 1.1)}px"></i><span>${esc(x.day.slice(5))}</span></div>`).join('')}</div>` : '<div class="empty">Daily totals appear after the first boardings.</div>';
  const rows = a.buses.length ? a.buses.map(b => `<tr><td><span class="plate" style="font-size:1rem">${esc(b.bus_number)}</span></td><td>${b.trips}</td><td>${b.avg_per_trip}</td><td style="min-width:140px"><div class="meter"><i style="width:${Math.min(100, b.avg_fill_percent)}%"></i></div><span class="small muted">${b.avg_fill_percent}% of ${b.capacity} seats</span></td></tr>`).join('') : '';
  $('#pane').innerHTML = `<div class="stack"><div class="card"><h3>Boardings per day</h3>${chart}</div>
    <div class="card"><h3>How full each bus runs</h3><p class="muted small">Average boardings per trip against seats. No names are used here.</p>
    ${rows ? `<div class="scroll"><table><thead><tr><th>Bus</th><th>Trips</th><th>Avg per trip</th><th>Fill</th></tr></thead><tbody>${rows}</tbody></table></div>` : '<div class="empty">No data yet.</div>'}</div></div>`;
}

async function tabNotices() {
  const [[, list], [, d]] = await Promise.all([api('/api/announcements'), api('/api/dashboard')]);
  $('#pane').innerHTML = `<div class="grid" style="grid-template-columns:repeat(auto-fit,minmax(300px,1fr))">
    <div class="card"><h3>Post a notice</h3><p class="muted small">Riders see it on their home screen. Choose a bus to show it only to that route.</p>
      <form id="nf"><label class="field"><span>Message</span><textarea id="nb" required maxlength="280" style="font-family:inherit;min-height:90px" placeholder="Route 9 is running 10 minutes late this evening."></textarea></label>
      <label class="field"><span>Who sees it</span><select id="nbus"><option value="">Everyone in ${esc(S.me.org.name)}</option>${d.buses.map(b => `<option value="${esc(b.bus_number)}">Route ${esc(b.bus_number)} riders only</option>`).join('')}</select></label>
      <button class="btn amber">Post notice</button></form></div>
    <div class="card"><h3>Posted notices</h3>${list.length ? `<ul class="feed">${list.map(n => `<li><span class="plate" style="font-size:.95rem">${n.bus_number ? esc(n.bus_number) : 'All'}</span><span>${esc(n.body)}</span><button class="btn small ghost" data-del="${n.id}" style="margin-left:auto">Remove</button></li>`).join('')}</ul>` : '<div class="empty">No notices yet.</div>'}</div></div>`;
  $('#nf').onsubmit = async e => { e.preventDefault(); const [s, r] = await api('/api/admin/announcements', { body: $('#nb').value, bus: $('#nbus').value }); toast(r.message, s >= 400); if (s < 400) tabNotices(); };
  $('#pane').onclick = async e => { const b = e.target.closest('[data-del]'); if (!b) return; await api('/api/admin/announcements/delete', { id: +b.dataset.del }); tabNotices(); };
}

async function tabSetup() {
  const [, d] = await api('/api/admin/org'); const on = online(d.last_seen);
  $('#pane').innerHTML = `<div class="stack"><div class="card"><h3>Organisation</h3>
    <form id="of"><label class="field"><span>Name</span><input id="on" value="${esc(d.name)}" required minlength="3" maxlength="80"></label>
    <label class="field"><span>Join code (optional)</span><input id="oj" value="${esc(d.join_code || '')}" placeholder="Leave empty to let anyone join" maxlength="20"></label>
    <p class="muted small">With a join code, riders must enter it to join ${esc(d.name)}. Share it with your students or staff. Type: ${esc(TYPE[d.org_type])}.</p>
    <button class="btn">Save settings</button></form></div>
    <div class="card"><div class="row"><h3 class="grow">Scanner</h3><span class="status ${on ? 'on' : ''}"><i></i>${d.last_seen ? (on ? 'Online' : 'Quiet') + ', last seen ' + esc(ago(d.last_seen)) : 'Not connected yet'}</span></div>
    <p class="muted small" style="margin-top:.6rem">Scanner key for <b>${esc(d.name)}</b>. Put it in <code>secrets.h</code> as DEVICE_KEY. Keep it private.</p>
    <code class="key" id="key">${esc(d.device_key)}</code>
    <div class="row" style="margin-top:.7rem"><button class="btn small" id="cp">Copy key</button><button class="btn small danger" id="rot">Make a new key</button></div></div>
    <div class="card"><h3>Build the scanner</h3>
    <p class="muted small">ESP32 board, MFRC522 reader, green and red LEDs with 220 Ω resistors, an active buzzer, jumper wires. The reader needs 3.3V, not 5V. Cards must be 13.56 MHz MIFARE or NFC.</p>
    <div class="scroll"><table><thead><tr><th>MFRC522</th><th>ESP32</th><th>Part</th><th>ESP32</th></tr></thead><tbody>
    <tr><td>3.3V</td><td>3V3</td><td>Green LED (+ resistor)</td><td>GPIO 25</td></tr><tr><td>GND</td><td>GND</td><td>Red LED (+ resistor)</td><td>GPIO 26</td></tr>
    <tr><td>SDA / SS</td><td>GPIO 5</td><td>Buzzer +</td><td>GPIO 27</td></tr><tr><td>RST</td><td>GPIO 22</td><td>LEDs and buzzer −</td><td>GND</td></tr>
    <tr><td>SCK</td><td>GPIO 18</td><td></td><td></td></tr><tr><td>MISO</td><td>GPIO 19</td><td></td><td></td></tr><tr><td>MOSI</td><td>GPIO 23</td><td></td><td></td></tr></tbody></table></div>
    <ol class="steps"><li>In the Arduino IDE, add ESP32 board support and install the libraries MFRC522 and ArduinoJson (v7).</li>
    <li>Upload <code>firmware/step1_read_uid</code>. The Serial Monitor (115200) should print version 0x91 or 0x92 and a UID when you tap a card.</li>
    <li>Copy <code>secrets.h.example</code> to <code>secrets.h</code> in <code>firmware/segro_node</code>. Fill in Wi-Fi, this site's address, the key above, and the bus number.</li>
    <li>Upload <code>segro_node</code>. On start-up one green flash and one beep means the scanner reached this site.</li>
    <li>In the Riders tab, press Link card for a rider and tap their card. Two beeps means it is linked.</li></ol>
    <p class="small muted">Signals: green + 1 beep boarded · green + 2 card linked · red + 1 unknown card · red + 2 wrong bus or already boarded · red + 3 bus full · long red connection problem.</p></div></div>`;
  $('#of').onsubmit = async e => { e.preventDefault(); const [s, r] = await api('/api/admin/org', { name: $('#on').value, join_code: $('#oj').value }); toast(r.message, s >= 400); if (s < 400) { const [, m] = await api('/api/me'); S.me = m; tabSetup(); } };
  $('#cp').onclick = async () => { try { await navigator.clipboard.writeText(d.device_key); toast('Key copied'); } catch (e) { toast('Select the key and copy it by hand', true); } };
  $('#rot').onclick = async () => { if (!confirm('The current scanner will stop working until you flash the new key. Continue?')) return;
    const [s, r] = await api('/api/admin/device/rotate', {}); toast(r.message, s >= 400); tabSetup(); };
}

/* ---------- boot ---------- */
async function boot() {
  const [s, m] = await api('/api/me');
  if (s !== 200) return showAuth();
  S.me = m; S.tab = 'live';
  if (m.role === 'rider') showRider(); else showStaff();
}
boot();
