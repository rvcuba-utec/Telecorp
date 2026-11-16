import { api, $, h, fmt, pill, loadCatalog, CATEGORY_LABEL } from './api.js';

let token = null, selected = null, timer = null;
try { token = sessionStorage.getItem('pulsored.op'); } catch {}

const call = (path, opts = {}) => api(path, { token, retries: 1, ...opts });
function msg(el, cls, text) { el.replaceChildren(h('div', { class: 'msg ' + cls }, text)); }
const table = (cols, rows) => h('table', {}, h('thead', {}, h('tr', {}, ...cols.map((t) => h('th', {}, t)))), h('tbody', {}, rows));

async function enter() {
  token = $('#token').value.trim() || token;
  const r = await call('/incidents');
  if (r.status === 401) { msg($('#loginmsg'), 'err', 'Token inválido.'); token = null; return; }
  if (r.status !== 200) { msg($('#loginmsg'), 'err', r.data.message || 'Servicio no disponible.'); return; }
  try { sessionStorage.setItem('pulsored.op', token); } catch {}
  $('#login').hidden = true; $('#app').hidden = false;
  const cat = await loadCatalog();
  cat.zones.forEach((x) => $('#fzone').append(h('option', { value: x.zone_id }, `${x.name} (${x.zone_id})`)));
  const s = await call('/settings');
  if (s.status === 200) {
    const i = s.data.integrations || {};
    $('#integrations').textContent = `Config: ${s.data.source}${s.data.config_version ? ' v' + s.data.config_version : ''} · umbral ${s.data.USERS_THRESHOLD} usuarios/${s.data.WINDOW_HOURS} h · modo ${s.data.CORRELATION_MODE}` +
      ` · AWS ${i.aws ? 'conectado' : 'simulado'} · GCP ${i.gcp ? 'conectado' : 'simulado'} · correo ${i.email ? 'real' : 'simulado'} · SMS simulado`;
  }
  refresh();
  clearInterval(timer); timer = setInterval(refresh, 15000);
}

async function refresh() {
  const zone = $('#fzone').value;
  const q = new URLSearchParams();
  if (zone) q.set('zone_id', zone);
  if ($('#ftype').value) q.set('incident_type', $('#ftype').value);
  if ($('#fprio').value) q.set('priority_level', $('#fprio').value);
  if ($('#fclosed').value === '1') q.set('include_closed', '1');
  const zq = zone ? '?zone_id=' + encodeURIComponent(zone) : '';
  const [r, ev, ants, preds] = await Promise.all([call('/incidents?' + q), call('/infra/events' + zq), call('/antennas' + zq), call('/predictions' + zq)]);
  if (r.status === 401) { location.reload(); return; }
  renderList(r.data.items || []);
  renderInfra(ev.data.items || []);
  renderAntennas(ants.data || {});
  renderPredictions(preds.data || {});
  $('#upd').textContent = 'Actualizado ' + new Date().toLocaleTimeString();
  if (selected) openIncident(selected, true);
}

function renderList(items) {
  const box = $('#list');
  if (!items.length) { box.replaceChildren(h('p', { class: 'mut' }, 'Sin incidencias.')); return; }
  box.replaceChildren(table(['Prioridad', 'Tipo', 'Zona', 'Estado', 'Usuarios', 'Detectada por'],
    items.map((i) => h('tr', { class: 'row-click' + (i.incident_id === selected ? ' sel' : ''), onclick: () => openIncident(i.incident_id) },
      h('td', {}, pill(i.priority_level), ' ', String(i.priority_score ?? ''), i.priority_override ? ' ✋' : ''),
      h('td', {}, i.incident_type), h('td', {}, i.zone_name || i.zone_id),
      h('td', {}, pill(i.status)), h('td', {}, String(i.users_affected)), h('td', {}, i.detected_by || '—')))));
}

function renderInfra(items) {
  const box = $('#infra');
  if (!items.length) { box.replaceChildren(h('p', { class: 'mut' }, 'Sin eventos recibidos.')); return; }
  box.replaceChildren(table(['Hora', 'Evento', 'Antena', 'Zona', 'Estado', 'Severidad'],
    items.slice(0, 15).map((e) => h('tr', {}, h('td', {}, fmt(e.occurred_at)), h('td', {}, e.event_type),
      h('td', {}, e.antenna_id), h('td', {}, e.zone_id), h('td', {}, `${e.previous_state || '—'} → ${e.new_state || '—'}`),
      h('td', {}, e.severity || '—')))));
}

function renderAntennas(d) {
  $('#antsrc').textContent = d.source === 'aws' ? '(AWS en vivo)' : '(proyección local de eventos)';
  const items = (d.items || []).filter((a) => a.state && a.state !== 'NORMAL');
  const total = (d.items || []).length;
  $('#antennas').replaceChildren(
    h('p', { class: 'mut' }, `${total - items.length} de ${total} antenas en estado NORMAL.`),
    items.length ? table(['Antena', 'Zona', 'Estado', 'Desde'], items.map((a) => h('tr', {},
      h('td', {}, a.antenna_id), h('td', {}, a.zone_id), h('td', {}, h('span', { class: 'pill ' + a.state }, a.state)),
      h('td', {}, fmt(a.state_since))))) : '');
}

function renderPredictions(d) {
  $('#predsrc').textContent = d.source === 'aws' ? '(AWS)' : '(alertas PREVENTIVA recibidas)';
  const items = d.items || [];
  $('#predictions').replaceChildren(items.length
    ? table(['Antena', 'Prob.', 'Estado previsto', 'Horizonte', 'Vigente hasta'], items.slice(0, 15).map((p) => h('tr', {},
      h('td', {}, p.antenna_id), h('td', {}, p.probability != null ? Math.round(p.probability * 100) + ' %' : '—'),
      h('td', {}, p.predicted_state || '—'), h('td', {}, p.horizon_minutes ? p.horizon_minutes + ' min' : '—'), h('td', {}, fmt(p.valid_until)))))
    : h('p', { class: 'mut' }, 'Sin riesgos vigentes.'));
}

async function act(id, body) {
  const res = await call('/incidents/' + encodeURIComponent(id), { method: 'PATCH', body: { note: $('#note')?.value || '', ...body } });
  const box = $('#actmsg');
  if (box) msg(box, res.status >= 400 ? 'err' : 'ok', res.status >= 400 ? (res.data.message || 'No permitido') : 'Acción registrada.');
  setTimeout(refresh, 800);
}

async function openIncident(id, silent) {
  selected = id;
  const r = await call('/incidents/' + encodeURIComponent(id));
  const box = $('#detail');
  if (r.status !== 200) { msg(box, 'err', r.data.message || 'Error'); return; }
  if (silent && document.activeElement && box.contains(document.activeElement)) return;   // no pisar lo que el operador escribe
  const d = r.data;
  const btn = (label, body, cls = 'sec') => h('button', { class: cls, onclick: () => act(id, body) }, label);
  const next = { REGISTRADA: ['EN_REVISION'], EN_REVISION: ['EN_ATENCION'], EN_ATENCION: ['SOLUCIONADA'], SOLUCIONADA: ['CERRADA', 'EN_REVISION'] }[d.status] || [];
  const prio = h('select', { id: 'prio' }, h('option', { value: '' }, 'Prioridad automática'),
    ...['CRITICA', 'ALTA', 'MEDIA', 'BAJA'].map((l) => h('option', { value: l }, l)));
  prio.value = d.priority_override || '';
  const ai = d.ai || {};
  box.replaceChildren(
    h('p', {}, pill(d.priority_level), ' ', h('b', {}, String(d.priority_score)), '/100  ·  ', pill(d.status), '  ·  ', d.incident_type, '  ·  ', String(d.users_affected), ' usuarios'),
    h('p', { class: 'mut' }, `${d.zone_name || d.zone_id} · detectada por ${d.detected_by || '—'} · antenas: `, (d.antenna_ids || []).join(', ') || 'sin evidencia (verificar en campo)',
      d.detection_lag_s != null ? ` · la red avisó ${d.detection_lag_s >= 0 ? d.detection_lag_s + ' s antes' : -d.detection_lag_s + ' s después'} que los usuarios` : ''),
    d.prediction ? h('p', {}, h('b', {}, 'Predicción: '), `${Math.round((d.prediction.probability || 0) * 100)} % de ${d.prediction.predicted_state} en ${d.prediction.horizon_minutes} min (${d.prediction.model_version || ''})`) : '',
    d.recurrence ? h('p', {}, h('b', {}, 'Recurrencia: '), `${d.recurrence.episodes_7d ?? '—'} episodios en 7 días, ${d.recurrence.episodes_30d ?? '—'} en 30 días`) : '',
    ai.summary ? h('div', { class: 'msg' }, h('b', {}, `IA (${d.ai_source}): `), ai.summary,
      ai.probable_cause ? h('div', {}, h('b', {}, 'Causa probable: '), ai.probable_cause) : '',
      ai.recommended_action ? h('div', {}, h('b', {}, 'Acción recomendada: '), ai.recommended_action) : '') : '',
    (d.antenna_evidence || []).length ? h('p', { class: 'mut' }, 'Evidencia actual: ', d.antenna_evidence.map((a) => `${a.antenna_id} ${a.state || a.event_type}`).join(', ')) : '',
    h('p', { class: 'mut mono' }, 'Factores: ', JSON.stringify(d.priority_factors || {})),
    h('input', { id: 'note', placeholder: 'Nota para el historial (opcional)', maxlength: '300' }),
    h('div', { class: 'actions' },
      ['REGISTRADA', 'EN_REVISION'].includes(d.status) ? [btn('Confirmar', { action: 'CONFIRMAR' }), btn('Descartar (falso positivo)', { action: 'DESCARTAR' }, 'bad')] : '',
      next.map((to) => btn('→ ' + to, { action: 'CAMBIAR_ESTADO', to })),
      prio, btn('Aplicar prioridad', { action: 'CAMBIAR_PRIORIDAD', get level() { return $('#prio').value; } })),
    h('div', { id: 'actmsg' }),
    h('h2', { style: 'margin-top:16px' }, 'Línea de tiempo'),
    h('ol', { class: 'tl' }, (d.history || []).slice().reverse().map((x) => h('li', {}, h('b', {}, x.action), ' · ', x.actor, ' · ', fmt(x.ts), x.from !== x.to ? ` · ${x.from ?? '∅'} → ${x.to}` : '', x.note ? ' · ' + x.note : ''))),
    h('h2', { style: 'margin-top:16px' }, `Reportes vinculados (${(d.reports || []).length})`),
    h('ul', { class: 'mut' }, (d.reports || []).slice(0, 10).map((x) => h('li', {}, h('span', { class: 'mono' }, x.report_id), ' · ',
      CATEGORY_LABEL[x.category] || x.category, x.ai_urgency ? ` · urgencia ${x.ai_urgency}` : '', ' · ', x.description))),
  );
}

$('#enter').addEventListener('click', enter);
$('#token').addEventListener('keydown', (e) => e.key === 'Enter' && enter());
$('#refresh').addEventListener('click', refresh);
['#fzone', '#ftype', '#fprio', '#fclosed'].forEach((s) => $(s).addEventListener('change', refresh));
if (token) enter();
