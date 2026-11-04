'use strict';
let state = {events: [], audit: [], crm: [], summary: {}};
let selected = null;
let filter = 'all';
let lastSerialized = '';
let toastTimer;
const $ = id => document.getElementById(id);
const escapeHtml = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const label = status => ({delivered:'Delivered', queued:'Queued', retrying:'Retrying', dead_letter:'Needs attention'}[status] || status);
const time = value => new Date(value * 1000).toLocaleTimeString([], {hour:'2-digit', minute:'2-digit', second:'2-digit'});

function toast(message) {
  $('toast').textContent = message;
  $('toast').classList.add('visible');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => $('toast').classList.remove('visible'), 3600);
}

async function api(path, method = 'GET', body) {
  const response = await fetch(path, {method, headers: {'Content-Type':'application/json'}, ...(body ? {body:JSON.stringify(body)} : {})});
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail || 'Request failed.');
  return data;
}

function render() {
  const s = state.summary;
  $('m-received').textContent = s.received ?? 0;
  $('m-delivered').textContent = s.delivered ?? 0;
  $('m-duplicates').textContent = s.duplicates ?? 0;
  $('m-dead').textContent = s.dead_letter ?? 0;
  const visible = state.events.filter(e => filter === 'all' || (filter === 'active' ? ['queued','retrying'].includes(e.status) : e.status === filter));
  $('events-body').innerHTML = visible.length ? visible.map(e => `<tr class="event-row ${e.event_id === selected ? 'selected' : ''}" tabindex="0" role="button" aria-label="Inspect ${escapeHtml(e.payload.name)} event" data-event="${escapeHtml(e.event_id)}"><td><div class="lead-cell"><div class="initials">${escapeHtml(e.payload.name.split(' ').map(n=>n[0]).slice(0,2).join(''))}</div><div class="lead-info"><strong>${escapeHtml(e.payload.name)}</strong><small>${escapeHtml(e.event_id)}</small></div></div></td><td><span class="status ${e.status}">${label(e.status)}</span></td><td class="attempts">${e.attempts} / 3</td><td>${time(e.created_at)}</td></tr>`).join('') : '<tr><td colspan="4" class="empty">No events in this view.<br>Run a scenario above to see the workflow in action.</td></tr>';
  $('record-count').textContent = `${visible.length} of ${state.events.length} unique events · ${s.active || 0} in progress`;
  $('crm-count').textContent = `${state.crm.length} records`;
  $('crm-body').innerHTML = state.crm.length ? state.crm.map(c => `<tr><td class="crm-id">${escapeHtml(c.crm_id)}</td><td>${escapeHtml(c.name)}<br><small>${escapeHtml(c.email)}</small></td><td>${escapeHtml(c.company)}</td><td class="crm-id">${escapeHtml(c.event_id)}</td></tr>`).join('') : '<tr><td colspan="4" class="empty">Delivered synthetic leads will appear here.</td></tr>';
  document.querySelectorAll('[data-event]').forEach(row => {
    const inspect = () => {selected = row.dataset.event; render();};
    row.addEventListener('click', inspect);
    row.addEventListener('keydown', e => {if (e.key === 'Enter' || e.key === ' ') {e.preventDefault(); inspect();}});
  });
  renderDetail();
}

function renderDetail() {
  const event = state.events.find(e => e.event_id === selected);
  if (!event) {$('detail-name').textContent = 'Select an event'; $('event-detail').innerHTML = '<p class="empty">Choose a row to inspect its complete delivery history.</p>'; return;}
  $('detail-name').textContent = event.payload.name;
  const audit = state.audit.filter(a=>a.event_id === selected).sort((a,b)=>a.id-b.id);
  const actionNames = {accepted:'Lead accepted',duplicate_ignored:'Duplicate safely ignored',attempt_started:'Delivery attempted',retry_scheduled:'Retry scheduled',dead_letter:'Moved to dead-letter queue',delivered:'Mock CRM confirmed',replayed:'Manual replay requested'};
  $('event-detail').innerHTML = `<div class="detail-id">${escapeHtml(event.event_id)}</div><div class="detail-meta"><div><small>Current state</small><span class="status ${event.status}">${label(event.status)}</span></div><div><small>Destination</small><strong>Local mock CRM</strong></div><div><small>Delivery attempts</small><strong>${event.total_attempts} total · ${event.replay_count} replay${event.replay_count === 1 ? '' : 's'}</strong></div><div><small>Duplicates prevented</small><strong>${event.duplicate_count}</strong></div></div><div class="timeline-title">EXECUTION TIMELINE</div><div class="timeline">${audit.map(a=>`<div class="timeline-item ${a.action}-item"><strong>${actionNames[a.action] || escapeHtml(a.action)}<time>${time(a.at)}</time></strong><p>${escapeHtml(a.detail)}</p></div>`).join('')}</div>${event.status === 'dead_letter' ? '<button class="replay" id="replay-button">Clear simulated outage & replay ↗</button><p class="replay-note">Same event. Preserved history. A fresh attempt.</p>' : ''}`;
  const replay = $('replay-button');
  if (replay) replay.addEventListener('click', async () => {
    replay.disabled = true;
    try {await api(`/api/events/${encodeURIComponent(event.event_id)}/replay`, 'POST'); toast('Outage cleared. The same event has been requeued.'); await refresh(true);}
    catch (error) {toast(error.message); replay.disabled = false;}
  });
}

async function refresh(force = false) {
  try {
    const data = await api('/api/state');
    $('health-text').textContent = 'Local service online';
    $('health-text').parentElement.querySelector('i').style.background = '#47a580';
    const serialized = JSON.stringify(data);
    if (serialized !== lastSerialized || force) {
      state = data; lastSerialized = serialized;
      if (!state.events.some(e=>e.event_id === selected)) selected = state.events[0]?.event_id || null;
      render();
    }
  } catch (error) {
    $('health-text').textContent = 'Connection interrupted';
    $('health-text').parentElement.querySelector('i').style.background = '#c76a75';
  }
}

document.querySelectorAll('[data-scenario]').forEach(button => button.addEventListener('click', async () => {
  button.disabled = true;
  try {
    const result = await api(`/api/scenarios/${button.dataset.scenario}`, 'POST');
    selected = result.event_id;
    filter = 'all';
    document.querySelectorAll('[data-filter]').forEach(b=>b.classList.toggle('active',b.dataset.filter === 'all'));
    toast(button.dataset.scenario === 'duplicate' ? 'Submitted twice with one event ID. Only one delivery is queued.' : 'Synthetic lead accepted. Follow its receipt below.');
    await refresh(true);
  } catch (error) {toast(error.message);} finally {button.disabled = false;}
}));

document.querySelectorAll('[data-filter]').forEach(button => button.addEventListener('click', () => {
  filter = button.dataset.filter;
  document.querySelectorAll('[data-filter]').forEach(b=>b.classList.toggle('active',b === button));
  render();
}));

document.querySelectorAll('[data-tab]').forEach(button => button.addEventListener('click', () => {
  const evidence = button.dataset.tab === 'evidence';
  $('overview-panel').hidden = evidence;
  $('evidence-panel').hidden = !evidence;
  $('crumb').textContent = evidence ? 'Evidence & API' : 'Overview';
  document.querySelectorAll('[data-tab]').forEach(b=>b.classList.toggle('active', b === button));
}));

$('reset-button').addEventListener('click', async () => {
  if (!confirm('Clear the local synthetic events, audit log and mock CRM records?')) return;
  try {await api('/api/reset','POST'); selected = null; toast('Demo data cleared. Choose a scenario to start again.'); await refresh(true);}
  catch (error) {toast(error.message);}
});

async function poll() {await refresh(); setTimeout(poll, 550);}
poll();
