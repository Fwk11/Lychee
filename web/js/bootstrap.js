/* lychee 前端 · bootstrap.js
 * 启动：初始化主题与特效，恢复上次 Tab，首次进入加载音乐
 */

/* ---------- 键盘快捷键 ---------- */
document.addEventListener('keydown', e => {
  if (e.target.matches('input, textarea')) return;
  const modalOpen = !$('#chapter-modal').classList.contains('hidden');
  if (modalOpen) {
    if (e.key === 'Escape') { closeChapterModal(); return; }
    if (e.key === 'ArrowLeft') { readerPrev(); return; }
    if (e.key === 'ArrowRight') { readerNext(); return; }
  }
  if (e.key === '1') switchTab('music');
  if (e.key === '2') switchTab('video');
  if (e.key === '3') switchTab('novel');
  if (e.key.toLowerCase() === 't') toggleTheme();
});

/* ---------- 启动 ---------- */
initTheme();
loadReaderPrefs();
const _readerBody = $('#chapter-modal-body');
if (_readerBody) _readerBody.addEventListener('scroll', onReaderScroll);
if (window.__STATIC__ && !window.__API_BASE__) {
  const sb = $('#static-banner');
  if (sb) { sb.classList.remove('hidden'); sb.classList.add('show'); }
  const nsh = $('#novel-static-hint');
  const nctrl = $('#novel-controls');
  if (nsh && nctrl) { nsh.classList.remove('hidden'); nctrl.style.display = 'none'; }
}
initAurora();
initSpotlight();
initScrollProgress();
const savedTab = localStorage.getItem('lychee_tab');
if (savedTab && ['music', 'video', 'novel'].includes(savedTab)) {
  switchTab(savedTab);
} else {
  loadMusic();
}
