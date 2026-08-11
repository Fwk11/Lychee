/* lychee 动态前端 v2
 * 双模式：本地 FastAPI (window.__STATIC__=false) 走 /api；
 *        静态部署 (window.__STATIC__=true) 读打包的 /data/*.json
 */
'use strict';

const API_BASE = window.__API_BASE__ || '';
let API_KEY = new URLSearchParams(location.search).get('key') || window.__API_KEY__ || localStorage.getItem('aa_key') || '';
if (API_KEY) localStorage.setItem('aa_key', API_KEY);

/* ---------- 工具 ---------- */
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
// 把后端 detail（字符串 / 对象 / 数组 / undefined）统一转成人话
const fmtDetail = x => {
  if (x == null) return '';
  if (typeof x === 'string') return x;
  if (Array.isArray(x)) return x.map(fmtDetail).filter(Boolean).join('； ');
  if (x.detail) return fmtDetail(x.detail);
  if (x.message || x.msg) return x.message || x.msg;
  try { return JSON.stringify(x); } catch { return String(x); }
};
function toast(msg) {
  const t = $('#toast'); t.textContent = msg; t.classList.add('show');
  clearTimeout(t._t); t._t = setTimeout(() => t.classList.remove('show'), 2600);
}
const PALETTE = ['#8b5cf6','#38bdf8','#f472b6','#34d399','#fbbf24','#fb7185','#a78bfa','#2dd4bf','#f59e0b','#c084fc'];

