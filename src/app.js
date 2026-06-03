/* ============================================================
   参政议政赋能平台 · 应用逻辑
   架构：纯前端 SPA，依赖 window.PLATFORM_DATA（来自 data.js）
   ============================================================ */

const DATA = window.PLATFORM_DATA;

const OUTPUT_LABEL = {
  brief: "社情民意信息",
  proposal: "提案建议",
  research: "参政议政课题",
};

const LEVEL_CLASS = {
  "中央": "lv-central",
  "市级": "lv-city",
  "部委": "lv-ministry",
  "法定公开": "lv-public",
};

const state = {
  selectedTheme: "all",
  selectedTopicId: DATA.topics[0].id,
  selectedOutput: "brief",
  search: "",
  leaderRole: "all",     // all | 陈吉宁 | 龚正
  leaderTheme: "all",
  leaderSearch: "",
  leaderItems: [],       // 所有 leaders 信号缓存
};

/* ------------------------------------------------------------
   元素引用
   ------------------------------------------------------------ */

const $ = (sel) => document.querySelector(sel);

const els = {
  themeFilter: $("#themeFilter"),
  leaderStream: $("#leaderStream"),
  leaderTimeline: $("#leaderTimeline"),
  leadersEyebrow: $("#leadersEyebrow"),
  leaderRoleFilter: $("#leaderRoleFilter"),
  leaderThemeFilter: $("#leaderThemeFilter"),
  leaderSearch: $("#leaderSearch"),
  leaderStat: $("#leaderStat"),
  focusGrid: $("#focusGrid"),
  focusEyebrow: $("#focusEyebrow"),
  cutGrid: $("#cutGrid"),
  cutSearch: $("#cutSearch"),
  signalList: $("#signalList"),
  signalScope: $("#signalScope"),
  outputTabs: $("#outputTabs"),
  workbenchTitle: $("#workbenchTitle"),
  activeCut: $("#activeCut"),
  draftPanel: $("#draftPanel"),
  sourceGrid: $("#sourceGrid"),
  metaSignals: $("#metaSignals"),
  metaTopics: $("#metaTopics"),
  metaSources: $("#metaSources"),
};

/* ------------------------------------------------------------
   工具函数
   ------------------------------------------------------------ */

function relativeDate(dateStr) {
  if (!dateStr) return "";
  const d = new Date(dateStr);
  if (Number.isNaN(d.getTime())) return dateStr;
  const now = new Date();
  const diff = Math.floor((now - d) / (1000 * 60 * 60 * 24));
  if (diff <= 0) return "今日";
  if (diff === 1) return "昨日";
  if (diff < 7) return `${diff} 天前`;
  if (diff < 30) return `${Math.floor(diff / 7)} 周前`;
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

function levelClass(level) {
  return LEVEL_CLASS[level] || "lv-public";
}

function getMatchedSignals(topic) {
  return DATA.signals.filter((sig) =>
    topic.keywords.some((kw) => sig.keywords.includes(kw))
  );
}

function getTopicScore(topic) {
  const matched = getMatchedSignals(topic);
  if (!matched.length) return 60;
  const avgIntensity =
    matched.reduce((sum, s) => sum + s.intensity, 0) / matched.length;
  const bonus = matched.reduce((sum, s) => {
    if (s.level === "中央") return sum + 7;
    if (s.level === "市级") return sum + 5;
    if (s.level === "部委") return sum + 4;
    return sum + 2;
  }, 0);
  return Math.min(98, Math.round(avgIntensity * 0.7 + bonus));
}

function rankedTopics() {
  return DATA.topics
    .map((t) => ({ ...t, _score: getTopicScore(t), _matched: getMatchedSignals(t).length }))
    .sort((a, b) => b._score - a._score);
}

function filteredTopics() {
  return rankedTopics().filter((t) =>
    state.selectedTheme === "all" || t.theme === state.selectedTheme
  );
}

function filteredSignals() {
  return DATA.signals.filter((s) =>
    state.selectedTheme === "all" || s.theme === state.selectedTheme
  );
}

/* ------------------------------------------------------------
   渲染：市委主要领导动向（最高优先级）
   ------------------------------------------------------------ */

async function loadLeaderSignals() {
  // 优先用 data/leaders.json（真实抓取）；合并 data.js 的 mock 演示数据
  let real = [];
  try {
    const r = await fetch("./data/leaders.json", { cache: "no-store" });
    if (r.ok) {
      const j = await r.json();
      if (Array.isArray(j)) real = j;
    }
  } catch (e) {
    // 加载失败回退到纯 mock
  }
  const mock = (DATA.leader_signals || []).map((m) => ({ ...m, _source_kind: "mock" }));
  const realMarked = real.map((r) => ({ ...r, _source_kind: "real" }));
  // 真实数据 url 去重，与 mock 不同 source
  const seen = new Set(realMarked.map((r) => r.url));
  return [...realMarked, ...mock.filter((m) => !seen.has(m.url))];
}

async function renderLeaders() {
  // 加载并缓存所有信号
  const all = (await loadLeaderSignals())
    .filter((s) => s.date)
    .sort((a, b) => (b.date || "").localeCompare(a.date || ""));
  state.leaderItems = all;

  if (els.leadersEyebrow && all.length) {
    els.leadersEyebrow.textContent = `市委关注 · 共 ${all.length} 条 · 最近更新 ${all[0].date}`;
  }

  renderLeaderFilters();
  renderTrendChart();
  renderPhraseCloud();
  renderLeaderTimeline();
}

/* ============ 月度主题趋势图 ============ */

const THEME_ORDER = ["城市治理", "开放发展", "科技产业", "营商环境", "民生治理", "文化教育", "生态环境", "法治建设"];

function renderTrendChart() {
  const target = document.querySelector("#leaderTrend");
  if (!target) return;
  const items = filteredLeaders();
  if (!items.length) { target.innerHTML = ""; return; }

  // 按月分组 + 按主题统计
  const byMonth = {};
  items.forEach((s) => {
    const ym = (s.date || "").slice(0, 7);
    if (!byMonth[ym]) byMonth[ym] = { total: 0, themes: {} };
    byMonth[ym].total += 1;
    const t = s.theme || "未分类";
    byMonth[ym].themes[t] = (byMonth[ym].themes[t] || 0) + 1;
  });

  const months = Object.keys(byMonth).sort();
  const maxTotal = Math.max(...months.map((m) => byMonth[m].total));

  // 当前可见主题（按全局排序，仅保留有数据的）
  const visibleThemes = THEME_ORDER.filter((t) =>
    months.some((m) => (byMonth[m].themes[t] || 0) > 0)
  );

  const filterLabel =
    state.leaderRole !== "all" || state.leaderTheme !== "all" || state.leaderSearch
      ? `（当前筛选下 ${items.length} 条）`
      : "";

  const rowsHtml = months.map((ym) => {
    const data = byMonth[ym];
    const widthPct = (data.total / maxTotal) * 100;
    const segs = visibleThemes.map((t) => {
      const n = data.themes[t] || 0;
      if (!n) return "";
      const segPct = (n / data.total) * 100;
      return `<span class="trend-seg t-${t}" style="width:${segPct}%" title="${t} ${n} 条"></span>`;
    }).join("");
    const ymLabel = ym.replace(/^\d{4}-0?/, "") + " 月";
    return `
      <div class="trend-row">
        <span class="trend-month-label">${ymLabel}</span>
        <div class="trend-bar" style="width:${widthPct}%">${segs}</div>
        <span class="trend-total">${data.total}</span>
      </div>
    `;
  }).join("");

  const legendHtml = visibleThemes.map((t) => `
    <span class="trend-legend-item">
      <span class="trend-legend-dot t-${t}"></span>${t}
    </span>
  `).join("");

  target.innerHTML = `
    <div class="trend-chart">
      <div class="trend-head">
        <span class="trend-title">主题热度月度趋势</span>
        <span class="trend-sub">${filterLabel || `按月份统计 ${items.length} 条`}</span>
      </div>
      <div class="trend-rows">${rowsHtml}</div>
      <div class="trend-legend">${legendHtml}</div>
    </div>
  `;
}

/* ============ 近期新提法 Top（按日期倒序） ============ */

function renderPhraseCloud() {
  const target = document.querySelector("#leaderPhraseCloud");
  if (!target) return;
  const items = filteredLeaders();
  if (!items.length) { target.innerHTML = ""; return; }

  // 收集所有新提法 + 出处，按日期倒序展示
  const phrases = [];
  items.forEach((s) => {
    (s.new_phrasing || []).forEach((p) => {
      phrases.push({ text: p, date: s.date, leader: s.leader, theme: s.theme });
    });
  });

  if (!phrases.length) { target.innerHTML = ""; return; }

  // 按日期倒序，取近 20 条
  phrases.sort((a, b) => (b.date || "").localeCompare(a.date || ""));
  const top = phrases.slice(0, 20);

  // 用 chip 大小区分最新（前 5 条放大）
  const chipsHtml = top.map((p, i) => {
    const size = i < 3 ? "size-3" : i < 8 ? "size-2" : "";
    return `<span class="phrase-chip ${size}" title="${p.date} · ${p.leader} · ${p.theme || ""}">${p.text}</span>`;
  }).join("");

  target.innerHTML = `
    <div class="phrase-cloud">
      <div class="phrase-cloud-head">
        <span class="phrase-cloud-title">近期新提法</span>
        <span class="phrase-cloud-sub">按日期倒序 · Top ${top.length} / 共 ${phrases.length} 条</span>
      </div>
      <div class="phrase-cloud-list">${chipsHtml}</div>
    </div>
  `;
}

function renderLeaderFilters() {
  if (!els.leaderRoleFilter || !els.leaderThemeFilter) return;
  const all = state.leaderItems;
  // 领导筛选 chips（按数量降序）
  const byLeader = {};
  all.forEach((s) => { byLeader[s.leader] = (byLeader[s.leader] || 0) + 1; });
  const roles = [["all", `全部 ${all.length}`], ...Object.entries(byLeader)
    .sort((a,b)=>b[1]-a[1])
    .map(([k,v]) => [k, `${k} ${v}`])];
  els.leaderRoleFilter.innerHTML = roles.map(([k, label]) => `
    <button type="button" class="filter-chip ${state.leaderRole === k ? "active" : ""}" data-role="${k}">${label}</button>
  `).join("");

  // 主题筛选 chips（按数量降序）
  const byTheme = {};
  all.forEach((s) => {
    if (s.theme) byTheme[s.theme] = (byTheme[s.theme] || 0) + 1;
  });
  const themes = [["all", `全部主题`], ...Object.entries(byTheme)
    .sort((a,b)=>b[1]-a[1])
    .map(([k,v]) => [k, `${k} ${v}`])];
  els.leaderThemeFilter.innerHTML = themes.map(([k, label]) => `
    <button type="button" class="filter-chip ${state.leaderTheme === k ? "active" : ""}" data-theme="${k}">${label}</button>
  `).join("");
}

function filteredLeaders() {
  const q = state.leaderSearch.toLowerCase().trim();
  return state.leaderItems.filter((s) => {
    if (state.leaderRole !== "all" && s.leader !== state.leaderRole) return false;
    if (state.leaderTheme !== "all" && s.theme !== state.leaderTheme) return false;
    if (!q) return true;
    const hay = [
      s.headline, s.occasion, s.summary, s.theme,
      ...(s.new_phrasing || []), ...(s.key_points || []),
      ...(s.keywords || []), ...(s.subthemes || []),
    ].join(" ").toLowerCase();
    return hay.includes(q);
  });
}

function renderLeaderTimeline() {
  if (!els.leaderTimeline) return;
  const items = filteredLeaders();

  if (els.leaderStat) {
    els.leaderStat.textContent = `${items.length} / ${state.leaderItems.length} 条`;
  }

  if (!items.length) {
    els.leaderTimeline.innerHTML = `<p class="empty-tip">没有匹配的条目，调整筛选试试。</p>`;
    return;
  }

  // 按 YYYY-MM 分组
  const groups = {};
  items.forEach((s) => {
    const ym = (s.date || "").slice(0, 7);
    (groups[ym] = groups[ym] || []).push(s);
  });
  const ymsDesc = Object.keys(groups).sort().reverse();

  // 默认展开：最近月（最新的那一个）+ 当筛选后只剩 <= 2 个月时全部展开
  const expandSet = new Set(ymsDesc.slice(0, ymsDesc.length <= 2 ? ymsDesc.length : 1));

  els.leaderTimeline.innerHTML = ymsDesc.map((ym) => {
    const list = groups[ym].sort((a, b) => (b.date || "").localeCompare(a.date || ""));
    const open = expandSet.has(ym) ? "open" : "";
    const ymLabel = ym.replace("-", "年") + "月";
    return `
      <details class="month-group" ${open}>
        <summary class="month-head">
          <span class="month-label">${ymLabel}</span>
          <span class="month-count">${list.length} 条</span>
        </summary>
        <div class="month-list">
          ${list.map(renderLeaderCardHTML).join("")}
        </div>
      </details>
    `;
  }).join("");
}

function renderLeaderCardHTML(sig) {
  const np = sig.new_phrasing || [];
  const npPreview = np.slice(0, 2);
  const npRest = np.slice(2);
  const npHtml = np.length ? `
    <div class="leader-newphrase">
      <div class="np-label">重点变化 · ${np.length}</div>
      <ul class="np-list">
        ${npPreview.map((p) => `<li>${p}</li>`).join("")}
        ${npRest.length ? `<li class="np-more">+${npRest.length} 条，展开查看</li>` : ""}
      </ul>
    </div>` : "";

  const kp = sig.key_points || [];
  const kpHtml = kp.length ? `
    <div class="card-section">
      <div class="card-section-label">关键论断</div>
      <ul class="kp-list">${kp.map((p) => `<li>${p}</li>`).join("")}</ul>
    </div>` : "";

  const npFullHtml = npRest.length ? `
    <div class="card-section">
      <div class="card-section-label">完整新提法 · 共 ${np.length} 条</div>
      <ol class="np-quote-list">
        ${np.map((p, i) => `
          <li>
            <span class="np-quote-num">${String(i + 1).padStart(2, "0")}</span>
            <span class="np-quote-text">${p}</span>
          </li>
        `).join("")}
      </ol>
    </div>` : "";

  const summaryHtml = sig.summary ? `
    <div class="card-section">
      <div class="card-section-label">核心要点</div>
      <p class="card-text">${sig.summary}</p>
    </div>` : "";

  const implHtml = sig.policy_implications ? `
    <div class="leader-implications">
      <div class="impl-label">参政议政切口建议</div>
      <p>${sig.policy_implications}</p>
    </div>` : "";

  const themeChip = sig.theme ? `<span class="theme-chip">${sig.theme}</span>` : "";
  const roleClass = `rank-${sig.role_rank || 3}`;
  const occasion = sig.occasion || "";

  return `
    <details class="leader-card ${roleClass}">
      <summary class="leader-summary-head">
        <div class="leader-headrow">
          <span class="leader-name">${sig.leader}</span>
          <span class="leader-date">${sig.date}</span>
          ${themeChip}
        </div>
        <h3 class="leader-headline">${sig.headline}</h3>
        ${occasion ? `<p class="leader-occasion-line">${occasion}</p>` : ""}
        ${npHtml}
      </summary>
      <div class="leader-expand">
        ${summaryHtml}
        ${npFullHtml}
        ${kpHtml}
        ${implHtml}
        <div class="leader-foot">
          <a href="${sig.url}" target="_blank" rel="noreferrer">查看来源 →</a>
        </div>
      </div>
    </details>
  `;
}

/* ------------------------------------------------------------
   渲染：首屏 Top 3 切口
   ------------------------------------------------------------ */

function renderFocus() {
  const all = rankedTopics();
  const top3 = all.slice(0, 3);
  els.focusGrid.innerHTML = "";

  // eyebrow：本周 + 当前日期
  const now = new Date();
  const md = `${now.getMonth() + 1} 月 ${now.getDate()} 日`;
  els.focusEyebrow.textContent = `本周关注 · ${md}`;

  top3.forEach((topic, idx) => {
    const card = document.createElement("article");
    card.className = "focus-card";
    card.tabIndex = 0;
    card.innerHTML = `
      <span class="rank">${String(idx + 1).padStart(2, "0")}</span>
      <span class="theme">${topic.theme}</span>
      <h3>${topic.cut}</h3>
      <p class="focus-thesis">${topic.thesis}</p>
      <div class="focus-meta">
        <span>${topic._matched} 条信号匹配</span>
        <span class="score-chip">热度 ${topic._score}</span>
      </div>
    `;
    card.addEventListener("click", () => selectTopic(topic.id));
    card.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        selectTopic(topic.id);
      }
    });
    els.focusGrid.append(card);
  });
}

/* ------------------------------------------------------------
   渲染：切口孵化库
   ------------------------------------------------------------ */

function renderCuts() {
  const query = state.search.toLowerCase().trim();
  const topics = filteredTopics().filter((t) => {
    if (!query) return true;
    return [t.title, t.cut, t.theme, t.thesis, ...t.keywords]
      .join(" ").toLowerCase().includes(query);
  });

  els.cutGrid.innerHTML = "";
  if (!topics.length) {
    els.cutGrid.innerHTML = `<p style="color:var(--ink-mute);font-size:14px;padding:24px 0;">没有匹配的切口，试试别的关键词。</p>`;
    return;
  }

  topics.forEach((topic) => {
    const card = document.createElement("article");
    card.className = "cut-card";
    const mechChips = topic.mechanism
      .map((m) => `<span class="mech-chip">${m}</span>`)
      .join("");
    card.innerHTML = `
      <div class="cut-head">
        <div>
          <div class="cut-theme">${topic.theme}</div>
          <h3>${topic.cut}</h3>
        </div>
      </div>
      <p class="cut-thesis">${topic.thesis}</p>
      <div class="mechanism">${mechChips}</div>
      <div class="cut-foot">
        <span class="cut-score">热度 <strong>${topic._score}</strong> · ${topic._matched} 信号</span>
        <button type="button" class="open-btn">进入工作台</button>
      </div>
    `;
    card.querySelector(".open-btn").addEventListener("click", () => selectTopic(topic.id));
    els.cutGrid.append(card);
  });
}

/* ------------------------------------------------------------
   渲染：信号流
   ------------------------------------------------------------ */

function renderSignals() {
  const signals = [...filteredSignals()].sort(
    (a, b) => (b.date || "").localeCompare(a.date || "")
  );
  els.signalList.innerHTML = "";
  els.signalScope.textContent =
    state.selectedTheme === "all" ? "全部专题" : state.selectedTheme;

  signals.forEach((sig) => {
    const li = document.createElement("li");
    li.className = "signal-item";
    li.innerHTML = `
      <div class="signal-stem">
        <div class="date">${relativeDate(sig.date)}</div>
        <div>${sig.date || ""}</div>
      </div>
      <div class="signal-body">
        <h4>${sig.title}</h4>
        <p>${sig.summary}</p>
        <div class="signal-meta">
          <span class="level-chip ${levelClass(sig.level)}">${sig.level}</span>
          <span>${sig.source}</span>
          <span>${sig.theme}</span>
          <a class="signal-link" href="${sig.url}" target="_blank" rel="noreferrer">查看来源 →</a>
        </div>
      </div>
    `;
    els.signalList.append(li);
  });
}

/* ------------------------------------------------------------
   渲染：成果工作台
   ------------------------------------------------------------ */

function renderWorkbench() {
  const topic = DATA.topics.find((t) => t.id === state.selectedTopicId);
  if (!topic) return;
  els.workbenchTitle.textContent = `成果转化 · ${topic.theme}`;

  // 左侧：切口与机制
  els.activeCut.innerHTML = `
    <div class="pane-theme">${topic.theme}</div>
    <h3>${topic.cut}</h3>
    <p class="pane-thesis">${topic.thesis}</p>
    <h4>核验问题</h4>
    <ul>${topic.verification.map((v) => `<li>${v}</li>`).join("")}</ul>
    <h4>机制拆解</h4>
    <ul>${topic.mechanism.map((m) => `<li>${m}</li>`).join("")}</ul>
  `;

  // 右侧：当前选中档位的草稿
  const out = topic.outputs[state.selectedOutput];
  const blocks = out.blocks
    .map(
      ([heading, body]) => `
        <div class="draft-block">
          <div class="label">${heading}</div>
          <p>${body}</p>
        </div>
      `
    )
    .join("");
  els.draftPanel.innerHTML = `
    <div class="pane-theme">${OUTPUT_LABEL[state.selectedOutput]}</div>
    <div class="draft-title">${out.title}</div>
    ${blocks}
  `;
}

/* ------------------------------------------------------------
   渲染：信源
   ------------------------------------------------------------ */

function renderSources() {
  els.sourceGrid.innerHTML = "";
  DATA.sources.forEach((s) => {
    const card = document.createElement("article");
    card.className = "source-card";
    card.innerHTML = `
      <h3>${s.name}</h3>
      <div class="source-meta">${s.type} · ${s.cadence}</div>
      <div class="source-scope">${s.scope}</div>
      <a href="${s.url}" target="_blank" rel="noreferrer">打开来源 →</a>
    `;
    els.sourceGrid.append(card);
  });
}

/* ------------------------------------------------------------
   渲染：页脚元数据
   ------------------------------------------------------------ */

function renderMeta() {
  els.metaSignals.textContent = `${DATA.signals.length} 条公开信号`;
  els.metaTopics.textContent = `${DATA.topics.length} 个候选切口`;
  els.metaSources.textContent = `${DATA.sources.length} 个权威信源`;
}

/* ------------------------------------------------------------
   渲染：主题筛选下拉
   ------------------------------------------------------------ */

function renderThemeFilter() {
  const themes = [...new Set(DATA.topics.map((t) => t.theme))];
  themes.forEach((theme) => {
    const opt = document.createElement("option");
    opt.value = theme;
    opt.textContent = theme;
    els.themeFilter.append(opt);
  });
}

/* ------------------------------------------------------------
   交互：选中切口 → 进入工作台
   ------------------------------------------------------------ */

function selectTopic(topicId) {
  state.selectedTopicId = topicId;
  renderWorkbench();
  const wb = document.querySelector("#workbench");
  if (wb) wb.scrollIntoView({ behavior: "smooth", block: "start" });
}

/* ------------------------------------------------------------
   事件绑定
   ------------------------------------------------------------ */

function bindEvents() {
  els.themeFilter.addEventListener("change", (e) => {
    state.selectedTheme = e.target.value;
    renderFocus();
    renderCuts();
    renderSignals();
  });

  els.cutSearch.addEventListener("input", (e) => {
    state.search = e.target.value;
    renderCuts();
  });

  els.outputTabs.addEventListener("click", (e) => {
    const btn = e.target.closest("button[data-output]");
    if (!btn) return;
    state.selectedOutput = btn.dataset.output;
    els.outputTabs.querySelectorAll(".tab").forEach((t) => {
      t.classList.toggle("active", t === btn);
    });
    renderWorkbench();
  });

  // 领导筛选 chips
  if (els.leaderRoleFilter) {
    els.leaderRoleFilter.addEventListener("click", (e) => {
      const btn = e.target.closest("button[data-role]");
      if (!btn) return;
      state.leaderRole = btn.dataset.role;
      renderLeaderFilters();
      renderTrendChart();
      renderPhraseCloud();
      renderLeaderTimeline();
    });
  }
  if (els.leaderThemeFilter) {
    els.leaderThemeFilter.addEventListener("click", (e) => {
      const btn = e.target.closest("button[data-theme]");
      if (!btn) return;
      state.leaderTheme = btn.dataset.theme;
      renderLeaderFilters();
      renderTrendChart();
      renderPhraseCloud();
      renderLeaderTimeline();
    });
  }
  if (els.leaderSearch) {
    els.leaderSearch.addEventListener("input", (e) => {
      state.leaderSearch = e.target.value;
      renderTrendChart();
      renderPhraseCloud();
      renderLeaderTimeline();
    });
  }
}

/* ------------------------------------------------------------
   启动
   ------------------------------------------------------------ */

function init() {
  renderThemeFilter();
  renderLeaders();
  renderFocus();
  renderCuts();
  renderSignals();
  renderWorkbench();
  renderSources();
  renderMeta();
  bindEvents();
}

init();
