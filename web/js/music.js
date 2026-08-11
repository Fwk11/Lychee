/* lychee 前端 · music.js
 * 音乐模块：新歌榜、Hero KPI、真·谱子画像、榜单筛选、平台图标、进阶实验室
 */

let MUSIC_CACHE = null;
let MUSIC_UID_LOADED = null;
const MUSIC_UID_KEY = 'lychee_music_uid';
const MUSIC_NAME_KEY = 'lychee_music_name';

// 多用户：每个浏览器本地保存一个身份，作为独立 user_id。
// 默认 'default' = 站长示例数据；访客上传卡片后切换为各自昵称对应的 user_id。
function getCurrentUser() { return localStorage.getItem(MUSIC_UID_KEY) || 'default'; }
function userQS() {
  const u = getCurrentUser();
  return (u && u !== 'default') ? 'user_id=' + encodeURIComponent(u) : '';
}
function currentName() {
  const n = localStorage.getItem(MUSIC_NAME_KEY);
  const u = getCurrentUser();
  return n || (u === 'default' ? '示例(站长)' : u);
}
function sanitizeUid(s) {
  let u = (s || '').trim().replace(/[^\w一-龥-]/g, '_').slice(0, 60);
  if (!u) u = 'guest-' + Math.random().toString(36).slice(2, 8);
  return u;
}

async function loadMusic() {
  setupMusicOnboard();
  renderOnboardState();
  await loadNewReleases();
}


/* ---------- 上传音乐卡片（多用户入职） ---------- */
function setupMusicOnboard() {
  const dz = $('#qr-dropzone');
  const input = $('#qr-file');
  const btn = $('#qr-submit');
  if (!dz || !input) return;

  dz.addEventListener('click', (e) => {
    if (e.target === btn) return;          // 按钮单独处理
    input.click();
  });
  btn.addEventListener('click', (e) => { e.stopPropagation(); input.click(); });
  input.addEventListener('change', () => {
    const f = input.files && input.files[0];
    if (f) doOnboard(f);
  });
  dz.addEventListener('dragover', (e) => { e.preventDefault(); dz.classList.add('drag'); });
  dz.addEventListener('dragleave', () => dz.classList.remove('drag'));
  dz.addEventListener('drop', (e) => {
    e.preventDefault(); dz.classList.remove('drag');
    const f = e.dataTransfer.files && e.dataTransfer.files[0];
    if (f) doOnboard(f);
  });
}

async function doOnboard(file) {
  const btn = $('#qr-submit');
  const status = $('#ob-status');
  // 确保本浏览器有独立身份（不能是 default/站长）
  let uid = getCurrentUser();
  if (!uid || uid === 'default') {
    uid = 'guest-' + Math.random().toString(36).slice(2, 10);
    localStorage.setItem(MUSIC_UID_KEY, uid);
  }
  const fd = new FormData();
  fd.append('file', file);
  if (btn) { btn.disabled = true; btn.textContent = '识别中…'; }
  if (status) status.textContent = '正在识别二维码并抓取歌单…';
  try {
    const res = await apiPostMultipart('/api/music/v2/onboard-qr?user_id=' + encodeURIComponent(uid), fd);
    if (res && res.profile) {
      if (res.name) localStorage.setItem(MUSIC_NAME_KEY, res.name);
      const n = res.song_count ?? (res.profile && res.profile.library_size) ?? '';
      if (status) status.textContent = '已识别 ' + (n ? n + ' 首' : '你的歌单') + '，已生成你的专属推荐';
      toast('已识别你的卡片，正在刷新推荐');
      MUSIC_CACHE = null; MUSIC_UID_LOADED = null;
      renderOnboardState();
      await loadNewReleases();
    } else {
      if (status) status.textContent = '识别失败：请确认是 QQ音乐 分享卡片截图';
    }
  } catch (e) {
    if (status) status.textContent = '识别失败：' + (e && e.message ? e.message : e);
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = '重新选择'; }
    input.value = '';
  }
}

function renderOnboardState() {
  const links = $('#ob-links');
  if (!links) return;
  const uid = getCurrentUser();
  if (uid && uid !== 'default') {
    links.innerHTML =
      '<a href="javascript:;" id="ob-reset">重新上传</a>' +
      '<a href="javascript:;" id="ob-demo">查看示例推荐</a>';
    $('#ob-reset').addEventListener('click', () => {
      const input = $('#qr-file'); if (input) input.click();
    });
    $('#ob-demo').addEventListener('click', () => {
      localStorage.setItem(MUSIC_UID_KEY, 'default');
      MUSIC_CACHE = null; MUSIC_UID_LOADED = null;
      links.innerHTML = '';
      const st = $('#ob-status'); if (st) st.textContent = '';
      loadNewReleases();
    });
  } else {
    links.innerHTML = '';
  }
}



async function loadNewReleases() {
  const list = $('#new-releases-list');
  const uid = getCurrentUser();
  if (MUSIC_CACHE && MUSIC_UID_LOADED === uid) {
    renderMusic(MUSIC_CACHE);
    return;
  }
  list.dataset.loaded = '1';
  list.classList.add('cols-2');
  list.innerHTML = Array(6).fill('<div class="skeleton" style="height:90px"></div>').join('');
  const qs = userQS();
  try {
    const d = window.__STATIC__
      ? await fetch('data/new_releases.json').then(r => r.json())
      : await api('/api/music/v2/new-releases?top_n=20&period=last_week' + (qs ? '&' + qs : ''));
    if (!d || !d.recommendations || !d.recommendations.length) {
      list.innerHTML = '<div class="empty">暂无新歌数据</div>';
      return;
    }
    MUSIC_CACHE = d;
    MUSIC_UID_LOADED = uid;
    renderMusic(d);
  } catch (e) {
    list.innerHTML = '<div class="empty">加载失败</div>';
  }
}


function renderMusic(d) {
  const recs = d.recommendations || [];
  updateHeroKpi(d);
  renderSheetProfile();
  renderSongList(recs);
  animateBars();
  observeReveal();
  attachTilt();
}

function updateHeroKpi(d) {
  const profile = d.profile || {};
  const lib = profile.library_size ?? 0;
  const rel = d.period_release_count ?? d.total_fetched ?? 0;
  const total = d.total_fetched ?? 0;
  const hit = (d.recommendations || []).length;
  const kpi = $('#hero-kpi');
  kpi.innerHTML = `
    <div class="hk reveal"><span class="n kpi-num" data-count="${lib}">0</span><span class="l">分析曲目</span></div>
    <div class="hk reveal"><span class="n kpi-num" data-count="${rel}">0</span><span class="l">上周新歌</span></div>
    <div class="hk reveal"><span class="n kpi-num" data-count="${hit}">0</span><span class="l">推荐命中</span></div>`;
  setTimeout(() => {
    $$('#hero-kpi .reveal').forEach(el => el.classList.add('in'));
    $$('#hero-kpi .kpi-num').forEach(el => countUp(el, +el.dataset.count));
  }, 100);
}

async function renderSheetProfile() {
  if (window.__STATIC__) return;
  const box = $('#sheet-profile');
  const bars = $('#sp-bars');
  const count = $('#sp-count');
  const status = $('#sp-status');
  if (!box) return;
  const qs = userQS();
  try {
    const p = await api('/api/music/v2/melody-profile' + (qs ? '?' + qs : ''));
    if (p && p.status === 'generating') {
      // OMR 正在后台识别该用户的谱子，显示进度并轮询
      count.textContent = '识别中…';
      const prog = p.progress || {};
      status.textContent = prog.text ? ('谱子识别中：' + prog.text) : '谱子识别中…';
      bars.innerHTML = '';
      pollSheetProfile(p.task_id);
      return;
    }
    const recognized = p.recognized || 0;
    count.textContent = `已识别 ${recognized} 首`;
    status.textContent = recognized > 0 ? '基于真实曲谱图片识别' : '尚未生成';
    const items = [
      { label: '大调', value: (p.key_mode_dist?.['大调'] || 0), color: '#8b5cf6' },
      { label: '中音区', value: (p.register_dist?.['中'] || 0), color: '#38bdf8' },
      { label: '级进', value: (p.contour_dist?.['级进'] || 0), color: '#34d399' },
      { label: '节奏密集', value: (p.rhythm_dist?.['密集'] || 0), color: '#f472b6' },
      { label: '4/4拍', value: (p.meter_dist?.['4/4'] || 0), color: '#fbbf24' },
    ];
    bars.innerHTML = items.map(it => `
      <div class="sp-bar-row">
        <span class="sp-bar-label">${it.label}</span>
        <div class="sp-bar-track"><div class="sp-bar-fill" style="width:${Math.round(it.value * 100)}%;background:${it.color}"></div></div>
        <span class="sp-bar-val">${Math.round(it.value * 100)}%</span>
      </div>
    `).join('');
  } catch (e) {
    count.textContent = '未生成';
    status.textContent = '尚未生成';
    bars.innerHTML = '';
  }
}

let _sheetPollTimer = null;
function pollSheetProfile(taskId) {
  if (!taskId) return;
  if (_sheetPollTimer) clearInterval(_sheetPollTimer);
  const status = $('#sp-status');
  _sheetPollTimer = setInterval(async () => {
    try {
      const t = await api('/api/tasks/' + taskId);
      if (t && t.status === 'done') {
        clearInterval(_sheetPollTimer); _sheetPollTimer = null;
        renderSheetProfile();
      } else if (t && t.status === 'error') {
        clearInterval(_sheetPollTimer); _sheetPollTimer = null;
        if (status) status.textContent = '谱子识别失败，可重试';
      } else if (status && t && t.progress) {
        status.textContent = '谱子识别中：' + (t.progress.text || '');
      }
    } catch (e) { /* 轮询失败忽略，下一轮重试 */ }
  }, 4000);
}

function countUp(el, target, dur = 900) {
  const t0 = performance.now();
  const ease = t => 1 - Math.pow(1 - t, 3);
  (function step(now) {
    const p = Math.min(1, (now - t0) / dur);
    el.textContent = Math.round(target * ease(p)).toLocaleString();
    if (p < 1) requestAnimationFrame(step);
  })(t0);
}

function renderTop3(top) {
  const box = $('#top3-list');
  box.innerHTML = top.map((s, i) => {
    const f = s.factors || {};
    const pf = s.platform || 'qq';
    return `<div class="top-card reveal" style="animation-delay:${i*80}ms">
      <div class="rank">#${i + 1}</div>
      <div class="content">
        <div class="t">${platformIcon(pf)} ${esc(s.title)}</div>
        <div class="ar">${esc(s.artists)} · ${esc(s.album || '单曲')}</div>
        <div class="score">${s.match_score}</div>
        <div class="reason">${esc(s.reason)}</div>
        <div class="factors">
          ${factorBar('歌手', f.artist / 40)}
          ${factorBar('风格', f.style / 35)}
          ${factorBar('新鲜', f.freshness / 15)}
        </div>
      </div>
    </div>`;
  }).join('');
  setTimeout(() => $$('.top-card.reveal').forEach(el => el.classList.add('in')), 120);
}

function songDomId(s, i) { return 'song-' + (s._key || ('i' + i)).replace(/[^a-zA-Z0-9_-]/g, ''); }

function renderSongList(recs) {
  const list = $('#new-releases-list');
  $('#music-count').textContent = '全部 ' + recs.length + ' 首推荐';
  list.innerHTML = recs.map((s, i) => {
    const f = s.factors || {};
    const pf = s.platform || 'qq';
    return `<div class="song reveal" id="${songDomId(s, i)}" style="animation-delay:${(i % 5)*60}ms">
      <div class="idx">${i + 1}</div>
      <div class="song-main">
        <div class="song-top">
          <div class="meta">
            <div class="t">${platformIcon(pf)} ${esc(s.title)} ${i < 3 ? '<span class="badge new">TOP</span>' : ''} <span class="ar">${esc(s.artists)} · ${esc(s.album || '单曲')} · ${esc(s.language || '未知')}</span></div>
          </div>
          <div class="eq"><i></i><i></i><i></i><i></i></div>
          <div class="score">${s.match_score}</div>
        </div>
        <div class="tags">
          <span class="tag">发布 ${esc(s.release_date || '未知')}</span>
          <span class="tag ml">${esc(s.reason)}</span>
        </div>
        <div class="factors">
          ${factorBar('歌手', f.artist / 40)}
          ${factorBar('风格', f.style / 35)}
          ${factorBar('创作者', (f.creator || 0) / 10)}
          ${factorBar('语言', (f.language || 0) / 5)}
          ${factorBar('新鲜', f.freshness / 15)}
          ${factorBar('热度', f.popularity / 10)}
        </div>
      </div>
    </div>`;
  }).join('');
  list.dataset.loaded = '1';
  setTimeout(() => $$('#new-releases-list .reveal').forEach((el, i) => {
    setTimeout(() => el.classList.add('in'), i * 40);
  }), 50);
}

function filterSongs() {
  const q = ($('#song-search').value || '').trim().toLowerCase();
  const sort = $('#song-sort').value || 'score';
  let recs = [...(MUSIC_CACHE?.recommendations || [])];
  if (q) recs = recs.filter(s =>
    [s.title, s.artists, s.language, s.album, (s.matched_styles || []).join(' '), (s.genre || '')]
      .join(' ').toLowerCase().includes(q));
  recs.sort((a, b) => {
    if (sort === 'artist') return (a.artists || '').localeCompare(b.artists || '');
    if (sort === 'language') return (a.language || '').localeCompare(b.language || '');
    if (sort === 'fresh') return (b.release_date || '').localeCompare(a.release_date || '');
    return (b.match_score || 0) - (a.match_score || 0);
  });
  renderSongList(recs);
  animateBars();
  observeReveal();
  $('#song-count').textContent = recs.length + ' 首';
  $('#music-count').textContent = '全部 ' + recs.length + ' 首推荐';
}

function factorBar(name, v, cls) {
  return `<div class="factor">${name}<div class="fb ${cls}"><i data-w="${Math.round((v || 0) * 100)}"></i></div></div>`;
}
function animateBars() {
  requestAnimationFrame(() => $$('.fb > i').forEach(i => i.style.width = i.dataset.w + '%'));
}
function platformIcon(platform) {
  const neteaseIcon = `<svg class="platform-icon netease" viewBox="0 0 24 24" width="18" height="18" aria-label="网易云音乐"><circle cx="12" cy="12" r="11" fill="#C20C0C"/><path d="M7 16.5c.5.8 1.5 1.2 2.5 1 1.5-.3 2.5-1.7 2.2-3.2-.1-.5-.4-1-.8-1.3l.2-2.5c.8.4 1.4 1.1 1.7 2 .3 1 .2 2-.3 2.9-.4.7-.3 1.5.3 2 .6.5 1.5.4 2-.2 1.2-1.4 1.5-3.3.8-5-1-2.5-3.8-3.8-6.3-3-1 .3-1.8 1-2.3 1.9l-.3-3.5c-.1-.6-.6-1-1.2-.9-.5.1-.9.6-.8 1.2l.8 8.1c0 .2.1.4.2.5zm4.5-9c-.2-.6-.8-1-1.4-.8-.6.2-1 .8-.8 1.4.1.2.1.3.2.5l.2.5c.2.5.7.8 1.2.7.5-.1.9-.6.8-1.1l-.2-.5z" fill="#fff"/></svg>`;
  const qqIcon = `<svg class="platform-icon qq" viewBox="0 0 24 24" width="18" height="18" aria-label="QQ音乐"><circle cx="12" cy="12" r="11" fill="#31C27C"/><path d="M16.2 6.3c-.4 0-.8.2-1.1.5l-2.3 2.6c-.4.4-.3.9.2 1.1.5.2 1.1.5 1.4.9.4.5.3 1.1-.3 1.4-.5.3-1.2.2-1.6-.2-.4-.4-.5-1-.3-1.5.2-.4 0-.9-.4-1.1-.4-.2-.9 0-1.1.4-.5 1-.3 2.2.5 3 1 1 2.6 1.2 3.7.4 1.2-.8 1.6-2.4.9-3.6-.2-.3-.4-.6-.7-.8l1.6-1.8c.5-.5.4-1.3-.1-1.7-.3-.3-.6-.4-1-.4zM9.4 8.6c-.5-.2-1.1 0-1.3.5l-1 2.9c-.2.5.1 1 .6 1.2.6.2 1.1.6 1.3 1.1.2.6-.1 1.2-.7 1.4-.6.2-1.3-.1-1.5-.7-.2-.5-.1-1.1.3-1.5.4-.3.4-.9.1-1.3-.3-.4-.9-.5-1.3-.2-1 .7-1.4 2-.9 3.1.5 1.1 1.9 1.7 3.1 1.3 1.3-.4 2-1.8 1.6-3.1-.1-.4-.3-.7-.6-1l1.8-1.6c.5-.4.5-1.2.1-1.6-.2-.2-.5-.3-.8-.4z" fill="#fff"/></svg>`;
  if (platform === 'both') return `<span class="platform-icons">${qqIcon}${neteaseIcon}</span>`;
  if (platform === 'netease') return neteaseIcon;
  return qqIcon;
}

