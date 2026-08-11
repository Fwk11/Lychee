/* lychee 前端 · theme.js
 * 主题切换：读取/写入 localStorage 主题、切换、更新图标
 */

/* ---------- 主题切换 ---------- */
function initTheme() {
  const saved = localStorage.getItem('lychee_theme') || 'dark';
  document.documentElement.dataset.theme = saved;
  updateThemeIcon(saved);
}
function toggleTheme() {
  const next = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
  document.documentElement.dataset.theme = next;
  localStorage.setItem('lychee_theme', next);
  updateThemeIcon(next);
}
function updateThemeIcon(theme) {
  $('#theme-toggle').textContent = theme === 'dark' ? '☾' : '☀';
}

