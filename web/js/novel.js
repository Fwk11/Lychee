/* lychee 前端 · novel.js
 * 小说剧场：书架/上传/搜索/URL 下载、起点智能导入、全局事件绑定
 */

/* ---------- 小说上传 / 搜索 / URL 下载 ---------- */
async function novelUpload(file) {
  if (!file) return;
  const st = $('#novel-upload-status');
  if (window.__STATIC__ && !window.__API_BASE__) { st.textContent = STATIC_MSG; return; }
  st.textContent = `上传中：${file.name}…`;
  const fd = new FormData(); fd.append('file', file);
  try {
    const r = await fetch(API_BASE + '/api/novel/upload', { method: 'POST', headers: API_KEY ? { 'X-API-Key': API_KEY } : {}, body: fd });
    const text = await r.text(); let d = {}; try { d = JSON.parse(text); } catch (_) {}
    if (!r.ok) { st.textContent = '上传失败：' + (fmtDetail(d.detail) || r.statusText || r.status); return; }
    st.textContent = `✓ 已上传《${d.name}》`;
    loadNovels();
  } catch (e) { st.textContent = '上传失败：' + e.message; }
}

const STATIC_MSG = '当前为静态预览站，小说剧场需启动本地后端 http://127.0.0.1:8000';

function staticNovelHint(box) {
  if (box) box.innerHTML = `<p class="muted small">${STATIC_MSG}</p>`;
  else toast(STATIC_MSG);
}

async function novelSearch() {
  const input = $('#novel-search-input');
  const box = $('#novel-search-results');
  const title = input.value.trim();
  if (!title) return;
  box.style.display = 'block';
  if (window.__STATIC__ && !window.__API_BASE__) { staticNovelHint(box); return; }
  box.innerHTML = '<p class="muted small">正在搜索…</p>';
  try {
    const d = await apiPost('/api/novel/search', { title, source: NOVEL_SRC });
    if (!d.results || !d.results.length) { box.innerHTML = `<p class="muted small">${esc(d.message || '未找到结果')}</p>`; return; }
    box.innerHTML = '<p class="muted small">点击书籍即可下载：</p>' + d.results.map(r => `
      <div class="vitem" data-download-url="${esc(r.url)}" data-download-title="${esc(r.title)}" data-download-source="${esc(NOVEL_SRC)}" style="cursor:pointer">
        <div class="vname">${esc(r.title)}</div>
        <div class="vmeta muted small">${esc(r.author || '未知作者')} · ${esc(r.source)} ${r.status ? '· ' + esc(r.status) : ''}</div>
      </div>`).join('');
  } catch (e) { box.innerHTML = '<p class="muted small">搜索失败：' + esc(e.message) + '</p>'; }
}

async function novelDownloadFromSearch(el) {
  if (window.__STATIC__ && !window.__API_BASE__) { staticNovelHint($('#novel-search-results')); return; }
  const url = el.dataset.downloadUrl;
  const title = el.dataset.downloadTitle;
  const source = el.dataset.downloadSource || 'qidian';
  const box = $('#novel-search-results');
  box.innerHTML = `<p class="muted small">正在下载《${esc(title)}》…</p>`;
  try {
    const d = await apiPost('/api/novel/download', { url, title, source });
    const extra = d.content_available === false
      ? '<br><span style="color:var(--acc)">⚠ 起点反爬拦截了正文：上方元数据/目录已存，请改用「起点·粘贴正文」补全。</span>'
      : '';
    box.innerHTML = `<p class="muted small">✓ 已下载《${esc(d.title || title)}》（${d.n_chapters_downloaded != null ? d.n_chapters_downloaded + '章正文 · ' : ''}${d.n_chapters_catalog ? d.n_chapters_catalog + '章目录 · ' : ''}${( (d.n_chars||0) / 10000).toFixed(1)}万字）</p>${extra}`;
    loadNovels();
  } catch (e) { box.innerHTML = '<p class="muted small">下载失败：' + esc(e.message) + '</p>'; }
}

// 起点·智能导入：粘贴 URL 或正文，自动识别路由
function autoNovelTitle(text) {
  const m = text.match(/《([^》]+)》/);
  if (m) return m[1].trim();
  const firstLine = text.split(/\n/).map(s => s.trim()).find(s => s.length > 0) || '';
  return firstLine.slice(0, 30).trim() || '粘贴小说';
}

async function novelSmartImport() {
  const raw = $('#qidian-smart-input').value.trim();
  const st = $('#novel-upload-status');
  if (!raw) { st.textContent = '请粘贴起点书籍页网址或章节正文'; return; }
  if (window.__STATIC__ && !window.__API_BASE__) { st.textContent = STATIC_MSG; return; }
  st.textContent = '识别导入中…';
  // 自动识别：含起点域名或以 http 开头 → 视为书籍页网址；否则视为正文
  const isUrl = /qidian\.com/i.test(raw) || /^https?:\/\//i.test(raw);
  try {
    let d;
    if (isUrl) {
      d = await apiPost('/api/novel/download', { url: raw, source: 'qidian' });
    } else {
      d = await apiPost('/api/novel/download', { title: autoNovelTitle(raw), author: '', text: raw, source: 'paste' });
    }
    st.textContent = `✓ 已导入《${d.title || '未知'}》`
      + (d.content_available === false ? '（起点反爬拦截了正文，请直接粘贴章节正文补全）' : '');
    $('#qidian-smart-input').value = '';
    loadNovels();
  } catch (e) { st.textContent = '导入失败：' + e.message; }
}

/* ---------- 事件绑定 ---------- */
document.addEventListener('click', e => {
  const tab = e.target.closest('nav .tab');
  if (tab) return switchTab(tab.dataset.tab);
  if (e.target.closest('#theme-toggle')) return toggleTheme();

  const vitem = e.target.closest('#video-list .vitem');
  // 批量：勾选框 / 单个删除按钮 不触发打开
  if (e.target.closest('.vsel')) { updateBatchCount(); return; }
  if (e.target.closest('.del')) return;
  if (vitem && !vitem.classList.contains('pending') && vitem.dataset.video) {
    return openVideo(vitem.dataset.video, vitem.dataset.vid);
  }
  if (e.target.closest('#btn-reload-videos')) return loadVideos();
  if (e.target.closest('#video-select-all')) { syncSelectAll(); return; }
  if (e.target.closest('#btn-batch-delete')) return batchDeleteVideos();
  const exportBtn = e.target.closest('[data-export]');
  if (exportBtn) return exportAnnotations(exportBtn.dataset.export);

  const nitem = e.target.closest('#novel-list .vitem');
  const delNovel = e.target.closest('.del-novel');
  if (delNovel) { e.stopPropagation(); return deleteNovel(delNovel.dataset.delNovel); }
  if (nitem && nitem.dataset.novel) return openNovel(nitem.dataset.novel);
  if (e.target.closest('#btn-reload-novels')) return loadNovels();
  if (e.target.closest('#btn-novel-search')) return novelSearch();
  const dlitem = e.target.closest('#novel-search-results .vitem');
  if (dlitem && dlitem.dataset.downloadUrl) return novelDownloadFromSearch(dlitem);
  const sbtn = e.target.closest('[data-storyboard-chapter]');
  if (sbtn) return novelStoryboardChapter(+sbtn.dataset.storyboardChapter);
  const cbtn = e.target.closest('[data-copy]');
  if (cbtn) return copyText(cbtn.dataset.copy);
  if (e.target.closest('#btn-qidian-smart')) return novelSmartImport();

  // 视频聊天附件删除
  const rmAttach = e.target.closest('[data-rm-attach]');
  if (rmAttach) return removeChatAttachment(+rmAttach.dataset.rmAttach);

  // 章节行点击：阅读内容
  const chrow = e.target.closest('.chapter-row');
  if (chrow && NOVEL.current && chrow.dataset.chapter) {
    return readChapter(+chrow.dataset.chapter);
  }

  // 阅读器控制
  if (e.target.closest('#chapter-modal-close')) return closeChapterModal();
  // 点击遮罩空白处关闭弹窗（点到卡片内部不关）
  if (e.target.id === 'chapter-modal') return closeChapterModal();
  if (e.target.closest('#reader-toc-toggle')) { e.stopPropagation(); return toggleReaderToc(); }
  if (e.target.closest('#reader-toc-close')) { e.stopPropagation(); return toggleReaderToc(); }
  const tocItem = e.target.closest('[data-toc-idx]');
  if (tocItem) { e.stopPropagation(); return readChapter(+tocItem.dataset.tocIdx); }
  if (e.target.closest('#reader-prev')) { e.stopPropagation(); return readerPrev(); }
  if (e.target.closest('#reader-next')) { e.stopPropagation(); return readerNext(); }
  if (e.target.closest('#reader-font-plus')) { e.stopPropagation(); return readerFontSize(1); }
  if (e.target.closest('#reader-font-minus')) { e.stopPropagation(); return readerFontSize(-1); }
  if (e.target.closest('#reader-settings-toggle')) { e.stopPropagation(); return toggleReaderSettings(); }
  // 阅读设置分段选择
  const seg = e.target.closest('.rs-seg button');
  if (seg) {
    e.stopPropagation();
    const group = seg.parentElement.id.replace('rs-', '');
    const value = seg.dataset.v;
    setReaderSetting(group, value, seg.parentElement.id);
    if (group === 'theme') $$('#reader-settings .rs-seg button').forEach(b => b.classList.toggle('active', b.dataset.v === READER.theme));
    return;
  }
  const tocSearch = e.target.closest('#reader-toc-search');
  if (tocSearch) { e.stopPropagation(); return renderReaderToc(); }
});

$('#novel-upload-input')?.addEventListener('change', e => novelUpload(e.target.files[0]));
$('#novel-search-input')?.addEventListener('keydown', e => { if (e.key === 'Enter') novelSearch(); });
initVideoUpload();

function initVideoUpload() {
  const dz = $('#upload-dropzone');
  const inp = $('#video-file-input');
  const btn = $('#upload-select-btn');
  if (btn) btn.onclick = () => inp && inp.click();
  if (inp) inp.onchange = e => { const fs = Array.from(e.target.files || []); if (fs.length) autoAnnotateFiles(fs); inp.value = ''; };
  if (dz) {
    dz.addEventListener('dragover', e => { e.preventDefault(); dz.classList.add('dragover'); });
    dz.addEventListener('dragleave', () => dz.classList.remove('dragover'));
    dz.addEventListener('drop', e => { e.preventDefault(); dz.classList.remove('dragover'); const fs = Array.from(e.dataTransfer.files || []).filter(f => f.type.startsWith('video/')); if (fs.length) autoAnnotateFiles(fs); });
  }
}

async function autoAnnotateFiles(files) {
  const q = $('#upload-queue');
  if (!q) return;
  for (const f of files) {
    const el = document.createElement('div');
    el.className = 'queue-item';
    el.innerHTML = '<span class="qi-name">' + esc(f.name) + '</span><span class="qi-status">上传中…</span>';
    q.appendChild(el);
    const fd = new FormData(); fd.append('files', f);
    try {
      const r = await fetch(API_BASE + '/api/videos/upload?fast=false', { method: 'POST', headers: API_KEY ? { 'X-API-Key': API_KEY } : {}, body: fd });
      const d = await r.json();
      if (!r.ok) throw new Error(fmtDetail(d.detail) || r.statusText);
      const items = (d.results || []).filter(x => x.status === 'started');
      if (!items.length) throw new Error(fmtDetail(d.results?.[0]?.detail) || '上传失败');
      const item = items[0];
      el.querySelector('.qi-status').textContent = '分析中…';
      pollUploadTask(item.task_id, item.name, item.video_id);
    } catch (e) {
      el.querySelector('.qi-status').textContent = '✗ ' + e.message;
    }
  }
}

function pollUploadTask(taskId, name, videoId, _miss) {
  _miss = _miss || 0;
  const fail = (msg) => {
    const p = (window._pendingAnnotations || []).find(x => x.video_id === videoId);
    if (p) p.status = 'error';
    loadVideos();
    if (typeof toast === 'function') toast(msg);
  };
  fetch(API_BASE + '/api/tasks/' + taskId, { headers: { 'X-API-Key': API_KEY } })
    .then(r => r.json().then(t => ({ code: r.status, t })))
    .then(({ code, t }) => {
      if (code === 404 || !t.status) {
        if (++_miss >= 2) return fail('任务已中断，请重新标注');
        return setTimeout(() => pollUploadTask(taskId, name, videoId, _miss), 2000);
      }
      if (t.status === 'done') {
        window._pendingAnnotations = (window._pendingAnnotations || []).filter(p => p.video_id !== videoId);
        loadVideos();
        openVideo(name, videoId);
      } else if (t.status === 'error') {
        window._pendingAnnotations = (window._pendingAnnotations || []).filter(p => p.video_id !== videoId);
        const p = window._pendingAnnotations?.find(x => x.video_id === videoId);
        if (p) p.status = 'error';
        loadVideos();
      } else {
        setTimeout(() => pollUploadTask(taskId, name, videoId, 0), 2000);
      }
    }).catch(() => {
      if (++_miss >= 10) return fail('连不上后端，请确认后端运行后重试');
      setTimeout(() => pollUploadTask(taskId, name, videoId, _miss), 3000);
    });
}

/* =========================================================
 *  创新系统 v3：极光背景 / 聚光灯 / 进度条 / 3D倾斜 / 画像 / ⌘K
 * ========================================================= */

