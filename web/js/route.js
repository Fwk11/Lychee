/* lychee 前端 · route.js
 * 路由：switchTab 切换 Tab 并懒加载对应模块数据
 */

/* ---------- 路由 ---------- */
function switchTab(tab) {
  $$('nav .tab').forEach(b => b.classList.toggle('active', b.dataset.tab === tab));
  $$('.tabpage').forEach(p => p.classList.add('hidden'));
  const page = $('#tab-' + tab);
  if (page) page.classList.remove('hidden');
  localStorage.setItem('lychee_tab', tab);
  if (tab === 'music') loadMusic();
  if (tab === 'video') loadVideos();
  if (tab === 'novel') loadNovels();
}

/* =========================================================
 *  音乐模块
 * ========================================================= */
