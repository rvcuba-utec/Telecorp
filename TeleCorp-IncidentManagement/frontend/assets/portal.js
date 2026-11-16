import { api, $, h, fmt, pill, uuid, loadCatalog, CATEGORY_LABEL } from './api.js';

const DRAFT = 'pulsored.draft';   // reporte pendiente (misma idempotency_key en cada reintento)
const store = {
  get() { try { return JSON.parse(localStorage.getItem(DRAFT)); } catch { return null; } },
  set(v) { try { localStorage.setItem(DRAFT, JSON.stringify(v)); } catch {} },
  clear() { try { localStorage.removeItem(DRAFT); } catch {} },
};
let zones = [];
let location_ = null;   // coordenadas si el usuario las compartió

// ---- catálogos (zonas y categorías de contracts/) ----
(async () => {
  const cat = await loadCatalog();
  zones = cat.zones;
  const sel = $('#zone');
  sel.append(h('option', { value: '' }, 'Selecciona tu zona'));
  zones.forEach((z) => sel.append(h('option', { value: z.zone_id }, `${z.name} (${z.zone_id})`)));
  const c = $('#category');
  cat.category.forEach((k) => c.append(h('option', { value: k }, CATEGORY_LABEL[k] || k)));
})();

const now = new Date(Date.now() - new Date().getTimezoneOffset() * 60000).toISOString().slice(0, 16);
$('#when').value = now; $('#when').max = now;

function show(el, cls, ...kids) { el.replaceChildren(h('div', { class: 'msg ' + cls }, ...kids)); }

// ---- coordenadas -> zona más cercana (el backend hace lo mismo si no se envía zone_id) ----
function nearest(lat, lng) {
  let best = null, dist = Infinity;
  for (const z of zones) {
    if (!z.centroid) continue;
    const d = (z.centroid.lat - lat) ** 2 + ((z.centroid.lng - lng) * Math.cos(lat * Math.PI / 180)) ** 2;
    if (d < dist) { best = z; dist = d; }
  }
  return best;
}
$('#geo').addEventListener('click', () => {
  if (!navigator.geolocation) { $('#geomsg').textContent = 'Tu navegador no permite ubicación.'; return; }
  $('#geomsg').textContent = 'Buscando…';
  navigator.geolocation.getCurrentPosition((p) => {
    location_ = { lat: +p.coords.latitude.toFixed(5), lng: +p.coords.longitude.toFixed(5) };
    const z = nearest(location_.lat, location_.lng);
    if (z) $('#zone').value = z.zone_id;
    $('#geomsg').textContent = z ? `Zona detectada: ${z.name}` : 'Ubicación registrada.';
  }, () => { $('#geomsg').textContent = 'No se pudo obtener la ubicación; elige la zona.'; }, { timeout: 8000 });
});

async function submitReport(payload) {
  const res = $('#result'), btn = $('#send');
  btn.disabled = true;
  const r = await api('/reports', { method: 'POST', body: payload, retries: 3 });
  btn.disabled = false;
  if (r.status === 202 || r.status === 200) {
    store.clear();
    show(res, 'ok', 'Reporte registrado. Tu código es ', h('b', { class: 'mono' }, r.data.report_id),
      '. Guárdalo: lo necesitas junto con los últimos 4 dígitos de tu celular para consultar el estado.');
    $('#code').value = r.data.report_id;
    $('#verify').value = payload.phone.replace(/\D/g, '').slice(-4);
    $('#form').reset(); $('#when').value = now; location_ = null; $('#geomsg').textContent = '';
    lookup();
  } else if (r.status === 429) {
    show(res, 'warn', r.data.message || 'Enviaste muchos reportes en poco tiempo. Intenta más tarde.');
  } else if (r.status === 0 || r.status >= 500) {
    // El servicio no respondió tras los reintentos: NO mostramos código (aún no existe). Se guarda y se reenvía.
    store.set(payload);
    show(res, 'warn', 'No pudimos enviar tu reporte ahora. Quedó guardado en este dispositivo y se reenviará cuando el servicio vuelva.');
  } else {
    show(res, 'err', r.data.message || 'No se pudo registrar el reporte.');
  }
}

$('#form').addEventListener('submit', (e) => {
  e.preventDefault();
  const draft = store.get();
  const payload = {
    idempotency_key: draft?.idempotency_key || uuid(),
    user_document: $('#doc').value.trim(), phone: $('#phone').value.trim(), zone_id: $('#zone').value,
    occurred_at: new Date($('#when').value).toISOString(), category: $('#category').value,
    description: $('#desc').value.trim(),
  };
  const mail = $('#email').value.trim(); if (mail) payload.email = mail;
  if (location_) payload.location = location_;
  submitReport(payload);
});

// Reintenta un borrador pendiente al abrir la página
const pending = store.get();
if (pending) submitReport(pending);

// ---- consulta de estado (código + dato de verificación) ----
async function lookup() {
  const code = $('#code').value.trim(), verify = $('#verify').value.trim();
  const out = $('#status');
  if (!code) return;
  if (!/^\d{4}$/.test(verify)) { show(out, 'err', 'Escribe los últimos 4 dígitos de tu celular.'); return; }
  const r = await api('/reports/' + encodeURIComponent(code) + '?verify=' + encodeURIComponent(verify), { retries: 2 });
  if (r.status !== 200) { show(out, 'err', r.data.message || 'No se pudo consultar.'); return; }
  const d = r.data;
  out.replaceChildren(
    h('p', {}, 'Estado: ', pill(d.status), '  ·  Zona ', d.zone_id, '  ·  Registrado ', fmt(d.created_at)),
    d.incident ? h('p', { class: 'mut' }, 'Tu reporte forma parte de una incidencia colectiva en tu zona (', d.incident.incident_id, ').') : '',
    h('ol', { class: 'tl' }, d.history.map((x) => h('li', {}, h('b', {}, x.label || x.action), ' · ', fmt(x.ts),
      x.action === 'STATUS_CHANGED' ? ` · ${x.to}` : (x.note ? ' · ' + x.note : '')))),
  );
}
$('#lookup').addEventListener('click', lookup);
const q = new URLSearchParams(location.search).get('r');
if (q) $('#code').value = q;
