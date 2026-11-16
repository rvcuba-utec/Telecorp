// Cliente de API compartido (portal y consola). Sin dependencias.
let _cfg;
export async function cfg() {
  if (!_cfg) _cfg = await (await fetch('/config.json', { cache: 'no-store' })).json();
  return _cfg;
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

/** fetch con timeout y reintentos con backoff exponencial + jitter (solo ante 5xx/red; 429 se devuelve al llamador). */
export async function api(path, { method = 'GET', body, token, retries = 3 } = {}) {
  const { apiBase } = await cfg();
  const headers = { 'Content-Type': 'application/json' };
  if (token) headers['x-operator-token'] = token;
  let last;
  for (let i = 0; i <= retries; i++) {
    const ctl = new AbortController();
    const t = setTimeout(() => ctl.abort(), 12000);
    try {
      const r = await fetch(apiBase + '/api' + path, { method, headers, body: body ? JSON.stringify(body) : undefined, signal: ctl.signal });
      clearTimeout(t);
      const data = await r.json().catch(() => ({}));
      if (r.status < 500) return { status: r.status, data };
      last = { status: r.status, data };
    } catch (e) {
      clearTimeout(t);
      last = { status: 0, data: { error: 'NETWORK', message: 'Sin conexión con el servicio' } };
    }
    if (i < retries) await sleep(Math.min(8000, 600 * 2 ** i) * (0.5 + Math.random()));
  }
  return last;
}

/** Zonas y categorías (contracts/zones.json y enums.json): de la API, o de la copia estática si no responde. */
export async function loadCatalog() {
  const [z, c] = await Promise.all([api('/zones', { retries: 1 }), api('/catalog', { retries: 1 })]);
  const zones = z.data?.items || (await (await fetch('/zones.json')).json()).zones;
  let category = c.data?.category;
  if (!category) {
    const enums = await (await fetch('/enums.json')).json();
    category = enums.category;
    Object.assign(STATUS_LABEL, enums.status_labels);
  } else Object.assign(STATUS_LABEL, c.data.status_labels);
  return { zones, category };
}

export const STATUS_LABEL = {
  REGISTRADA: 'Registrada', EN_REVISION: 'En revisión', EN_ATENCION: 'Siendo atendida', SOLUCIONADA: 'Solucionada', CERRADA: 'Cerrada',
};
export const CATEGORY_LABEL = {
  SIN_INTERNET: 'Sin internet', INTERNET_LENTO: 'Internet lento', SIN_SENAL: 'Sin señal',
  LLAMADAS: 'Fallas en llamadas', SMS: 'Fallas en SMS', OTRO: 'Otro',
};

export const $ = (s, el = document) => el.querySelector(s);
export function h(tag, attrs = {}, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === 'class') el.className = v;
    else if (k.startsWith('on')) el.addEventListener(k.slice(2), v);
    else el.setAttribute(k, v);
  }
  for (const kid of kids.flat()) el.append(kid instanceof Node ? kid : document.createTextNode(kid ?? ''));
  return el; // todo texto entra por nodos de texto: sin innerHTML, sin XSS
}
export const fmt = (iso) => (iso ? new Date(iso).toLocaleString() : '—');
export const pill = (v) => h('span', { class: 'pill ' + v }, STATUS_LABEL[v] || v || '—');
export function uuid() {
  return crypto.randomUUID ? crypto.randomUUID() : 'k-' + Date.now() + '-' + Math.random().toString(36).slice(2);
}
