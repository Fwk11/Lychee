/* lychee 前端 · effects.js
 * 视觉特效：极光背景、聚光灯、滚动进度条、TOP3 3D 倾斜、音乐 DNA 画像
 */

/* ---------- 极光动态背景（canvas + 鼠标视差）---------- */
function initAurora() {
  const cv = document.getElementById('aurora');
  if (!cv || !cv.getContext) return;
  const ctx = cv.getContext('2d');
  let w, h, dpr;
  function resize() {
    dpr = Math.min(2, window.devicePixelRatio || 1);
    w = cv.width = innerWidth * dpr; h = cv.height = innerHeight * dpr;
    cv.style.width = innerWidth + 'px'; cv.style.height = innerHeight + 'px';
  }
  resize();
  addEventListener('resize', resize);
  const blobs = [
    { x: .20, y: .22, r: .52, c: [139, 92, 246], sx: .00007, sy: .00005, px: 0, py: 0, a: .26 },
    { x: .82, y: .30, r: .46, c: [56, 189, 248], sx: -.00006, sy: .00008, px: 1.7, py: .4, a: .22 },
    { x: .62, y: .82, r: .50, c: [244, 114, 182], sx: .00005, sy: -.00006, px: 3.1, py: 2.2, a: .20 },
    { x: .36, y: .72, r: .42, c: [34, 211, 238], sx: -.00004, sy: .00004, px: 4.4, py: 1.1, a: .20 },
    { x: .50, y: .45, r: .56, c: [129, 140, 248], sx: .00003, sy: .00007, px: 5.5, py: 3.3, a: .14 }
  ];
  let mx = 0, my = 0, t = 0;
  addEventListener('mousemove', e => { mx = e.clientX / innerWidth - .5; my = e.clientY / innerHeight - .5; });
  function draw() {
    t += 0.004;
    ctx.clearRect(0, 0, w, h);
    ctx.globalCompositeOperation = 'lighter';
    for (const b of blobs) {
      b.px += b.sx; b.py += b.sy;
      const cx = (b.x + Math.sin(b.px * 6.2832) * 0.08 + mx * 0.05) * w;
      const cy = (b.y + Math.cos(b.py * 6.2832) * 0.08 + my * 0.05) * h;
      const rad = b.r * h * (1 + Math.sin(t + b.px * 3) * 0.06);  // 缓慢呼吸
      const alpha = b.a * (0.78 + 0.22 * Math.sin(t * 1.3 + b.py * 2));
      const g = ctx.createRadialGradient(cx, cy, 0, cx, cy, rad);
      g.addColorStop(0, `rgba(${b.c[0]},${b.c[1]},${b.c[2]},${alpha.toFixed(3)})`);
      g.addColorStop(1, `rgba(${b.c[0]},${b.c[1]},${b.c[2]},0)`);
      ctx.fillStyle = g; ctx.beginPath(); ctx.arc(cx, cy, rad, 0, 7); ctx.fill();
    }
    requestAnimationFrame(draw);
  }
  draw();
}

/* ---------- 光标聚光灯 ---------- */
function initSpotlight() {
  const sp = document.getElementById('spotlight');
  if (!sp) return;
  addEventListener('mousemove', e => {
    sp.style.setProperty('--mx', e.clientX + 'px');
    sp.style.setProperty('--my', e.clientY + 'px');
  }, { passive: true });
}

/* ---------- 滚动进度条 ---------- */
function initScrollProgress() {
  const bar = document.getElementById('scroll-progress');
  if (!bar) return;
  addEventListener('scroll', () => {
    const st = document.documentElement.scrollTop || document.body.scrollTop;
    const sh = (document.documentElement.scrollHeight || document.body.scrollHeight) - innerHeight;
    bar.style.width = (sh > 0 ? (st / sh * 100) : 0) + '%';
  }, { passive: true });
}

/* ---------- TOP3 卡片 3D 倾斜 + 高光 ---------- */
function attachTilt() {
  $$('.top-card').forEach(card => {
    if (card.dataset.tilt) return;
    card.dataset.tilt = '1';
    card.addEventListener('mousemove', e => {
      const r = card.getBoundingClientRect();
      const px = (e.clientX - r.left) / r.width, py = (e.clientY - r.top) / r.height;
      const rx = (py - .5) * -10, ry = (px - .5) * 12;
      card.style.transform = `perspective(900px) rotateX(${rx}deg) rotateY(${ry}deg) translateY(-6px)`;
      card.style.setProperty('--gx', (px * 100) + '%');
      card.style.setProperty('--gy', (py * 100) + '%');
    });
    card.addEventListener('mouseleave', () => { card.style.transform = ''; });
  });
}


