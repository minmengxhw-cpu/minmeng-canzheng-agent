/* 盟易办 · 口袋履职 · 前端逻辑（纯前端 + localStorage，无后端） */
(() => {
  'use strict';

  const LS_PROG = 'mengyiban.progress.v1';
  const LS_ORG = 'mengyiban.org.v1';
  const MOD_COLOR = {
    sixiang: 'var(--m-sixiang)', zuzhi: 'var(--m-zuzhi)',
    canzheng: 'var(--m-canzheng)', shehui: 'var(--m-shehui)', neibu: 'var(--m-neibu)'
  };

  let DATA = { tasks: null, acts: null, guide: null };
  let progress = loadJSON(LS_PROG, {});      // { taskId: doneCount }
  let orgName = localStorage.getItem(LS_ORG) || '未命名支部';
  let curView = 'home';
  let openAct = null;     // 当前展开的活动 id

  // ---------- 工具 ----------
  function loadJSON(k, def) { try { return JSON.parse(localStorage.getItem(k)) || def; } catch { return def; } }
  function save() { localStorage.setItem(LS_PROG, JSON.stringify(progress)); }
  function $(sel, el = document) { return el.querySelector(sel); }
  function el(html) { const t = document.createElement('template'); t.innerHTML = html.trim(); return t.content.firstChild; }
  function esc(s) { return String(s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c])); }
  let toastTimer;
  function toast(msg) {
    const old = $('.toast'); if (old) old.remove();
    const t = el(`<div class="toast">${esc(msg)}</div>`);
    document.body.appendChild(t);
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => t.remove(), 2200);
  }

  // 全部任务扁平化
  function allTasks() { return DATA.tasks.modules.flatMap(m => m.tasks.map(t => ({ ...t, module: m.key, modName: m.name }))); }
  function taskById(id) { return allTasks().find(t => t.id === id); }
  function doneOf(id) { return progress[id] || 0; }
  function isDone(t) { return doneOf(t.id) >= t.target; }

  // 模块完成统计
  function moduleStats() {
    return DATA.tasks.modules.map(m => {
      const done = m.tasks.filter(t => doneOf(t.id) >= t.target).length;
      return { key: m.key, name: m.name, done, total: m.tasks.length };
    });
  }
  function overallStats() {
    const ts = allTasks();
    const done = ts.filter(isDone).length;
    return { done, total: ts.length, pct: Math.round(done / ts.length * 100) };
  }

  // ---------- 视图：首页 ----------
  function viewHome() {
    const o = overallStats();
    const mods = moduleStats();
    const C = 2 * Math.PI * 46;
    const offset = C * (1 - o.pct / 100);

    // 临近节点（演示逻辑：取未达标的关键任务）
    const pending = [];
    const dlMap = [
      { id: 'sh_jidu_biao', tag: '季度表 6/20 前', cls: 'warn' },
      { id: 'sx_xuanchuan', tag: '本月宣传稿', cls: 'warn' },
      { id: 'cz_zhuanxiang_huiyi', tag: '专项会议待召开', cls: 'danger' }
    ];
    dlMap.forEach(d => { const t = taskById(d.id); if (t && !isDone(t)) pending.push({ t, ...d }); });

    const wrap = el(`<div class="fade-in"></div>`);
    wrap.appendChild(el(`
      <section>
        <div class="hero">
          <div class="ring">
            <svg width="104" height="104" viewBox="0 0 104 104">
              <circle class="ring-track" cx="52" cy="52" r="46" fill="none" stroke-width="9"/>
              <circle class="ring-fill" cx="52" cy="52" r="46" fill="none" stroke-width="9"
                stroke-dasharray="${C.toFixed(1)}" stroke-dashoffset="${offset.toFixed(1)}"/>
            </svg>
            <div class="ring-center"><div><div class="ring-pct">${o.pct}%</div><div class="ring-cap">今年达标</div></div></div>
          </div>
          <div class="hero-meta">
            <h2>${esc(orgName)}</h2>
            <p>2026 年度履职进度</p>
            <div class="hero-stat">
              <div><b>${o.done}</b><span>已达标</span></div>
              <div><b style="color:var(--accent)">${o.total - o.done}</b><span>待完成</span></div>
              <div><b>${o.total}</b><span>总任务</span></div>
            </div>
          </div>
        </div>
      </section>`));

    // 快捷入口
    const entries = [
      ['◷', '今日待办', () => go('tasks')],
      ['▤', '年度任务', () => go('tasks')],
      ['✦', '我要办活动', () => go('activity')],
      ['✎', '我要写材料', () => go('activity')],
      ['↥', '我要报送', () => toast('报送中心将在下一期接入')],
      ['❖', '我要查指南', () => go('guide')]
    ];
    const grid = el(`<section><h3 class="sec-title">快捷入口</h3><div class="grid6"></div></section>`);
    const g = $('.grid6', grid);
    entries.forEach(([ico, txt, fn]) => {
      const e = el(`<button class="entry"><span class="e-ico">${ico}</span><span class="e-txt">${txt}</span></button>`);
      e.addEventListener('click', fn); g.appendChild(e);
    });
    wrap.appendChild(grid);

    // 临近节点
    const dlSec = el(`<section><h3 class="sec-title">临近节点</h3><div class="deadlines"></div></section>`);
    const dlBox = $('.deadlines', dlSec);
    if (pending.length === 0) {
      dlBox.appendChild(el(`<div class="dl"><span class="dl-dot ok"></span><div class="dl-body"><b>暂无临近节点</b><span>本月任务进展良好</span></div></div>`));
    } else {
      pending.forEach(({ t, tag, cls }) => {
        dlBox.appendChild(el(`
          <div class="dl">
            <span class="dl-dot ${cls}"></span>
            <div class="dl-body"><b>${esc(t.title)}</b><span>${esc(t.cadence)} · 已完成 ${doneOf(t.id)}/${t.target}</span></div>
            <span class="dl-tag ${cls}">${esc(tag)}</span>
          </div>`));
      });
    }
    wrap.appendChild(dlSec);

    // 模块进度迷你
    const ms = el(`<section><h3 class="sec-title">五大模块</h3><div class="mod-bars"></div></section>`);
    const mb = $('.mod-bars', ms);
    mods.forEach(m => {
      const pct = Math.round(m.done / m.total * 100);
      mb.appendChild(el(`
        <div class="mod-bar">
          <div class="mod-bar-head">
            <b><span class="dot-mod" style="background:${MOD_COLOR[m.key]}"></span>${esc(m.name)}</b>
            <span class="frac"><b>${m.done}</b>/${m.total} 项达标</span>
          </div>
          <div class="bar"><i style="width:${pct}%;background:${MOD_COLOR[m.key]}"></i></div>
        </div>`));
    });
    wrap.appendChild(ms);
    return wrap;
  }

  // ---------- 视图：年度任务 ----------
  function viewTasks() {
    const wrap = el(`<div class="fade-in"></div>`);
    const o = overallStats();
    wrap.appendChild(el(`<section><h3 class="sec-title">年度任务 · 已达标 ${o.done}/${o.total}</h3></section>`));

    DATA.tasks.modules.forEach(m => {
      const grp = el(`<div class="mod-group"><h3><span class="dot-mod" style="background:${MOD_COLOR[m.key]}"></span>${esc(m.name)}</h3></div>`);
      m.tasks.forEach(t => grp.appendChild(taskCard(t)));
      wrap.appendChild(grp);
    });
    return wrap;
  }

  function taskCard(t) {
    const done = doneOf(t.id), full = done >= t.target;
    const pct = Math.min(100, Math.round(done / t.target * 100));
    const card = el(`
      <div class="task ${full ? 'done' : ''}">
        <div class="task-top">
          <div>
            <div class="task-title">${esc(t.title)}</div>
            <div class="task-cad">${esc(t.cadence)} · 归档：${esc(t.archive)}</div>
          </div>
          <span class="lvl ${t.level}">${t.level}</span>
        </div>
        <div class="task-ctrl">
          <div class="counter">
            <button data-act="dec">−</button>
            <span class="val"><b class="dv">${done}</b><small>/${t.target}${t.unit}</small></span>
            <button data-act="inc">+</button>
          </div>
          <div class="task-bar"><i style="width:${pct}%;background:${full ? 'var(--ok)' : 'var(--accent)'}"></i></div>
          ${full ? '<span class="task-check">✓ 达标</span>' : ''}
        </div>
      </div>`);
    card.querySelectorAll('.counter button').forEach(b => {
      b.addEventListener('click', () => {
        const d = b.dataset.act === 'inc' ? 1 : -1;
        progress[t.id] = Math.max(0, Math.min(t.target, doneOf(t.id) + d));
        save();
        const newCard = taskCard(t);
        card.replaceWith(newCard);
      });
    });
    return card;
  }

  // ---------- 视图：办活动（覆盖矩阵） ----------
  function viewActivity() {
    const wrap = el(`<div class="fade-in"></div>`);
    wrap.appendChild(el(`<section><h3 class="sec-title">选一个活动 · 看它能覆盖几项指标</h3></section>`));
    DATA.acts.activities.forEach(a => wrap.appendChild(actCard(a)));
    return wrap;
  }

  function actCard(a) {
    const isOpen = openAct === a.id;
    const card = el(`
      <div class="act-card ${isOpen ? 'open' : ''}">
        <div class="act-name">${esc(a.name)}</div>
        <div class="act-scene">适用：${esc(a.scene)}</div>
        <div class="act-foot">
          <span class="cover-pill">可计 ${a.covers.length} 项指标</span>
          <span class="act-go">${isOpen ? '收起 ▲' : '展开看覆盖 ▾'}</span>
        </div>
      </div>`);

    card.addEventListener('click', e => {
      if (e.target.closest('.matrix')) return;     // 矩阵内部点击不收起
      openAct = isOpen ? null : a.id;
      go('activity');
    });

    if (isOpen) {
      const m = el(`<div class="matrix"></div>`);
      m.appendChild(el(`<div class="matrix-h">一次活动可同时计入 ↓</div>`));
      let gapCount = 0;
      a.covers.forEach(cid => {
        const t = taskById(cid); if (!t) return;
        const done = doneOf(cid), full = done >= t.target;
        if (!full) gapCount++;
        m.appendChild(el(`
          <div class="cov">
            <span class="cov-tick" style="background:${full ? 'var(--ok)' : 'var(--accent)'}">${full ? '✓' : '+'}</span>
            <div class="cov-body"><b>${esc(t.title)}</b><span>${esc(t.modName)} · ${esc(t.cadence)}</span></div>
            <span class="cov-prog ${full ? 'full' : 'gap'}">${done}/${t.target}${t.unit}</span>
          </div>`));
      });
      if (a.brand && a.brand.length) {
        const bt = el(`<div class="brand-tags"></div>`);
        a.brand.forEach(b => bt.appendChild(el(`<span class="brand-tag">${esc(b)}</span>`)));
        m.appendChild(bt);
      }
      m.appendChild(el(`<div class="summary-line">办这一次活动，可补齐 ${gapCount} 个未达标缺口，并一次生成下列 ${a.materials.length} 套材料。</div>`));
      const mats = el(`<div class="mat-list"></div>`);
      a.materials.forEach(x => mats.appendChild(el(`<span class="mat-chip">${esc(x)}</span>`)));
      m.appendChild(mats);

      const gen = el(`<button class="btn-primary">一键生成全套材料</button>`);
      gen.addEventListener('click', () => toast('材料生成将在下一期接入模型，先体验任务覆盖逻辑'));
      m.appendChild(gen);

      const mark = el(`<button class="btn-ghost">登记为已办（各指标 +1）</button>`);
      mark.addEventListener('click', () => {
        a.covers.forEach(cid => { const t = taskById(cid); if (t) progress[cid] = Math.min(t.target, doneOf(cid) + 1); });
        save();
        toast(`已登记：${a.covers.length} 项指标各 +1`);
        openAct = null; go('activity');
      });
      m.appendChild(mark);
      card.appendChild(m);
    }
    return card;
  }

  // ---------- 视图：查指南 ----------
  function viewGuide() {
    const wrap = el(`<div class="fade-in"></div>`);
    wrap.appendChild(el(`<section><h3 class="sec-title">${esc(DATA.guide.title)}</h3></section>`));
    DATA.guide.modules.forEach((m, i) => {
      const mod = el(`
        <div class="guide-mod ${i === 0 ? 'open' : ''}">
          <div class="guide-mod-head">
            <span class="dot-mod" style="background:${MOD_COLOR[m.key]}"></span>
            <b>${esc(m.name)}</b><span class="chev">▸</span>
          </div>
          <div class="guide-mod-body"></div>
        </div>`);
      const body = $('.guide-mod-body', mod);
      m.points.forEach(p => body.appendChild(el(`<div class="guide-pt">${esc(p)}</div>`)));
      $('.guide-mod-head', mod).addEventListener('click', () => mod.classList.toggle('open'));
      wrap.appendChild(mod);
    });
    wrap.appendChild(el(`<div class="kaohe-note">📋 ${esc(DATA.guide.kaohe)}</div>`));
    return wrap;
  }

  // ---------- 路由 ----------
  const VIEWS = { home: viewHome, tasks: viewTasks, activity: viewActivity, guide: viewGuide };
  function go(v) {
    curView = v;
    const host = $('#view');
    host.innerHTML = '';
    host.appendChild(VIEWS[v]());
    host.scrollTop = 0; window.scrollTo(0, 0);
    document.querySelectorAll('.tab').forEach(t => t.classList.toggle('active', t.dataset.view === v));
  }

  // ---------- 初始化 ----------
  async function init() {
    document.querySelectorAll('.tab').forEach(t => t.addEventListener('click', () => { openAct = null; go(t.dataset.view); }));
    $('#orgBtn').textContent = orgName + ' ▾';
    $('#orgBtn').addEventListener('click', () => {
      const n = prompt('输入支部 / 基层组织名称', orgName);
      if (n && n.trim()) { orgName = n.trim(); localStorage.setItem(LS_ORG, orgName); $('#orgBtn').textContent = orgName + ' ▾'; if (curView === 'home') go('home'); }
    });

    try {
      const [t, a, g] = await Promise.all([
        fetch('data/tasks.json').then(r => r.json()),
        fetch('data/activities.json').then(r => r.json()),
        fetch('data/guide.json').then(r => r.json())
      ]);
      DATA.tasks = t; DATA.acts = a; DATA.guide = g;
      go('home');
    } catch (e) {
      $('#view').innerHTML = `<div class="empty">数据加载失败：${esc(e.message)}</div>`;
    }
  }
  init();
})();
