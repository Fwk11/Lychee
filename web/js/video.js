/* lychee 前端 · video.js
 * 视频模块：标注记录列表、对话式标注、上传、批量删除、报告渲染、读者偏好
 */

/* ---------- 滚动渐显 ---------- */
function observeReveal() {
  if (!window.IntersectionObserver) return;
  const io = new IntersectionObserver(entries => {
    entries.forEach(e => { if (e.isIntersecting) e.target.classList.add('in'); });
  }, { threshold: .12, rootMargin: '0px 0px -30px 0px' });
  $$('.reveal').forEach(el => io.observe(el));
}

/* =========================================================
 *  视频模块（本地后端）
 * ========================================================= */
function loadVideos() {
  if (window.__STATIC__ && !window.__API_BASE__) {
    $('#video-list').innerHTML = '<div class="empty">静态部署版不含视频后端，请使用本地版</div>';
    $('#video-empty').classList.remove('hidden'); $('#video-report').classList.add('hidden');
    return;
  }
  fetch(API_BASE + '/api/reports', { headers: API_KEY ? { 'X-API-Key': API_KEY } : {} })
    .then(r => r.json()).then(d => {
      const records = d.reports || [];
      // 合并正在上传/分析中的临时记录
      const pending = (window._pendingAnnotations || []).filter(p => !records.some(r => r.video_id === p.video_id));
      const all = [...pending, ...records];
      $('#video-list').innerHTML = all.map(v => {
        const dur = v.duration_sec ? `${Math.round(v.duration_sec)}s` : '';
        const shots = v.shot_count ? `${v.shot_count} 镜` : '';
        const meta = [dur, shots, v.analyzed_at || ''].filter(Boolean).join(' · ');
        const isPending = v.status === 'analyzing';
        return `<div class="vitem ${isPending ? 'pending' : ''}" data-video="${esc(v.name)}" data-vid="${esc(v.video_id)}">
          <input type="checkbox" class="vsel" data-vid="${esc(v.video_id)}">
          <span class="nm">${esc(v.name)}</span>
          <span class="badge ${isPending ? 'warn' : 'ok'}">${isPending ? '分析中' : '已完成'}</span>
          ${meta ? `<span class="muted tiny">${esc(meta)}</span>` : ''}
          <button class="del" data-del="${esc(v.video_id)}" title="删除该记录">✕</button></div>`;
      }).join('') || '<div class="empty">暂无标注记录</div>';
      $('#video-batch-bar').classList.toggle('hidden', all.length === 0);
      $('#video-select-all').checked = false;
      updateBatchCount();
    }).catch(() => $('#video-list').innerHTML = '<div class="empty">加载失败</div>');
}

// 收集当前勾选的视频 id
function selectedVideoIds() {
  return $$('#video-list .vsel:checked').map(c => c.dataset.vid);
}
function updateBatchCount() {
  const boxes = $$('#video-list .vsel');
  const n = boxes.filter(c => c.checked).length;
  $('#video-sel-count').textContent = `已选 ${n} 项`;
  $('#btn-batch-delete').disabled = n === 0;
  $('#btn-batch-export-csv').disabled = n === 0;
  $('#btn-batch-export-json').disabled = n === 0;
  const master = $('#video-select-all');
  if (master) master.checked = boxes.length > 0 && n === boxes.length;
}

// 全选 / 取消全选
function syncSelectAll() {
  const all = $('#video-select-all')?.checked;
  $$('#video-list .vsel').forEach(c => { c.checked = !!all; });
  updateBatchCount();
}

// 视频记录删除（事件委托，脚本加载时绑定一次）
document.addEventListener('click', e => {
  const del = e.target.closest('.del');
  if (!del) return;
  const vid = del.dataset.del;
  const name = del.closest('.vitem')?.querySelector('.nm')?.textContent || vid;
  if (!confirm(`删除标注记录「${name}」？\n\n对应的本地视频副本也将被删除（你本地上传的只是源文件的一个复制品）。`)) return;
  del.disabled = true; del.textContent = '…';
  fetch(API_BASE + '/api/reports/' + vid + '?include_source=true', { method: 'DELETE', headers: { 'X-API-Key': API_KEY } })
    .then(r => r.json()).then(d => {
      toast('✓ 已删除：' + (d.removed && d.removed.length ? d.removed.join('/') : '无记录'));
      loadVideos();
      if (window._currentVideoId === vid) { $('#video-report').classList.add('hidden'); $('#video-empty').classList.remove('hidden'); }
    })
    .catch(err => toast('删除失败：' + err.message))
    .finally(() => { del.disabled = false; del.textContent = '✕'; });
});

// 批量删除：收集勾选项，一次请求
async function batchDeleteVideos() {
  const boxes = $$('#video-list .vsel:checked');
  if (!boxes.length) return;
  const ids = boxes.map(c => c.dataset.vid);
  const names = boxes.map(c => c.closest('.vitem')?.querySelector('.nm')?.textContent || c.dataset.vid);
  if (!confirm(`确认批量删除 ${ids.length} 个标注记录？\n\n对应的本地视频副本也将被删除。\n\n` + names.join('、'))) return;
  const btn = $('#btn-batch-delete');
  btn.disabled = true; btn.textContent = '删除中…';
  try {
    const d = await apiPost('/api/reports/batch-delete', { video_ids: ids, include_source: true });
    toast(`✓ 已删除 ${d.deleted} 项，跳过 ${d.skipped} 项`);
    btn.disabled = false; btn.textContent = '🗑';
    loadVideos();
  } catch (err) {
    toast('批量清除失败：' + err.message);
    btn.disabled = false; btn.textContent = '🗑';
  }
}

// 批量导出：收集勾选项，一次请求下载单一文件（与批量删除同一套勾选机制）
async function batchExport(format) {
  const ids = selectedVideoIds();
  const all = ids.length === 0;
  if (all && !confirm('未勾选任何记录，是否导出全部已标注视频？')) return;
  const btn = format === 'csv' ? $('#btn-batch-export-csv') : $('#btn-batch-export-json');
  const old = btn.textContent;
  btn.disabled = true; btn.textContent = '导出中…';
  try {
    const r = await fetch(API_BASE + '/api/reports/annotations/batch', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-API-Key': API_KEY },
      body: JSON.stringify({ video_ids: ids, format })
    });
    if (!r.ok) throw new Error(fmtDetail((await r.json()).detail) || r.statusText);
    const blob = await r.blob();
    const m = (r.headers.get('Content-Disposition') || '').match(/filename="?([^"]+)"?/);
    downloadBlob(blob, m ? m[1] : `lychee_annotations_batch.${format}`);
    toast(`✓ 已批量导出 ${all ? '全部' : ids.length + ' 条'}（${format.toUpperCase()}）`);
  } catch (e) {
    toast('批量导出失败：' + e.message);
  } finally {
    btn.disabled = false; btn.textContent = old;
  }
}

// 导出按钮接线：单条报告的「导出 JSON/CSV」+ 批量栏「批量导出 CSV/JSON」
document.addEventListener('click', e => {
  const exp = e.target.closest('[data-export]');
  if (exp) { e.preventDefault(); exportAnnotations(exp.dataset.export); return; }
  const bec = e.target.closest('#btn-batch-export-csv');
  if (bec) { e.preventDefault(); batchExport('csv'); return; }
  const bej = e.target.closest('#btn-batch-export-json');
  if (bej) { e.preventDefault(); batchExport('json'); return; }
});

function openVideo(name, vid) {
  $$('#video-list .vitem').forEach(e => e.classList.toggle('sel', e.querySelector('.nm').textContent === name));
  window._currentVideoId = vid || name.replace(/\.[^.]+$/, '');
  $('#video-empty').classList.add('hidden'); $('#video-report').classList.remove('hidden');
  $('#video-report').innerHTML = '<div class="skeleton" style="height:200px"></div>';
  fetch(API_BASE + '/api/reports/' + encodeURIComponent(window._currentVideoId), { headers: { 'X-API-Key': API_KEY } })
    .then(r => {
      if (r.ok) return r.json().then(renderReport);
      // 报告尚不存在：尝试触发分析（兼容旧 raw-video 入口）
      return fetch(API_BASE + '/api/analyze', { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-API-Key': API_KEY },
        body: JSON.stringify({ video: name, fast: false }) })
        .then(r2 => r2.json()).then(d => {
          if (d.status === 'cached') return fetch(API_BASE + '/api/reports/' + encodeURIComponent(d.video_id), { headers: { 'X-API-Key': API_KEY } }).then(r3 => r3.json()).then(renderReport);
          pollTask(d.task_id, d.video_id);
        });
    })
    .catch(e => $('#video-report').innerHTML = '<div class="empty">加载失败：' + e.message + '</div>');
}
function pollTask(taskId, vid, _miss) {
  _miss = _miss || 0;
  fetch(API_BASE + '/api/tasks/' + taskId, { headers: { 'X-API-Key': API_KEY } })
    .then(r => r.json().then(t => ({ code: r.status, t })))
    .then(({ code, t }) => {
      if (code === 404 || !t.status) {
        if (++_miss >= 2) { $('#video-report').innerHTML = '<div class="empty">任务已中断，请重新分析</div>'; return; }
        return setTimeout(() => pollTask(taskId, vid, _miss), 2000);
      }
      if (t.status === 'done') {
        fetch(API_BASE + '/api/reports/' + encodeURIComponent(vid), { headers: { 'X-API-Key': API_KEY } }).then(r => r.json()).then(renderReport);
      } else if (t.status === 'error') {
        $('#video-report').innerHTML = '<div class="empty">分析出错</div>';
      } else { setTimeout(() => pollTask(taskId, vid, 0), 1500); }
    })
    .catch(() => {
      if (++_miss >= 10) { $('#video-report').innerHTML = '<div class="empty">连不上后端，请确认后端运行后重试</div>'; return; }
      setTimeout(() => pollTask(taskId, vid, _miss), 3000);
    });
}
function renderReport(r) {
  const shots = r.shots || [];
  const vid = r.video_id || '';
  const cm2 = { compliant: '合规', review: '需复核', blocked: '违规' };
  let html = `<div class="panel-head">
    <h3>自动标注结果 · ${esc(r.source || vid)} · ${shots.length} 个镜头</h3>
    <div style="display:flex;gap:8px;flex-wrap:nowrap;align-items:center;justify-content:flex-end">
      <button class="btn small" data-export="json">导出 JSON</button>
      <button class="btn small" data-export="csv">导出 CSV</button>
      <button class="btn small accent" data-ls-push title="自动建项目并导入带预标注的视频任务，打开即见时间轴">🚀 一键推送 LS</button>
      <button class="btn small ghost" data-ls-settings title="设置你自己的 Label Studio 地址与 API Key">⚙ LS 设置</button>
    </div>
  </div>`;
  const compliant = shots.filter(s => (s.compliance||{}).verdict === 'compliant').length;
  const review = shots.filter(s => (s.compliance||{}).verdict === 'review').length;
  const blocked = shots.filter(s => (s.compliance||{}).verdict === 'blocked').length;
  html += `<div class="hero-kpi" style="margin-bottom:16px">
    <div class="hk"><span class="n">${shots.length}</span><span class="l">镜头总数</span></div>
    <div class="hk"><span class="n" style="color:var(--ok)">${compliant}</span><span class="l">合规</span></div>
    <div class="hk"><span class="n" style="color:var(--warn)">${review}</span><span class="l">需复核</span></div>
    <div class="hk"><span class="n" style="color:var(--bad)">${blocked}</span><span class="l">违规</span></div>
  </div>`;
  html += shots.map(s => {
    const cm = s.compliance || {};
    const cverdict = cm.verdict || 'compliant';
    const lbl = cm2[cverdict];
    const sc = s.scores || {};
    const color = s.color || {};
    const dom = (color.dominant_colors || []).map(c => {
      const hex = '#' + [c[0],c[1],c[2]].map(v => v.toString(16).padStart(2,'0')).join('');
      return `<span style="display:inline-block;width:18px;height:18px;border-radius:4px;background:${hex};border:1px solid rgba(255,255,255,.2);vertical-align:middle" title="${hex}"></span>`;
    }).join('');
    const frameBase = `${API_BASE}/api/reports/${encodeURIComponent(vid)}/shots/${encodeURIComponent(s.shot_id)}/frame?key=${encodeURIComponent(API_KEY)}`;
    const frameUrl = `${frameBase}&t=${Date.now()}_${s.shot_id}`;
    const staticHint = window.__STATIC__ ? `<div class="muted small" style="padding:4px 0 8px">静态预览：封面需连接本地后端</div>` : '';
    return `<div class="shot">
      <div class="frame-box" style="position:relative;width:100%;min-height:100px;background:var(--card);border-radius:6px;overflow:hidden;margin-bottom:6px">
        <img src="${frameUrl}" data-shot="${esc(s.shot_id)}" data-frame-base="${esc(frameBase)}" onload="this.style.opacity=1;this.parentElement.querySelector('.frame-err').classList.add('hidden')" onerror="window.refreshFrame&&window.refreshFrame('${esc(s.shot_id)}');this.parentElement.querySelector('.frame-err').classList.remove('hidden')" style="width:100%;max-height:160px;object-fit:cover;display:block;transition:opacity .2s">
        <div class="frame-err hidden" style="position:absolute;inset:0;display:flex;flex-direction:column;align-items:center;justify-content:center;background:var(--bg);color:var(--muted);font-size:12px;padding:12px;text-align:center;gap:8px">
          <span>${window.__STATIC__ ? '静态站无法显示封面<br>请连接本地后端' : '封面加载失败<br>请确认后端运行且视频源文件存在'}</span>
          ${window.__STATIC__ ? '' : `<button type="button" class="btn tiny" onclick="window.refreshFrame('${esc(s.shot_id)}')">刷新封面</button>`}
        </div>
      </div>
      ${staticHint}
      <div>
        <div class="h">
          <b>#${esc(s.shot_id)}</b>
          <span class="compliance ${cverdict}">${lbl}</span>
          <span class="muted small">${Math.round(s.start_sec)}–${Math.round(s.end_sec)}s</span>
          ${s.camera_move ? `<span class="tag">${esc(s.camera_move)}</span>` : ''}
          ${s.shot_scale ? `<span class="tag">${esc(s.shot_scale)}</span>` : ''}
          ${s.composition ? `<span class="tag">${esc(s.composition)}</span>` : ''}
          ${s.mood ? `<span class="tag">${esc(s.mood)}</span>` : ''}
        </div>
        <div class="muted small" style="margin:4px 0">${esc(s.content_caption || '—')}</div>
        <div class="tags" style="margin-bottom:6px">
          ${dom ? `<span class="tag">主色 ${dom}</span>` : ''}
          ${color.color_temp ? `<span class="tag">${esc(color.color_temp)}</span>` : ''}
          ${(s.lighting||{}).exposure ? `<span class="tag">曝光:${esc(s.lighting.exposure)}</span>` : ''}
          ${(cm.faces_detected||0) > 0 ? `<span class="tag">人脸:${cm.faces_detected}</span>` : ''}
          ${s.aesthetic_score ? `<span class="tag">美学 ${esc(String(s.aesthetic_score))}/10</span>` : ''}
        </div>
        <div class="factors" style="margin-top:6px">
          ${factorBar('色彩', (sc.color_score || 0) / 10, '')}
          ${factorBar('构图', (sc.composition_score || 0) / 10, '')}
          ${factorBar('光线', (sc.lighting_score || 0) / 10, '')}
          ${factorBar('美学', (sc.aesthetic_proxy || 0) / 10, 'ml')}
        </div>
      </div></div>`;
  }).join('');
  $('#video-report').innerHTML = html;
  animateBars();
}

async function exportAnnotations(format) {
  const vid = window._currentVideoId || '';
  if (!vid) { toast('请先选择视频'); return; }
  toast('正在生成标注文件…');
  try {
    const r = await fetch(API_BASE + `/api/reports/${vid}/annotations?format=${format}`, { headers: { 'X-API-Key': API_KEY } });
    if (!r.ok) throw new Error(fmtDetail((await r.json()).detail) || r.statusText);
    if (format === 'csv') {
      const blob = await r.blob();
      downloadBlob(blob, `${vid}_annotations.csv`);
      toast('✓ CSV 已下载');
    } else {
      const d = await r.json();
      const blob = new Blob([JSON.stringify(d, null, 2)], { type: 'application/json' });
      downloadBlob(blob, `${vid}_annotations_${format}.json`);
      toast('✓ JSON 已下载');
    }
  } catch (e) { toast('导出失败：' + e.message); }
}

function downloadBlob(blob, filename) {
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = filename;
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 5000);
}

/* ---------- Label Studio 一键推送 + 每使用者自配凭据 ---------- */

function pushToLabelStudio() {
  const vid = window._currentVideoId || '';
  if (!vid) { toast('请先选择一条标注记录'); return; }
  toast('正在推送到 Label Studio…');
  apiPost('/api/label-studio/push', { video_id: vid })
    .then(d => {
      if (!d.ok) { toast('推送失败：' + (d.error || '未知错误')); return; }
      toast('✓ 已推送，正在打开 Label Studio（打开即见时间轴标注）');
      if (d.editor_url) window.open(d.editor_url, '_blank');
    })
    .catch(e => toast('推送失败：' + e.message));
}

function openLsSettings() {
  api('/api/label-studio/config')
    .then(cfg => {
      const url = (cfg && cfg.url) || 'http://127.0.0.1:8080';
      const backdrop = document.createElement('div');
      backdrop.className = 'ls-settings-backdrop';
      backdrop.innerHTML = `<div class="ls-settings-card">
        <div class="ls-settings-head">Label Studio 设置 <button class="agent-close" id="ls-settings-x" type="button">✕</button></div>
        <p class="muted small">填写你自己的 Label Studio 地址与 API Key（仅存本机，不进代码 / git）。别人用各自填自己的，互不影响、互不覆盖。</p>
        <label>地址</label>
        <input id="ls-url" class="ls-input" value="${esc(url)}">
        <label>API Key</label>
        <input id="ls-key" class="ls-input" type="password" placeholder="在 Label Studio 个人设置 → Account &amp; Settings 里生成">
        <div class="ls-settings-actions">
          <button class="btn small ghost" id="ls-settings-cancel" type="button">取消</button>
          <button class="btn small accent" id="ls-settings-save" type="button">保存</button>
        </div>
      </div>`;
      document.body.appendChild(backdrop);
      const close = () => backdrop.remove();
      backdrop.addEventListener('click', e => { if (e.target === backdrop) close(); });
      $('#ls-settings-x').onclick = close;
      $('#ls-settings-cancel').onclick = close;
      $('#ls-settings-save').onclick = async () => {
        const u = $('#ls-url').value.trim();
        const k = $('#ls-key').value.trim();
        if (!k) { toast('请填写 API Key'); return; }
        try {
          const r = await fetch(API_BASE + '/api/label-studio/config', {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json', 'X-API-Key': API_KEY },
            body: JSON.stringify({ url: u, api_key: k }),
          });
          if (!r.ok) throw new Error(fmtDetail((await r.json()).detail) || r.statusText);
          toast('✓ Label Studio 设置已保存');
          close();
        } catch (e) { toast('保存失败：' + e.message); }
      };
    })
    .catch(e => toast('读取设置失败：' + e.message));
}

document.addEventListener('click', e => {
  const push = e.target.closest('[data-ls-push]');
  if (push) { e.preventDefault(); pushToLabelStudio(); return; }
  const set = e.target.closest('[data-ls-settings]');
  if (set) { e.preventDefault(); openLsSettings(); return; }
});

window.refreshFrame = function(shotId) {
  const vid = window._currentVideoId || '';
  if (!vid) return;
  const img = $(`img[data-shot="${esc(shotId)}"]`);
  if (!img) return;
  const base = img.dataset.frameBase || `${API_BASE}/api/reports/${encodeURIComponent(vid)}/shots/${encodeURIComponent(shotId)}/frame?key=${encodeURIComponent(API_KEY)}`;
  img.src = `${base}&t=${Date.now()}`;
  img.style.opacity = .5;
};

/* ---------- 视频对话式标注（统一 LLM 助手：上传+聊天） ---------- */
function chatBubble(role, text, html = false) {
  const hist = $('#chat-history');
  const b = document.createElement('div');
  b.className = `chat-bubble ${role}`;
  if (html) b.innerHTML = text; else b.textContent = text;
  hist.appendChild(b);
  hist.scrollTop = hist.scrollHeight;
}

function renderChatAttachments() {
  const box = $('#chat-attachments');
  const files = window._chatAttachments || [];
  box.innerHTML = files.map((f, i) =>
    `<span class="chat-attach-file">${esc(f.name)} <button type="button" data-rm-attach="${i}">✕</button></span>`
  ).join('');
}

function addChatAttachments(files) {
  if (!window._chatAttachments) window._chatAttachments = [];
  window._chatAttachments.push(...files);
  renderChatAttachments();
}

function removeChatAttachment(idx) {
  if (!window._chatAttachments) return;
  window._chatAttachments.splice(idx, 1);
  renderChatAttachments();
}

async function uploadChatFiles(files) {
  if (!files.length) return [];
  const fd = new FormData();
  files.forEach(f => fd.append('files', f));
  const r = await fetch(API_BASE + '/api/videos/upload?fast=false', {
    method: 'POST', headers: API_KEY ? { 'X-API-Key': API_KEY } : {}, body: fd
  });
  const text = await r.text(); let d = {}; try { d = JSON.parse(text); } catch (_) {}
  if (!r.ok) throw new Error(fmtDetail(d.detail) || '上传失败');
  const results = (d.results || []).filter(x => x.status === 'started');
  // 立即显示“分析中”占位
  if (!window._pendingAnnotations) window._pendingAnnotations = [];
  results.forEach(item => {
    window._pendingAnnotations.push({
      video_id: item.video_id, name: item.name, status: 'analyzing', size_mb: item.size_mb,
    });
    pollUploadTask(item.task_id, item.name, item.video_id);
  });
  loadVideos();
  return results.map(x => x.name);
}

async function submitChat() {
  const input = $('#chat-msg');
  let msg = input.value.trim();
  const files = window._chatAttachments || [];
  if (!msg && !files.length) return;

  if (window.__STATIC__ && !window.__API_BASE__) {
    chatBubble('bot', '静态部署版不支持视频对话标注，请使用本地版 http://127.0.0.1:8000');
    return;
  }

  // 1) 先上传附件
  let uploadedNames = [];
  if (files.length) {
    chatBubble('system', `正在上传 ${files.length} 个视频…`);
    try {
      uploadedNames = await uploadChatFiles(files);
      const sys = $('#chat-history .system'); if (sys) sys.remove();
      chatBubble('system', `已上传：${uploadedNames.join('、')}`);
    } catch (e) {
      const sys = $('#chat-history .system'); if (sys) sys.remove();
      chatBubble('bot', '上传失败：' + e.message);
      return;
    }
    window._chatAttachments = [];
    renderChatAttachments();
  }

  // 2) 构造发送消息：如果用户没写文字，默认请求标注刚上传的视频
  if (!msg) {
    msg = uploadedNames.length
      ? `请帮我标注刚刚上传的视频：${uploadedNames.join('、')}`
      : '';
  } else if (uploadedNames.length) {
    msg = msg + `（刚刚上传：${uploadedNames.join('、')}）`;
  }
  if (!msg) return;

  chatBubble('user', msg);
  input.value = '';
  await sendChat(msg);
}

async function sendChat(msg) {
  if (window.__STATIC__ && !window.__API_BASE__) {
    chatBubble('bot', '静态部署版不支持视频对话标注，请使用本地版 http://127.0.0.1:8000');
    return;
  }
  chatBubble('system', '正在识别项目与视频…');
  try {
    const r = await fetch(API_BASE + '/api/video/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-API-Key': API_KEY },
      body: JSON.stringify({ message: msg })
    });
    let text = '';
    try { text = await r.text(); } catch (e) { text = ''; }
    const sys = $('#chat-history .system');
    if (sys) sys.remove();
    if (!r.ok) {
      let detail = text;
      try { detail = fmtDetail(JSON.parse(text).detail) || text; } catch {}
      chatBubble('bot', `请求失败（${r.status}）：${esc(String(detail).slice(0, 200)) || '服务器内部错误'}`);
      return;
    }
    let d = {};
    try { d = JSON.parse(text); } catch (e) {
      chatBubble('bot', '返回不是合法 JSON：' + esc(text.slice(0, 120)));
      return;
    }
    chatBubble('bot', d.reply || '已处理', true);
    if (d.started && d.started.length) {
      for (const task of d.started) pollChatTask(task.task_id, task.video_id);
    }
    if (d.cached && d.cached.length) loadVideos();
  } catch (e) {
    const sys = $('#chat-history .system');
    if (sys) sys.remove();
    chatBubble('bot', '请求失败：' + e.message);
  }
}

function pollChatTask(taskId, vid) {
  fetch(API_BASE + '/api/tasks/' + taskId, { headers: { 'X-API-Key': API_KEY } })
    .then(r => r.json()).then(t => {
      if (t.status === 'done') { chatBubble('system', `✓ ${vid} 分析完成`); loadVideos(); }
      else if (t.status === 'error') { chatBubble('system', `✗ ${vid} 分析失败`); }
      else { setTimeout(() => pollChatTask(taskId, vid), 2000); }
    }).catch(() => {});
}

/* =========================================================
 *  小说剧场模块
 * ========================================================= */
let NOVEL = { current: null };
let NOVEL_SRC = 'qidian';

function copyText(text) {
  if (!text) return;
  navigator.clipboard?.writeText(text).then(
    () => toast('已复制提示词到剪贴板'),
    () => toast('复制失败，请手动选择')
  );
}

async function apiPost(path, body) {
  const r = await fetch(API_BASE + path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-API-Key': API_KEY },
    body: JSON.stringify(body)
  });
  const text = await r.text();
  let d = {};
  try { d = JSON.parse(text); } catch (_) {}
  if (!r.ok) throw new Error(fmtDetail(d.detail) || ('HTTP ' + r.status));
  return d;
}

async function deleteNovel(name) {
  if (!name) return;
  if (!confirm(`确认删除《${name}》及其分析、音频、分镜缓存？\n\n原书籍文件也会删除，不可恢复。`)) return;
  try {
    const r = await fetch(API_BASE + '/api/novel/books/' + encodeURIComponent(name), {
      method: 'DELETE', headers: API_KEY ? { 'X-API-Key': API_KEY } : {}
    });
    const text = await r.text(); let d = {}; try { d = JSON.parse(text); } catch (_) {}
    if (!r.ok) throw new Error(fmtDetail(d.detail) || r.statusText || r.status);
    toast('✓ 已删除《' + name + '》');
    if (NOVEL.current === name) {
      $('#novel-detail').classList.add('hidden');
      $('#novel-empty').classList.remove('hidden');
      NOVEL.current = null;
    }
    loadNovels();
  } catch (e) { toast('删除失败：' + e.message); }
}

async function loadNovels() {
  const box = $('#novel-list');
  if (window.__STATIC__ && !window.__API_BASE__) {
    box.innerHTML = '<div class="empty">静态部署版不支持小说功能，请使用本地版</div>';
    return;
  }
  box.innerHTML = '<div class="skeleton" style="height:44px"></div>';
  try {
    const d = await api('/api/novel/list');
    if (!d.novels.length) { box.innerHTML = '<div class="empty">书架为空</div>'; return; }
    box.innerHTML = d.novels.map(n => {
      const name = n.file.replace(/\.txt$/, '');
      return `<div class="vitem" data-novel="${esc(name)}">
        <div style="flex:1;min-width:0">
          <div class="vname">${esc(name)}</div>
          <div class="vmeta muted small">${(n.size / 1e4).toFixed(0)}万字节 · ${n.analyzed ? '已标注' : '未标注'}</div>
        </div>
        <button class="btn small danger del-novel" data-del-novel="${esc(name)}" title="删除该书及所有分析/音频/分镜缓存">🗑</button>
      </div>`;
    }).join('');
  } catch (e) { box.innerHTML = '<div class="empty">加载失败：' + esc(e.message) + '</div>'; }
}

async function openNovel(name) {
  NOVEL.current = name;
  $('#novel-empty').classList.add('hidden');
  $('#novel-detail').classList.remove('hidden');
  $('#novel-result-panel').classList.add('hidden');
  try {
    const c = await api(`/api/novel/${encodeURIComponent(name)}/chapters`);
    NOVEL.chapters = c.chapters || [];
    NOVEL.sbMap = {};
    $('#novel-chapters').innerHTML = renderChapterGroups(c.chapters);
    loadStoryboardIndex();   // 异步标注哪些章已生成分镜
  } catch (e) { $('#novel-chapters').innerHTML = '<p class="muted">目录加载失败</p>'; }
}

let READER = { idx: 0, fontSize: 18, font: 'serif', line: '1.95', theme: 'default', tocOpen: false, autoNext: false, loadingNext: false };

function loadReaderPrefs() {
  try {
    const s = JSON.parse(localStorage.getItem('lychee_reader') || '{}');
    if (s.fontSize) READER.fontSize = s.fontSize;
    if (s.font) READER.font = s.font;
    if (s.line) READER.line = s.line;
    if (s.theme) READER.theme = s.theme;
  } catch {}
}
function saveReaderPrefs() {
  localStorage.setItem('lychee_reader', JSON.stringify({
    fontSize: READER.fontSize, font: READER.font, line: READER.line, theme: READER.theme
  }));
}

async function readChapter(idx) {
  if (!NOVEL.current) return;
  READER.idx = idx;
  const modal = $('#chapter-modal');
  const title = $('#chapter-modal-title');
  const body = $('#chapter-modal-body');
  body.innerHTML = '<div class="reader-content"><div class="skeleton" style="height:160px"></div></div>';
  if ($('#chapter-modal').classList.contains('hidden')) document.body.style.overflow = 'hidden';
  modal.classList.remove('hidden');
  renderReaderProgress();
  try {
    const d = await api(`/api/novel/${encodeURIComponent(NOVEL.current)}/chapters/${idx}`);
    READER.idx = d.index ?? idx;
    title.textContent = `第${d.index}章 · ${esc(d.title)}`;
    const paras = (d.text || '（本章无正文）').split(/\n+/).filter(Boolean);
    body.innerHTML = '<div class="reader-content" id="reader-content">' + paras.map(p => '<p>' + esc(p) + '</p>').join('') + '</div>';
    applyReaderStyle();
    renderReaderProgress();
    renderReaderToc();
    rememberReaderPos();
  } catch (e) {
    body.innerHTML = `<div class="reader-content"><p class="muted">读取失败：${esc(e.message)}</p></div>`;
  }
}

function closeChapterModal() {
  $('#chapter-modal').classList.add('hidden');
  $('#reader-toc').classList.add('hidden');
  $('#reader-settings').classList.add('hidden');
  document.body.style.overflow = '';
}

function readerPrev() {
  if (!NOVEL.chapters || !NOVEL.chapters.length) return;
  const list = NOVEL.chapters.map(c => c.index ?? c.number ?? 0);
  const pos = list.indexOf(READER.idx);
  if (pos > 0) readChapter(list[pos - 1]);
}

function readerNext() {
  if (!NOVEL.chapters || !NOVEL.chapters.length) return;
  const list = NOVEL.chapters.map(c => c.index ?? c.number ?? 0);
  const pos = list.indexOf(READER.idx);
  if (pos >= 0 && pos < list.length - 1) readChapter(list[pos + 1]);
}

function renderReaderProgress() {
  if (!NOVEL.chapters || !NOVEL.chapters.length) return;
  const list = NOVEL.chapters.map(c => c.index ?? c.number ?? 0);
  const pos = list.indexOf(READER.idx);
  const pct = pos >= 0 ? ((pos + 1) / list.length * 100).toFixed(1) : 0;
  $('#reader-progress-fill').style.width = pct + '%';
  $('#reader-progress-text').textContent = pos >= 0 ? `${pos + 1}/${list.length} · ${pct}%` : '—';
  $('#reader-prev').disabled = pos <= 0;
  $('#reader-next').disabled = pos < 0 || pos >= list.length - 1;
}

function renderReaderToc() {
  const box = $('#reader-toc-list');
  const title = $('#reader-toc-title');
  if (!NOVEL.chapters) { box.innerHTML = ''; return; }
  title.textContent = NOVEL.current ? `《${NOVEL.current}》目录` : '目录';
  const q = ($('#reader-toc-search').value || '').trim().toLowerCase();
  const items = NOVEL.chapters.filter(c => !q || (c.title || '').toLowerCase().includes(q));
  if (!items.length) { box.innerHTML = '<p class="muted" style="padding:12px">无匹配章节</p>'; return; }
  box.innerHTML = items.map(c => {
    const idx = c.index ?? c.number ?? 0;
    const active = idx === READER.idx ? 'active' : '';
    return `<div class="reader-toc-item ${active}" data-toc-idx="${idx}">
      <span class="reader-toc-num">第${idx}章</span>
      <span class="reader-toc-name">${esc(c.title || '')}</span>
    </div>`;
  }).join('');
}

function toggleReaderToc() {
  const toc = $('#reader-toc');
  READER.tocOpen = !READER.tocOpen;
  toc.classList.toggle('hidden', !READER.tocOpen);
}

function applyReaderStyle() {
  const rc = $('#reader-content');
  if (!rc) return;
  rc.style.fontSize = READER.fontSize + 'px';
  rc.style.lineHeight = READER.line;
  rc.style.fontFamily = READER.font === 'serif'
    ? '"Songti SC","SimSun",Georgia,"Times New Roman",serif'
    : '"PingFang SC","Microsoft YaHei",-apple-system,sans-serif';
  const root = $('#reader-card');
  root.dataset.readerTheme = READER.theme;
  // 同步设置面板分段高亮
  $$('#rs-font button').forEach(b => b.classList.toggle('active', b.dataset.v === READER.font));
  $$('#rs-line button').forEach(b => b.classList.toggle('active', b.dataset.v === READER.line));
  $$('#rs-theme button').forEach(b => b.classList.toggle('active', b.dataset.v === READER.theme));
}

function readerFontSize(delta) {
  READER.fontSize = Math.max(14, Math.min(28, READER.fontSize + delta));
  applyReaderStyle();
  saveReaderPrefs();
}

function toggleReaderSettings() {
  $('#reader-settings').classList.toggle('hidden');
}

function setReaderSetting(group, value, segEl) {
  if (group === 'font') READER.font = value;
  if (group === 'line') READER.line = value;
  if (group === 'theme') READER.theme = value;
  $$('#' + segEl + ' button').forEach(b => b.classList.toggle('active', b.dataset.v === value));
  applyReaderStyle();
  saveReaderPrefs();
}

function rememberReaderPos() {
  if (!NOVEL.current) return;
  try { localStorage.setItem('lychee_reader_pos_' + NOVEL.current, String(READER.idx)); } catch {}
}
function resumeReaderPos() {
  if (!NOVEL.current) return null;
  try { return localStorage.getItem('lychee_reader_pos_' + NOVEL.current); } catch { return null; }
}

function onReaderScroll() {
  const body = $('#chapter-modal-body');
  if (!body) return;
  const scrolled = body.scrollTop / Math.max(1, body.scrollHeight - body.clientHeight);
  $('#reader-scroll-bar').style.width = (scrolled * 100).toFixed(1) + '%';
  // 自动续读：滚到底部且还有下一章
  if (scrolled > 0.92 && !READER.loadingNext) {
    const list = (NOVEL.chapters || []).map(c => c.index ?? c.number ?? 0);
    const pos = list.indexOf(READER.idx);
    if (pos >= 0 && pos < list.length - 1) {
      READER.loadingNext = true;
      readerNext();
      setTimeout(() => { READER.loadingNext = false; }, 800);
    }
  }
}

// 渲染章节列表：每章独立提供“分镜”按钮，避免 5 章一批太慢。
function renderChapterGroups(chapters) {
  if (!chapters.length) return '<p class="muted">暂无章节</p>';
  const groupSize = 5;
  const groups = [];
  for (let i = 0; i < chapters.length; i += groupSize) {
    groups.push(chapters.slice(i, i + groupSize));
  }
  const toolbar = `<div class="chapter-toolbar">
    <span class="muted small">分镜前可先建全本角色库，保证几百个角色跨章形象一致</span>
    <button class="btn small ghost" id="btn-build-bible">🧬 建全本角色库</button>
  </div>`;
  return toolbar + groups.map((grp) => {
    const start = grp[0].index;
    const end = grp[grp.length - 1].index;
    const rangeLabel = start === end ? `第${start}章` : `第${start}-${end}章`;
    return `<div class="chapter-group">
      <div class="chapter-group-head">
        <span class="muted small">${rangeLabel}</span>
      </div>
      ${grp.map(ch => {
        const done = !!(NOVEL.sbMap || {})[String(ch.index)];
        return `
        <div class="chapter-row" data-chapter="${ch.index}"><span class="ch-read-ico" title="点击阅读">📖</span>
          <span class="muted small" style="min-width:34px">${ch.index}</span>
          <span style="flex:1">${esc(ch.title)}</span>
          ${done ? '<span class="sb-done" title="分镜已生成，点击🎬秒开">已生成</span>' : ''}
          <span class="muted small">${(ch.n_chars / 1000).toFixed(1)}k字</span>
          <div style="display:flex;gap:6px;margin-left:8px">
            <button class="btn small" data-storyboard-chapter="${ch.index}" title="${done ? '查看分镜' : '生成分镜'}">🎬</button>
          </div>
        </div>`; }).join('')}
    </div>`;
  }).join('');
}

// 读取已生成的分镜缓存；没有返回 null（不抛错，交调用方决定是否生成）
async function _fetchStoryboard(rng) {
  try {
    return await api(`/api/novel/${encodeURIComponent(NOVEL.current)}/storyboard/${rng}`);
  } catch (_) { return null; }
}

// 统一渲染分镜结果。cached=true 时额外给出「重新生成」入口。
function _renderSbResult(sb, rng, cached, regen) {
  const bible = sb.characters_bible || {};
  const cont = sb.continuity || {};
  const scenes = (sb.scenes && sb.scenes.length) ? sb.scenes : null;

  let allText, htmlBody, totalSecs, nScenes;
  if (scenes) {
    const r = _renderDirectorScriptScenes(scenes, sb.chapter_title, bible, sb.director_note, cont, true);
    allText = r.text; htmlBody = r.html; nScenes = scenes.length;
    totalSecs = Math.round(scenes.reduce((a, sc) => a + (sc.shots || []).reduce((b, s) => {
      const d = s.spec && s.spec.duration;
      const m = (typeof d === 'string') ? parseFloat(d) : (typeof d === 'number' ? d : NaN);
      return b + (isFinite(m) ? m : 3.5);
    }, 0), 0));
  } else {
    const segments = buildSegments(sb.shots, bible);
    allText = _renderDirectorScript(segments, sb.chapter_title, bible, cont, false);
    htmlBody = _renderDirectorScript(segments, sb.chapter_title, bible, cont, true);
    nScenes = 0;
    totalSecs = Math.round(segments.reduce((a, s) => a + (s.dur || 0), 0));
  }

  $('#novel-sb-box').innerHTML =
    (cont.summary ? _renderContinuityBanner(cont) : '') +
    `<div class="sb-bar"><span class="muted small">${esc(sb.chapter_title || '')} · ` +
      (scenes ? `${nScenes} 场 · ` : '') + `${(sb.shots || []).length} 镜${cached ? ' · 已有缓存' : ''}</span>
      <span style="display:flex;gap:6px">
        <button class="btn small" id="copy-all-shots">复制整章</button>
        <button class="btn small ghost" id="btn-consistency-check" title="跑描述锁+风格锁，抓出飘了的镜头">🔍 一致性校验</button>
        ${cached ? '<button class="btn small ghost" id="regen-shots">重新生成</button>' : ''}
      </span></div>` + htmlBody;
  const cab = $('#copy-all-shots');
  if (cab) cab.onclick = () => { navigator.clipboard.writeText(allText); toast('✓ 已复制整章剧本'); };
  const ckb = $('#btn-consistency-check');
  if (ckb) ckb.onclick = () => novelConsistencyCheck(rng);
  const rb = $('#regen-shots');
  if (rb && regen) rb.onclick = () => { if (confirm('重新生成会覆盖当前结果，本地模型约几分钟。确定？')) regen(); };
}

// 一致性守门：调后端 /api/novel/consistency/check，把报告渲染到 #novel-consistency-box
async function novelConsistencyCheck(rng) {
  if (window.__STATIC__ && !window.__API_BASE__) { staticNovelHint(); return; }
  if (!NOVEL.current) return;
  const box = $('#novel-consistency-box');
  if (!box) return;
  box.classList.remove('hidden');
  box.innerHTML = '<div class="skeleton" style="height:36px"></div><p class="muted small" style="margin-top:8px">正在跑一致性守门（描述锁 + 风格锁）…</p>';
  try {
    const d = await apiPost('/api/novel/consistency/check', { name: NOVEL.current, rng: String(rng) });
    box.innerHTML = renderConsistencyReport(d.report || {});
  } catch (e) {
    box.innerHTML = '<p class="muted">校验失败：' + esc(e.message) + '</p>';
  }
}

function renderConsistencyReport(r) {
  const pass = r.pass;
  const desc = r.descriptive || {};
  const style = r.style_lock || {};
  const issues = (desc.issues_per_shot || []).filter(x => x.issues && x.issues.length);
  let h = '';
  h += '<div class="cons-head ' + (pass ? 'ok' : 'bad') + '">';
  h += '<div class="cons-score">' + (r.overall_score ?? '—') + '<span class="cons-score-unit">分</span></div>';
  h += '<div class="cons-gate">' + esc(r.gate || (pass ? '达标' : '不达标')) + '</div>';
  h += '</div>';
  h += '<div class="cons-metrics">';
  h += consMetric('描述锁', desc.score, desc.total_issues, '本镜角色是否仍锚在角色库');
  h += consMetric('风格锁', style.score, style.total_issues, '是否仍带国漫/赛璐璐尾巴');
  h += '</div>';
  if (issues.length) {
    h += '<div class="cons-issues"><h4 class="cons-issues-title">⚠ 漂移镜头（' + issues.length + '）</h4>';
    issues.forEach(sp => {
      h += '<div class="cons-issue"><b>镜头 ' + sp.shot_id + '</b>';
      sp.issues.forEach(it => {
        h += '<div class="cons-issue-row">· ' + esc(it.character) + ' 覆盖度 ' + Math.round((it.coverage || 0) * 100) + '%'
          + '<div class="muted small">角色库：' + esc((it.bible_desc || '').slice(0, 50)) + '</div>'
          + '<div class="muted small">本镜：' + esc((it.shot_desc || '').slice(0, 50)) + '</div></div>';
      });
      h += '</div>';
    });
    h += '</div>';
  } else {
    h += '<p class="cons-ok-note">✓ 所有出场角色均锁定在角色库上，无漂移。</p>';
  }
  if (style.drifted_shots && style.drifted_shots.length) {
    h += '<p class="muted small" style="margin-top:6px">⚠ 风格漂移镜头：' + style.drifted_shots.join('、') + '</p>';
  }
  h += '<p class="muted small" style="margin-top:8px">角色卡（投喂视频模型角色参考的锚）已冻结 ' + Object.keys(r.cast || {}).length + ' 个，见 /api/novel/{name}/cast</p>';
  return h;
}

function consMetric(label, score, issues, hint) {
  const s = (score == null) ? '—' : score;
  const cls = (score == null) ? '' : score >= 90 ? 'ok' : score >= 70 ? 'warn' : 'bad';
  const sub = (issues ? (issues + ' 处问题') : '无问题');
  return '<div class="cons-metric ' + cls + '" title="' + esc(hint || '') + '">'
    + '<span class="cm-label">' + esc(label) + '</span>'
    + '<span class="cm-score">' + s + '</span>'
    + '<span class="cm-sub">' + esc(sub) + '</span></div>';
}

// 按「场」渲染导演剧本：CAST + 导演阐述 + 每场（时间/地点/事件）+ 逐镜，全部收进一个框。
function _renderDirectorScriptScenes(scenes, chapterTitle, bible, directorNote, continuity, html) {
  const cast = _bibleText(bible);
  let text = '';
  if (directorNote) text += '══════ 🎬 导演阐述 ══════\n' + directorNote + '\n\n';
  scenes.forEach((sc) => {
    const head = `场 ${sc.scene_id}｜${sc.heading}`;
    let t = '══════ ' + head + ' ══════\n';
    t += '\n' + (sc.shots || []).map(s => '── 镜头' + s.shot_id + ' ──\n' + shotPrompt(s)).join('\n\n');
    text += t + '\n\n';
  });

  if (!html) return { text: text, html: '' };

  const renderCn = (cn) => {
    return String(cn).split('\n').filter(Boolean).map(line => {
      const ci = line.indexOf('：');
      if (ci > 0 && ci < 12) {
        return '<div class="ds-cn-row"><span class="ds-cn-k">' + esc(line.slice(0, ci + 1)) + '</span>'
          + '<span class="ds-cn-v">' + esc(line.slice(ci + 1)) + '</span></div>';
      }
      return '<div class="ds-cn-row"><span class="ds-cn-v">' + esc(line) + '</span></div>';
    }).join('');
  };

  const shotCompact = (s) => {
    const p = shotPrompt(s);
    const cn = s.video_prompt_cn || p;
    const spec = s.spec || {};
    const specText = (s.shot_type && spec.duration && spec.aspect)
      ? `${s.shot_type} · ${spec.duration} · ${spec.aspect} · 国漫`
      : (s.shot_type || '');
    return '<div class="ds-shot">'
      + '<div class="ds-shot-bar">'
      + '<span class="ds-shot-id">镜头 ' + (s.shot_id || '?') + '</span>'
      + (specText ? '<span class="ds-shot-type">' + esc(specText) + '</span>' : '')
      + '<button class="copy-mini" data-copy="' + attr(cn) + '">复制提示词</button>'
      + '</div>'
      + '<div class="ds-shot-cn">' + renderCn(cn) + '</div>'
      + (s.dialogue
          ? '<div class="ds-shot-line"><span class="ds-line-k">台词</span>'
            + String(s.dialogue).split('\n').filter(Boolean)
                .map(l => '<span class="ds-line-v">' + esc(l) + '</span>').join('')
            + '</div>'
          : '')
      + '</div>';
  };

  const totalShots = scenes.reduce((a, sc) => a + (sc.shots || []).length, 0);
  const blocks = [];
  blocks.push('<div class="ds-head"><span class="ds-title">' + esc(chapterTitle || '本章分镜剧本') + '</span>'
    + '<span class="ds-meta">' + scenes.length + ' 场 · ' + totalShots + ' 镜</span></div>');

  if (cast) {
    blocks.push('<section class="ds-block cast-block">'
      + '<h4 class="ds-label">CAST · 人物形象设定（跨镜头统一）</h4>'
      + '<pre class="ds-cast">' + esc(cast) + '</pre></section>');
  }
  if (directorNote) {
    blocks.push('<section class="ds-block note-block">'
      + '<h4 class="ds-label">🎬 导演阐述</h4>'
      + '<p class="ds-note">' + esc(directorNote) + '</p></section>');
  }
  scenes.forEach((sc) => {
    let sec = '<section class="ds-block scene-block">'
      + '<div class="ds-scene-head">'
      + '<span class="ds-badge">场 ' + sc.scene_id + '</span>'
      + '<span class="ds-scene-title">' + esc(sc.heading) + '</span>'
      + '</div>';
    sec += (sc.shots || []).map(shotCompact).join('') + '</section>';
    blocks.push(sec);
  });

  const htmlOut = '<div class="script-box director-script">' + blocks.join('') + '</div>';
  return { text: text, html: htmlOut };
}

async function novelStoryboardChapter(idx, force) {
  if (window.__STATIC__ && !window.__API_BASE__) { staticNovelHint(); return; }
  if (!NOVEL.current) return;
  $('#novel-result-panel').classList.remove('hidden');
  _setResultTab('sb');
  $('#novel-sb-box').innerHTML = '<div class="skeleton" style="height:60px"></div>';

  // 先读缓存：已生成过的章节秒开，不再重复跑 LLM
  if (!force) {
    const hit = (NOVEL.sbMap || {})[String(idx)];
    const sb = await _fetchStoryboard(idx) || (hit && hit !== String(idx) ? await _fetchStoryboard(hit) : null);
    if (sb && (sb.shots || []).length) {
      _renderSbResult(sb, idx, true, () => novelStoryboardChapter(idx, true));
      toast('✓ 已读取缓存分镜');
      return;
    }
  }

  toast(`第${idx}章：本地 LLM 生成导演级分镜…`);
  try {
    const d = await apiPost('/api/novel/storyboard', { name: NOVEL.current, chapter: idx });
    pollNovelTask(d.task_id, async () => {
      const sb = await _fetchStoryboard(idx);
      if (!sb) { $('#novel-sb-box').innerHTML = '<p class="muted">生成完成但读取失败，请刷新重试</p>'; return; }
      _renderSbResult(sb, idx, false);
      loadStoryboardIndex();
      toast('✓ 分镜已生成');
    }, (err) => { $('#novel-sb-box').innerHTML = `<p class="muted">生成失败：${esc(String(err).slice(0, 200))}</p>`; },
    (t) => { const pt = t && t.progress && t.progress.text; if (pt) $('#novel-sb-box').innerHTML = '<p class="muted">⏳ ' + esc(pt) + '</p>'; });
  } catch (e) { toast('失败：' + e.message); }
}

// 拉取该书已生成的分镜清单，用于章节列表标注「已生成」并直读缓存
async function loadStoryboardIndex() {
  if (!NOVEL.current || (window.__STATIC__ && !window.__API_BASE__)) return;
  try {
    const d = await api(`/api/novel/${encodeURIComponent(NOVEL.current)}/storyboards`);
    NOVEL.sbMap = (d && d.chapters) || {};
  } catch (_) { NOVEL.sbMap = {}; }
  if (NOVEL.chapters && $('#novel-chapters')) {
    $('#novel-chapters').innerHTML = renderChapterGroups(NOVEL.chapters);
  }
}

async function buildFullBible() {
  if (window.__STATIC__ && !window.__API_BASE__) { staticNovelHint(); return; }
  if (!NOVEL.current) return;
  if (!confirm(`将后台构建《${NOVEL.current}》全本角色库（可能十几分钟，期间分镜会变慢）。跑完自动缓存，之后每章分镜都会复用。确定？`)) return;
  try {
    const d = await apiPost('/api/novel/build-bible', { name: NOVEL.current, max_chapters: 200 });
    toast('⏳ 全本角色库后台构建中，跑完自动缓存');
    pollNovelTask(d.task_id, () => toast('✓ 全本角色库已建好，分镜时将自动复用'),
      (e) => toast('失败：' + e.message));
  } catch (e) { toast('失败：' + e.message); }
}

async function novelStoryboardBatch(rangeStr, force) {
  if (window.__STATIC__ && !window.__API_BASE__) { staticNovelHint(); return; }
  if (!NOVEL.current) return;
  const [start, end] = rangeStr.split('-').map(Number);
  $('#novel-result-panel').classList.remove('hidden');
  _setResultTab('sb');
  $('#novel-sb-box').innerHTML = '<div class="skeleton" style="height:60px"></div>';

  if (!force) {
    const sb = await _fetchStoryboard(rangeStr);
    if (sb && (sb.shots || []).length) {
      _renderSbResult(sb, rangeStr, true, () => novelStoryboardBatch(rangeStr, true));
      toast('✓ 已读取缓存分镜');
      return;
    }
  }

  toast(`第${start}-${end}章：本地 LLM 生成导演级分镜…`);
  try {
    const d = await apiPost('/api/novel/storyboard/batch', { name: NOVEL.current, start, end });
    pollNovelTask(d.task_id, async () => {
      const sb = await _fetchStoryboard(rangeStr);
      if (!sb) { $('#novel-sb-box').innerHTML = '<p class="muted">生成完成但读取失败，请刷新重试</p>'; return; }
      _renderSbResult(sb, rangeStr, false);
      loadStoryboardIndex();
      toast('✓ 分镜已生成');
    }, (err) => { $('#novel-sb-box').innerHTML = `<p class="muted">生成失败：${esc(String(err).slice(0, 200))}</p>`; },
    (t) => { const pt = t && t.progress && t.progress.text; if (pt) $('#novel-sb-box').innerHTML = '<p class="muted">⏳ ' + esc(pt) + '</p>'; });
  } catch (e) { toast('失败：' + e.message); }
}

function _setResultTab(tab) {
  const sb = $('#novel-sb-box'), au = $('#novel-audio-box'), vc = $('#novel-voice-catalog');
  document.querySelectorAll('#novel-result-panel .tab-btn').forEach(b => b.classList.toggle('active', b.dataset.rtab === tab));
  if (sb) sb.classList.toggle('hidden', tab !== 'sb');
  if (au) au.classList.toggle('hidden', tab !== 'audio');
  if (vc) vc.classList.toggle('hidden', tab !== 'audio');
}
document.addEventListener('click', e => {
  const bb = e.target.closest('#btn-build-bible');
  if (bb) { e.preventDefault(); buildFullBible(); return; }
  const rt = e.target.closest('[data-rtab]'); if (rt) _setResultTab(rt.dataset.rtab);
});

function _dedup(arr) {
  const seen = new Set(); const out = [];
  for (const x of arr) { if (!x) continue; if (seen.has(x)) continue; seen.add(x); out.push(x); }
  return out;
}

function buildSegments(shots, bible) {
  bible = bible || {};
  const groups = {}; const order = [];
  shots.forEach(s => {
    const key = (s.scene || '').trim() || ('场景段' + (order.length + 1));
    if (!groups[key]) { groups[key] = []; order.push(key); }
    groups[key].push(s);
  });
  return order.map((key, i) => {
    const ss = groups[key];
    const chars = [...new Set(ss.flatMap(s => (s.characters || '').split(/[,，、\s]+/).filter(Boolean)))];
    const modeling = chars.map(c => { const b = bible[c]; const d = b ? (typeof b === 'string' ? b : (b.zh || b.desc || '')) : ''; return d ? c + '：' + d : c; }).join('\n');
    const plots = _dedup(ss.map(s => s.plot)).filter(Boolean).join('；');
    const vfx = _dedup(ss.map(s => s.vfx)).filter(Boolean).join('；');
    const arrangement = _dedup(ss.map(s => s.arrangement || s.scene)).filter(Boolean).join('；');
    const cameras = ss.map(s => s.camera).filter(Boolean).join('，');
    const dialogues = _dedup(ss.map(s => s.dialogue)).filter(Boolean).join('\n');
    const bridges = ss.map(s => s.bridge).filter(Boolean).join('；');
    // spec.duration 形如 "3.5秒"（字符串），需用 parseFloat 提取数值再累加，否则 JS 会做字符串拼接 → NaN
    const dur = ss.reduce((a, s) => {
      const d = s.spec && s.spec.duration;
      const m = (typeof d === 'string') ? parseFloat(d) : (typeof d === 'number' ? d : NaN);
      return a + (isFinite(m) ? m : 3.5);
    }, 0);
    const prompt = [
      '══════ 场景段 ' + (i + 1) + '｜' + key + ' ══════',
      modeling ? '【出场角色】\n' + modeling : '',
      plots ? '【导演意图】\n' + plots : '',
      arrangement ? '【场景设置】\n' + arrangement : '',
      vfx ? '【特效技术】\n' + vfx : '',
      cameras ? '【镜头运动】\n' + cameras : '',
      dialogues ? '【台词】\n' + dialogues : '',
      bridges ? '【衔接】\n' + bridges : '',
      '【规格】时长约 ' + Math.round(dur) + ' 秒 · 帧率 24fps · 清晰度 1080p',
      '【风格】国漫，统一人物形象，连续叙事'
    ].filter(Boolean).join('\n\n');
    return { seg_id: i + 1, scene: key, prompt: prompt, n_shots: ss.length, dur: dur };
  });
}

function renderSegment(seg) {
  return '<div class="segment-card"><div class="seg-head"><span class="seg-id">场景段' + seg.seg_id + '</span><span class="seg-scene">' + esc(seg.scene) + '</span><span class="muted small">' + seg.n_shots + '镜合并</span><button class="copy-mini" data-copy="' + attr(seg.prompt) + '">复制</button></div><div class="prompt-main" data-copy="' + attr(seg.prompt) + '">' + esc(seg.prompt) + '</div></div>';
}

function attr(s) { return esc(s == null ? '' : String(s)).replace(/"/g, '&quot;'); }

function shotPrompt(s) {
  if (s.video_prompt_cn) {
    const spec = s.spec || {};
    const head = (s.shot_type && spec.duration && spec.aspect)
      ? `【${s.shot_type} · ${spec.duration} · ${spec.aspect} · 国漫】\n`
      : '';
    return head + s.video_prompt_cn;
  }
  if (s.prompt) return s.prompt;
  const p = [];
  if (s.shot_type) p.push(s.shot_type);
  if (s.action) p.push(s.action);
  if (s.scene) p.push(s.scene);
  if (s.lighting_tech) p.push('光影：' + s.lighting_tech);
  if (s.vfx) p.push('特效：' + s.vfx);
  if (s.camera) p.push('运镜：' + s.camera);
  return p.join('；');
}

function _bibleText(bible) {
  if (!bible || !Object.keys(bible).length) return '';
  return Object.entries(bible).map(([n,d]) => {
    const desc = (typeof d === 'string') ? d : (d.zh || d.desc || '');
    return n + '：' + desc;
  }).join('\n');
}

function _renderDirectorScript(segments, chapterTitle, bible, continuity, html) {
  // 生成导演级分镜剧本文本；html=true 返回 DOM，false 返回纯文本（供整章复制）。
  const totalDur = segments.reduce((a, s) => a + (s.dur || 0), 0);
  const cast = _bibleText(bible);
  const headerLines = [
    '╔══════════════════════════════════════╗',
    '║  ' + (chapterTitle || '本章分镜剧本'),
    '║  共 ' + segments.length + ' 个场景段 · 合计约 ' + Math.round(totalDur) + ' 秒',
    '║  风格：国漫 · 统一人物形象 · 连续叙事'
  ];
  if (continuity && continuity.summary) {
    headerLines.push('║  前文衔接：' + continuity.summary);
  }
  headerLines.push('╚══════════════════════════════════════╝');
  const header = headerLines.join('\n');

  const castBlock = cast ? '══════ CAST｜人物形象设定（跨镜头统一）══════\n\n' + cast : '';

  const body = segments.map((seg, i) => {
    const next = segments[i + 1];
    const trans = next ? '\n\n➤ 转场至场景段 ' + next.seg_id + '｜' + next.scene + ' — 保持角色形象与光线基调一致' : '';
    return seg.prompt + trans;
  }).join('\n\n');

  const fullText = [header, castBlock, body].filter(Boolean).join('\n\n');
  if (!html) return fullText;

  // 渲染为 DOM：CAST + 各场景段 + 段间转场，每段都可单独复制。
  let htmlOut = '<div class="script-box"><pre class="script-header">' + esc(header) + '</pre>';
  if (cast) {
    htmlOut += '<div class="script-segment script-cast">' +
      '<div class="script-seg-head"><span>══════ CAST｜人物形象设定（跨镜头统一）══════</span></div>' +
      '<pre class="script-body">' + esc(cast) + '</pre></div>';
  }
  segments.forEach((seg, i) => {
    const next = segments[i + 1];
    htmlOut += '<div class="script-segment">' +
      '<div class="script-seg-head"><span>══════ 场景段 ' + seg.seg_id + '｜' + esc(seg.scene) + ' ══════</span>' +
      '<button class="btn small copy-mini" data-copy="' + attr(seg.prompt) + '">复制本段</button></div>' +
      '<pre class="script-body">' + esc(seg.prompt) + '</pre></div>';
    if (next) {
      htmlOut += '<div class="script-transition">➤ 转场至场景段 ' + next.seg_id + '｜' + esc(next.scene) + ' — 保持角色形象与光线基调一致</div>';
    }
  });
  htmlOut += '</div>';
  return htmlOut;
}

function _renderContinuityBanner(cont) {
  if (!cont || !cont.summary) return '';
  return `<div class="cont-banner">📎 已衔接前文（上一批结尾）：${esc(cont.summary)}</div>`;
}

function renderShot(s) {
  const p = shotPrompt(s);
  const spec = s.spec || {};
  const specItems = [
    spec.duration ? `<div class="spec-item"><span>时长</span><b>${esc(spec.duration)}s</b></div>` : '',
    spec.aspect ? `<div class="spec-item"><span>画幅</span><b>${esc(spec.aspect)}</b></div>` : '',
    spec.fps ? `<div class="spec-item"><span>帧率</span><b>${esc(spec.fps)}</b></div>` : '',
    spec.composition ? `<div class="spec-item"><span>构图</span><b>${esc(spec.composition)}</b></div>` : '',
    spec.transition ? `<div class="spec-item"><span>转场</span><b>${esc(spec.transition)}</b></div>` : '',
  ].join('');
  return `<div class="shot-card">
    <div class="shot-head">
      <span class="shot-id">镜头${s.shot_id}</span>
      <span class="tag">${esc(s.shot_type || '—')}</span>
      <span class="tag">${esc(s.camera || '—')}</span>
      <button class="copy-mini" data-copy="${attr(s.video_prompt_cn || p)}">复制</button>
    </div>
    <div class="prompt-main" data-copy="${attr(p)}">${esc(p)}</div>
    ${s.prompt_en ? `<div class="prompt-en">${esc(s.prompt_en)} <button class="copy-mini" data-copy="${attr(s.prompt_en)}">复制英文</button></div>` : ''}
    ${s.dialogue ? `<div class="dialogue-block"><b>台词：</b>${esc(s.dialogue)}</div>` : ''}
    <details class="shot-detail"><summary>拆解</summary>
      ${specItems ? `<div class="spec-grid">${specItems}</div>` : ''}
      ${s.bridge ? `<div class="shot-row"><b>镜头衔接：</b>${esc(s.bridge)}</div>` : ''}
      ${s.color_script ? `<div class="shot-row"><b>色彩：</b>${esc(s.color_script)}</div>` : ''}
      ${s.audio ? `<div class="shot-row"><b>声音：</b>${esc(s.audio)}</div>` : ''}
      ${s.emotion ? `<div class="shot-row"><b>情绪：</b>${esc(s.emotion)}</div>` : ''}
      ${s.plot ? `<div class="shot-row"><b>剧情：</b>${esc(s.plot)}</div>` : ''}
      ${s.action ? `<div class="shot-row"><b>动作：</b>${esc(s.action)}</div>` : ''}
      ${s.scene ? `<div class="shot-row"><b>场景：</b>${esc(s.scene)}</div>` : ''}
      ${s.narration ? `<div class="shot-row"><b>旁白：</b>${esc(s.narration)}</div>` : ''}
    </details>
  </div>`;
}

function pollNovelTask(taskId, onDone, onError, onPending, _miss) {
  _miss = _miss || 0;
  fetch(API_BASE + '/api/tasks/' + taskId, { headers: { 'X-API-Key': API_KEY } })
    .then(r => r.json().then(t => ({ ok: r.ok, code: r.status, t })))
    .then(({ ok, code, t }) => {
      // 任务不存在（后端重启会清空内存任务表）：别无限轮询，直接报错让用户重来
      if (code === 404 || (!ok && !t.status)) {
        if (++_miss >= 2) {
          const msg = '任务已中断，请重新生成';
          return onError ? onError(msg) : toast(msg);
        }
        return setTimeout(() => pollNovelTask(taskId, onDone, onError, onPending, _miss), 2000);
      }
      if (t.status === 'done') onDone && onDone(t);
      else if (t.status === 'error') (onError ? onError(t.error || '未知错误') : toast('任务失败'));
      else if (!t.status) {
        const msg = '任务状态异常，请重新生成';
        return onError ? onError(msg) : toast(msg);
      }
      else { if (onPending) onPending(t); setTimeout(() => pollNovelTask(taskId, onDone, onError, onPending, 0), 3000); }
    }).catch(() => {
      // 网络错误容忍若干次，连不上就报错，不再静默转圈
      if (++_miss >= 10) {
        const msg = '连不上后端，请确认后端在运行后重新生成';
        return onError ? onError(msg) : toast(msg);
      }
      setTimeout(() => pollNovelTask(taskId, onDone, onError, onPending, _miss), 5000);
    });
}

