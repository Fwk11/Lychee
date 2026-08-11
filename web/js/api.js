/* lychee 前端 · api.js
 * 数据层：统一 fetch 封装 api()，处理静态模式回退与 401
 */

/* ---------- 数据层 ---------- */
async function api(path) {
  const hdr = API_KEY ? { 'X-API-Key': API_KEY } : {};
  if (window.__STATIC__ && path.startsWith('/api/music/v2/')) {
    const name = path.split('/api/music/v2/')[1].split('?')[0];
    const r = await fetch('data/' + name + '.json');
    if (!r.ok) throw new Error('no static data: ' + name);
    return r.json();
  }
  const r = await fetch(API_BASE + path, { headers: hdr });
  if (r.status === 401) { toast('需要 API Key'); return null; }
  if (!r.ok) throw new Error('HTTP ' + r.status);
  return r.json();
}

async function apiPost(path, body) {
  const hdr = API_KEY ? { 'X-API-Key': API_KEY, 'Content-Type': 'application/json' } : {};
  if (window.__STATIC__) throw new Error('静态模式不支持提交');
  const r = await fetch(API_BASE + path, { method: 'POST', headers: hdr, body: JSON.stringify(body) });
  if (r.status === 401) { toast('需要 API Key'); return null; }
  if (!r.ok) {
    let msg = 'HTTP ' + r.status;
    try { const e = await r.json(); if (e && e.detail) msg = e.detail; } catch (_) {}
    throw new Error(msg);
  }
  return r.json();
}

async function apiPostMultipart(path, formData) {
  const hdr = API_KEY ? { 'X-API-Key': API_KEY } : {};
  if (window.__STATIC__) throw new Error('静态模式不支持提交');
  const r = await fetch(API_BASE + path, { method: 'POST', headers: hdr, body: formData });
  if (r.status === 401) { toast('需要 API Key'); return null; }
  if (!r.ok) {
    let msg = 'HTTP ' + r.status;
    try { const e = await r.json(); if (e && e.detail) msg = e.detail; } catch (_) {}
    throw new Error(msg);
  }
  return r.json();
}

