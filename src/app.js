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
  evolveTheme: null,     // 提法流变当前选中主题
  evolveOnlyNew: false,  // 只看有新增提法的节点
  cutStatus: "all",      // 切口台账状态筛选
  lifeFilter: "all",     // 提法生命周期筛选
  leaderSearch: "",
  leaderItems: [],       // 所有 leaders 信号缓存
  phraseCounts: {},      // phrase -> 累计反复次数（来自 chronology）
  cuts: [],              // 自动生成的切口（来自 cuts.json），优先于 DATA.topics
  drafts: {},            // cut_id -> {brief, proposal, research} LLM 生成的初稿
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
  evolveThemes: $("#evolveThemes"),
  evolveBody: $("#evolveBody"),
  evolveStat: $("#evolveStat"),
  evolveRecent: $("#evolveRecent"),
  evolveOnlyNew: $("#evolveOnlyNew"),
  lifeFilter: $("#lifeFilter"),
  lifeBody: $("#lifeBody"),
  lifeStat: $("#lifeStat"),
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

function activeTopicPool() {
  // 优先用自动生成的真实切口，回退到 DATA.topics（demo 兜底）
  return state.cuts.length ? state.cuts : DATA.topics;
}

function filteredTopics() {
  const pool = activeTopicPool();
  // 当使用 cuts.json 时按 count 排（反复次数），用 DATA.topics 时按打分
  const sorted = state.cuts.length
    ? [...pool].sort((a, b) => (b.count || 0) - (a.count || 0) || (b.first_date || "").localeCompare(a.first_date || ""))
    : pool.map((t) => ({ ...t, _score: getTopicScore(t), _matched: getMatchedSignals(t).length }))
         .sort((a, b) => b._score - a._score);
  return sorted.filter((t) => state.selectedTheme === "all" || t.theme === state.selectedTheme);
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

async function loadPhraseCounts() {
  try {
    const r = await fetch("./data/phrase_chronology.json", { cache: "no-store" });
    if (!r.ok) return {};
    const arr = await r.json();
    const map = {};
    arr.forEach((x) => { if (x.phrase) map[x.phrase] = x.count || 1; });
    return map;
  } catch (e) { return {}; }
}

async function loadCuts() {
  try {
    const r = await fetch("./data/cuts.json", { cache: "no-store" });
    if (!r.ok) return [];
    const arr = await r.json();
    return Array.isArray(arr) ? arr : [];
  } catch (e) { return []; }
}

async function loadDrafts() {
  try {
    const r = await fetch("./data/drafts.json", { cache: "no-store" });
    if (!r.ok) return {};
    const obj = await r.json();
    return obj && typeof obj === "object" ? obj : {};
  } catch (e) { return {}; }
}

async function renderLeaders() {
  // 加载并缓存所有信号 + 累计次数 + 切口 + 草稿
  const [all0, counts, cuts, drafts] = await Promise.all([
    loadLeaderSignals(), loadPhraseCounts(), loadCuts(), loadDrafts(),
  ]);
  state.drafts = drafts;
  const all = all0
    .filter((s) => s.date)
    .sort((a, b) => (b.date || "").localeCompare(a.date || ""));
  state.leaderItems = all;
  state.phraseCounts = counts;
  state.cuts = cuts;
  // 切到自动切口后第一条作为工作台默认选中
  if (cuts.length) state.selectedTopicId = cuts[0].id;
  // 数据就绪后重渲染依赖此数据的模块
  renderFocus();
  renderCuts();
  renderWorkbench();
  renderThemeFilter();
  renderMeta();

  if (els.leadersEyebrow && all.length) {
    els.leadersEyebrow.textContent = `市委关注 · 共 ${all.length} 条 · 最近更新 ${all[0].date}`;
  }

  renderLeaderFilters();
  renderTrendChart();
  renderPhraseCloud();
  renderLeaderTimeline();
  renderEvolveRecent();
  renderEvolution();
  renderLifecycle();
}

/* ============ 提法生命周期 ============ */
function lifeDays(a, b) {
  const pa = a.split("-").map(Number), pb = b.split("-").map(Number);
  return Math.round((Date.UTC(pb[0], pb[1] - 1, pb[2]) - Date.UTC(pa[0], pa[1] - 1, pa[2])) / 86400000);
}
const LIFE_STAGE = { "核心": "core", "成长": "grow", "稳定": "stable", "沉寂": "dormant" };

function lifeAggregate() {
  const m = {};
  state.leaderItems.forEach((s) => {
    if (!s.date) return;
    evoNP(s).forEach((p) => {
      p = (p || "").trim();
      if (!p) return;
      if (!m[p]) m[p] = { dates: [], themes: new Set(), roles: new Set() };
      m[p].dates.push(s.date);
      if (s.theme) m[p].themes.add(s.theme);
      if (s.role) m[p].roles.add(s.role);
    });
  });
  return m;
}

function renderLifecycle() {
  if (!els.lifeBody) return;
  const dates = state.leaderItems.map((s) => s.date).filter(Boolean).sort();
  if (!dates.length) { els.lifeBody.innerHTML = '<p class="evo-empty">暂无数据。</p>'; return; }
  const maxd = dates[dates.length - 1];
  const m = lifeAggregate();
  const totalPhrases = Object.keys(m).length;

  let rows = Object.keys(m).map((p) => {
    const ds = m[p].dates.slice().sort();
    const count = ds.length, first = ds[0], last = ds[count - 1];
    const dormant = lifeDays(last, maxd);
    let stage;
    if (count >= 3 && dormant > 45) stage = "沉寂";
    else if (count >= 5) stage = "核心";
    else if (dormant <= 21) stage = "成长";
    else stage = "稳定";
    return { p, count, first, last, span: lifeDays(first, last), dormant, themes: [...m[p].themes], roles: [...m[p].roles], stage };
  }).filter((r) => r.count >= 2);

  const oneOff = totalPhrases - rows.length;
  const cnt = { 核心: 0, 成长: 0, 稳定: 0, 沉寂: 0 };
  rows.forEach((r) => cnt[r.stage]++);

  // 筛选 chips
  const opts = [["all", "全部立住", rows.length], ["核心", "核心", cnt.核心], ["成长", "成长", cnt.成长], ["沉寂", "沉寂预警", cnt.沉寂]];
  if (!["all", "核心", "成长", "沉寂"].includes(state.lifeFilter)) state.lifeFilter = "all";
  if (els.lifeFilter) {
    els.lifeFilter.innerHTML = opts.map(([v, label, n]) =>
      `<button type="button" class="filter-chip ${state.lifeFilter === v ? "active" : ""}" data-life="${v}">${label} <span class="chip-n">${n}</span></button>`
    ).join("");
  }

  let view = state.lifeFilter === "all" ? rows : rows.filter((r) => r.stage === state.lifeFilter);
  view.sort((a, b) => state.lifeFilter === "沉寂" ? (b.dormant - a.dormant) : (b.count - a.count || b.last.localeCompare(a.last)));

  els.lifeBody.innerHTML = view.length ? `<ol class="life-list">${view.map((r) => {
    const sk = LIFE_STAGE[r.stage];
    const themeTags = r.themes.map((t) => `<span class="life-theme">${evoEsc(t)}</span>`).join("");
    const roleTags = r.roles.map((t) => `<span class="life-role">${evoEsc(t)}</span>`).join("");
    const dormTxt = r.stage === "沉寂" ? ` · <span class="life-dorm">已 ${r.dormant} 天未现</span>` : "";
    return `<li class="life-node st-${sk}">
      <div class="life-top"><span class="life-phrase">${evoEsc(r.p)}</span><span class="life-badge b-${sk}">${r.stage}</span></div>
      <div class="life-meta">反复 <b>${r.count}×</b> · ${r.first} → ${r.last} · 跨度 ${r.span} 天${dormTxt}</div>
      <div class="life-tags">${roleTags}${themeTags}</div>
    </li>`;
  }).join("")}</ol>` : '<p class="evo-empty">该筛选下暂无提法。</p>';

  if (els.lifeStat) {
    els.lifeStat.textContent = `立住 ${rows.length} 条（核心${cnt.核心}·成长${cnt.成长}·沉寂${cnt.沉寂}）· 另 ${oneOff} 条一次性`;
  }
}

/* ============ 提法流变：同主题历次表述演变 ============ */

function evoEsc(s) {
  return String(s == null ? "" : s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

function evoThemeCounts() {
  const cnt = {};
  state.leaderItems.forEach((s) => { if (s.theme) cnt[s.theme] = (cnt[s.theme] || 0) + 1; });
  return cnt;
}

function evoNP(s) {
  return Array.isArray(s.new_phrasing) ? s.new_phrasing : (s.new_phrasing ? [s.new_phrasing] : []);
}

// 日期字符串 YYYY-MM-DD 减 n 天
function dateMinus(ymd, n) {
  const d = new Date(ymd + "T00:00:00");
  if (Number.isNaN(d.getTime())) return ymd;
  d.setDate(d.getDate() - n);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

/* 近 30 天新增提法高亮区（跨主题，基于数据最新日期为基准） */
function renderEvolveRecent() {
  if (!els.evolveRecent) return;
  const dates = state.leaderItems.map((s) => s.date).filter(Boolean).sort();
  if (!dates.length) { els.evolveRecent.innerHTML = ""; return; }
  const cutoff = dateMinus(dates[dates.length - 1], 30);
  const rows = [];
  state.leaderItems.forEach((s) => {
    if (!s.date || s.date < cutoff) return;
    evoNP(s).forEach((p) => rows.push({ p, date: s.date, theme: s.theme || "", url: s.url || "" }));
  });
  rows.sort((a, b) => (b.date || "").localeCompare(a.date || ""));
  if (!rows.length) { els.evolveRecent.innerHTML = ""; return; }
  const top = rows.slice(0, 12);
  const chips = top.map((r) =>
    `<a class="evo-rchip"${r.url ? ` href="${evoEsc(r.url)}" target="_blank" rel="noopener"` : ""}>
       <span class="erc-date">${evoEsc((r.date || "").slice(5))}</span>
       <span class="erc-theme">${evoEsc(r.theme)}</span>
       <span class="erc-text">${evoEsc(r.p)}</span></a>`
  ).join("");
  els.evolveRecent.innerHTML =
    `<div class="evo-rhead">🔥 近 30 天新增提法 · 共 ${rows.length} 条${rows.length > 12 ? "（展示最新 12 条）" : ""}</div>
     <div class="evo-rlist">${chips}</div>`;
}

function renderEvolution() {
  if (!els.evolveBody) return;
  const cnt = evoThemeCounts();
  const themes = Object.keys(cnt).sort((a, b) => cnt[b] - cnt[a]);
  if (!themes.length) {
    els.evolveBody.innerHTML = '<p class="evo-empty">暂无可展示的主题数据。</p>';
    if (els.evolveThemes) els.evolveThemes.innerHTML = "";
    if (els.evolveStat) els.evolveStat.textContent = "";
    return;
  }
  if (!state.evolveTheme || !themes.includes(state.evolveTheme)) state.evolveTheme = themes[0];

  // 主题筛选 chips（复用 .filter-chip 样式）
  if (els.evolveThemes) {
    els.evolveThemes.innerHTML = themes.map((t) =>
      `<button type="button" class="filter-chip ${t === state.evolveTheme ? "active" : ""}" data-theme="${evoEsc(t)}">${evoEsc(t)} <span class="chip-n">${cnt[t]}</span></button>`
    ).join("");
  }

  // 该主题信号，按日期正序（从早到晚）
  const allItems = state.leaderItems
    .filter((s) => s.theme === state.evolveTheme && s.date)
    .slice()
    .sort((a, b) => (a.date || "").localeCompare(b.date || ""));
  const totalAdd = allItems.reduce((sum, s) => sum + evoNP(s).length, 0);
  // 「只看有新增」开关：仅保留有新提法的节点
  const items = state.evolveOnlyNew ? allItems.filter((s) => evoNP(s).length) : allItems;

  const nodes = items.map((s, i) => {
    const np = evoNP(s);
    const isLatest = i === items.length - 1;
    const occ = s.headline || s.occasion || "";
    const roleTag = s.role ? `<span class="evo-role">${evoEsc(s.role)}</span>` : "";
    const phrasesHtml = np.length
      ? `<ul class="evo-phrases">${np.map((p) => `<li>${evoEsc(p)}</li>`).join("")}</ul>`
      : '<p class="evo-keep">延续既有表述，无新增提法</p>';
    const cmp = (s.compared_to && s.compared_to.date) ? `<span class="evo-cmp">较 ${evoEsc(s.compared_to.date)} 同主题</span>` : "";
    const src = s.url ? `<a class="evo-src" href="${evoEsc(s.url)}" target="_blank" rel="noopener">原文 ↗</a>` : "";
    return `<li class="evo-node${isLatest ? " is-latest" : ""}">
      <span class="evo-dot"></span>
      <div class="evo-meta"><span class="evo-date">${evoEsc(s.date)}</span>${roleTag}${isLatest ? '<span class="evo-latest">最新</span>' : ""}</div>
      <div class="evo-card">
        <div class="evo-occ">${evoEsc(occ)}</div>
        ${phrasesHtml}
        <div class="evo-foot">${cmp}${src}</div>
      </div>
    </li>`;
  }).join("");

  els.evolveBody.innerHTML = items.length
    ? `<ol class="evo-timeline">${nodes}</ol>`
    : '<p class="evo-empty">该主题在当前筛选下暂无记录。</p>';
  if (els.evolveStat) {
    els.evolveStat.textContent = state.evolveOnlyNew
      ? `「${state.evolveTheme}」· ${items.length} 次有新增 · 累计新增提法 ${totalAdd} 条`
      : `「${state.evolveTheme}」· ${allItems.length} 次出现 · 累计新增提法 ${totalAdd} 条`;
  }
}

/* ============ 月度主题趋势图 ============ */

const THEME_ORDER = ["城市治理", "开放发展", "科技产业", "营商环境", "民生治理", "文化教育", "生态环境", "法治建设"];

function renderTrendChart() {
  const target = document.querySelector("#leaderTrend");
  if (!target) return;
  const items = filteredLeaders();
  if (!items.length) { target.innerHTML = ""; return; }

  // 滚动 12 个月：从最新数据所在月往前推 11 个月，共 12 个月
  const newestYM = (items[0].date || "").slice(0, 7);
  if (!newestYM) { target.innerHTML = ""; return; }
  const [ny, nm] = newestYM.split("-").map((x) => parseInt(x, 10));
  // 计算窗口起点（最新月 - 11 个月）
  let startY = ny, startM = nm - 11;
  while (startM <= 0) { startM += 12; startY -= 1; }
  const startYM = `${startY}-${String(startM).padStart(2, "0")}`;

  const windowItems = items.filter((s) => {
    const ym = (s.date || "").slice(0, 7);
    return ym >= startYM && ym <= newestYM;
  });
  if (!windowItems.length) { target.innerHTML = ""; return; }

  // 按月分组 + 按主题统计
  const byMonth = {};
  windowItems.forEach((s) => {
    const ym = (s.date || "").slice(0, 7);
    if (!byMonth[ym]) byMonth[ym] = { total: 0, themes: {} };
    byMonth[ym].total += 1;
    const t = s.theme || "未分类";
    byMonth[ym].themes[t] = (byMonth[ym].themes[t] || 0) + 1;
  });

  // 滚动 12 个月数组生成（旧→新）
  const months = [];
  let cy = startY, cm = startM;
  for (let i = 0; i < 12; i++) {
    const ym = `${cy}-${String(cm).padStart(2, "0")}`;
    months.push(ym);
    if (!byMonth[ym]) byMonth[ym] = { total: 0, themes: {} };
    cm += 1;
    if (cm > 12) { cm = 1; cy += 1; }
  }
  const yearItems = windowItems;     // 复用下面副标的变量名
  const newestYear = `${startYM} → ${newestYM}`;
  const maxTotal = Math.max(1, ...months.map((m) => byMonth[m].total));

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
        <span class="trend-title">近 12 个月主题热度趋势</span>
        <span class="trend-sub">${filterLabel || `${newestYear} · 共 ${yearItems.length} 条`}</span>
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

  // 收集所有新提法 + 出处。判断"书记/市长"用 role 字段（leader 字段是人名）
  // 用户原则：市委书记讲话变化最高优先，侧栏配额书记 ≥10 / 市长 ≤5
  const phrases = [];
  items.forEach((s) => {
    const isSecretary = (s.role === "市委书记" || s.leader === "陈吉宁");
    (s.new_phrasing || []).forEach((p) => {
      phrases.push({
        text: p, date: s.date,
        leader: s.leader, role: s.role,
        isSecretary,
        theme: s.theme,
      });
    });
  });

  if (!phrases.length) { target.innerHTML = ""; return; }

  // 按日期倒序
  phrases.sort((a, b) => (b.date || "").localeCompare(a.date || ""));

  // 配额：书记池 + 市长池，分别按日期倒序，最后合并按日期再排
  const SEC_QUOTA = 10;   // 书记最多 10 条
  const MAYOR_QUOTA = 5;  // 市长最多 5 条
  const secretaryPool = phrases.filter((p) => p.isSecretary).slice(0, SEC_QUOTA);
  const mayorPool = phrases.filter((p) => !p.isSecretary).slice(0, MAYOR_QUOTA);
  const top = [...secretaryPool, ...mayorPool]
    .sort((a, b) => (b.date || "").localeCompare(a.date || ""));

  const secN = secretaryPool.length;
  const mayN = mayorPool.length;

  // 编号 + 日期 + 提法的紧凑列表
  const listHtml = top.map((p, i) => {
    const dateShort = (p.date || "").slice(5).replace("-", "/");
    const roleLabel = p.isSecretary ? "书记" : "市长";
    const roleClass = p.isSecretary ? "is-secretary" : "is-mayor";
    return `
      <li class="np-side-item ${roleClass}" title="${p.date} · ${p.leader} · ${p.theme || ""}">
        <span class="np-side-num">${String(i + 1).padStart(2, "0")}</span>
        <div class="np-side-body">
          <div class="np-side-text">${p.text}</div>
          <div class="np-side-meta">${dateShort} · ${roleLabel}</div>
        </div>
      </li>
    `;
  }).join("");

  target.innerHTML = `
    <aside class="phrase-side">
      <div class="phrase-side-head">
        <span class="phrase-side-eyebrow">近期新提法</span>
        <span class="phrase-side-sub">书记 ${secN} 条 · 市长 ${mayN} 条 / 共 ${phrases.length} 条</span>
      </div>
      <ol class="phrase-side-list">${listHtml}</ol>
    </aside>
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
  const items = filteredLeaders().sort((a, b) => (b.date || "").localeCompare(a.date || ""));

  if (els.leaderStat) {
    els.leaderStat.textContent = `${items.length} / ${state.leaderItems.length} 条`;
  }

  if (!items.length) {
    els.leaderTimeline.innerHTML = `<p class="empty-tip">没有匹配的条目，调整筛选试试。</p>`;
    return;
  }

  // 以筛选结果的最新一条为锚点，向前推 7 天（一周窗）作为"近期"区
  // 一周内若超过 6 条，按"领导级别+反复提法+场合级别"打分取 Top 6
  const RECENT_MAX = 6;
  const newestDate = items[0].date || "";
  const recentCutoff = shiftDate(newestDate, -7);
  const HIGH_OCC = /全会|常委会扩大|常委会|动员|推进会|部署|启动|开幕/;
  const MID_OCC = /调研|座谈|现场办公|审计/;
  const newestMs = newestDate ? new Date(newestDate + "T00:00:00").getTime() : 0;
  const recencyBoost = (date) => {
    if (!date || !newestMs) return 0;
    const days = Math.round((newestMs - new Date(date + "T00:00:00").getTime()) / 86400000);
    if (days <= 0) return 7;
    if (days === 1) return 5;
    if (days === 2) return 3;
    if (days === 3) return 1;
    return 0;
  };
  const scoreRecent = (s) => {
    let score = 0;
    if (s.leader === "陈吉宁" || s.role === "市委书记") score += 8;
    else if (s.leader === "龚正" || s.role === "市长") score += 5;
    const occ = (s.occasion || "") + (s.title || "") + (s.headline || "");
    if (HIGH_OCC.test(occ)) score += 5;
    else if (MID_OCC.test(occ)) score += 3;
    else score += 1;
    const np = s.new_phrasing || [];
    score += np.length * 2;
    np.forEach((p) => { if ((state.phraseCounts[(p || "").trim()] || 0) >= 2) score += 5; });
    if (s.policy_implications && s.policy_implications.length > 20) score += 3;
    score += recencyBoost(s.date);
    return score;
  };
  const recentAll = items.filter((s) => (s.date || "") >= recentCutoff);
  const recentSorted = [...recentAll].sort((a, b) => scoreRecent(b) - scoreRecent(a));
  const recent = recentSorted.slice(0, RECENT_MAX).sort((a, b) => (b.date || "").localeCompare(a.date || ""));
  const recentExtra = recentSorted.length - recent.length;
  // earlier = 一周外 的 + 一周内被裁掉的低分项（一并入历史折叠）
  const recentKeptUrls = new Set(recent.map((x) => x.url));
  const earlier = items.filter((s) => !recentKeptUrls.has(s.url) && (s.date || "") < recentCutoff)
    .concat(recentAll.filter((s) => !recentKeptUrls.has(s.url)));

  // 近期区：直接平铺（按日期降序）
  const recentLabel = `${formatDateShort(recentCutoff)} → ${formatDateShort(newestDate)}`;
  const recentSuffix = recentExtra > 0 ? `（按关注度 Top ${RECENT_MAX} · 另 ${recentExtra} 条已折叠到历史）` : "";
  const recentHTML = `
    <div class="recent-block">
      <div class="recent-head">
        <span class="recent-title">近一周 · ${recent.length} 条${recentSuffix}</span>
        <span class="recent-range">${recentLabel}</span>
      </div>
      <div class="recent-list">
        ${recent.map(renderLeaderCardHTML).join("")}
      </div>
    </div>
  `;

  // 历史区：所有 14 天前的活动塞进一个总折叠，里面按月分组（每月一个小标题，平铺不再嵌套折叠）
  let earlierHTML = "";
  if (earlier.length) {
    const groups = {};
    earlier.forEach((s) => {
      const ym = (s.date || "").slice(0, 7);
      (groups[ym] = groups[ym] || []).push(s);
    });
    const ymsDesc = Object.keys(groups).sort().reverse();
    const monthsHTML = ymsDesc.map((ym) => {
      const list = groups[ym];
      const ymLabel = ym.replace("-", "年") + "月";
      return `
        <section class="month-block">
          <h4 class="month-block-head">
            <span>${ymLabel}</span>
            <span class="month-block-count">${list.length} 条</span>
          </h4>
          <div class="month-block-list">${list.map(renderLeaderCardHTML).join("")}</div>
        </section>
      `;
    }).join("");

    earlierHTML = `
      <details class="earlier-fold">
        <summary class="earlier-summary">
          <span class="earlier-chev">▸</span>
          <span class="earlier-label">查看更早历史</span>
          <span class="earlier-count">${earlier.length} 条 · ${ymsDesc.length} 个月</span>
        </summary>
        <div class="earlier-content">${monthsHTML}</div>
      </details>
    `;
  }

  els.leaderTimeline.innerHTML = recentHTML + earlierHTML;
}

// 日期工具：YYYY-MM-DD ± n 天
function shiftDate(dateStr, days) {
  if (!dateStr) return dateStr;
  const d = new Date(dateStr + "T00:00:00");
  d.setDate(d.getDate() + days);
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}
function formatDateShort(dateStr) {
  if (!dateStr) return "";
  return dateStr.slice(5).replace("-", "/");
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
  if (!els.focusGrid) return;
  els.focusGrid.innerHTML = "";

  // eyebrow：本周 + 当前日期
  const now = new Date();
  const md = `${now.getMonth() + 1} 月 ${now.getDate()} 日`;

  // 真信号驱动：从近 7 天 state.leaderItems 自动评出 Top 3
  // state.leaderItems 还没就绪时，回退到旧的静态 topic 模式
  if (!state.leaderItems.length) {
    els.focusEyebrow.textContent = `本周关注 · ${md} · 加载中…`;
    return;
  }

  if (els.focusEyebrow) {
    els.focusEyebrow.textContent = `本周关注 · ${md} · 基于近 7 天市委活动自动研判`;
  }

  // 近 7 天的活动
  const newest = state.leaderItems[0].date || "";
  const d = new Date(newest + "T00:00:00");
  d.setDate(d.getDate() - 7);
  const cutoff = d.toISOString().slice(0, 10);
  const recent = state.leaderItems.filter((s) => (s.date || "") >= cutoff);

  if (!recent.length) {
    els.focusGrid.innerHTML = `<p class="empty-tip">近 7 天暂无收录的市委活动。</p>`;
    return;
  }

  // 打分逻辑：市委书记权重最高 + 反复提法加权 + 政策启示加分 + 高优先场合加分 + 新鲜度加分
  const HIGH_OCC = /全会|常委会扩大|常委会|动员|推进会|部署|启动|开幕/;
  const MID_OCC = /调研|座谈|现场办公|审计/;
  // 新鲜度衰减：越靠近 newest 日期分越高（避免最新动态被高优先级老条目压住）
  const newestMs = newest ? new Date(newest + "T00:00:00").getTime() : 0;
  const recencyBoost = (date) => {
    if (!date || !newestMs) return 0;
    const days = Math.round((newestMs - new Date(date + "T00:00:00").getTime()) / 86400000);
    if (days <= 0) return 7;   // 当天
    if (days === 1) return 5;  // 1 天前
    if (days === 2) return 3;  // 2 天前
    if (days === 3) return 1;  // 3 天前
    return 0;                  // 4 天以上
  };
  const scored = recent.map((s) => {
    let score = 0;
    if (s.leader === "陈吉宁" || s.role === "市委书记") score += 8;
    else if (s.leader === "龚正" || s.role === "市长") score += 5;
    const occ = (s.occasion || "") + (s.title || "") + (s.headline || "");
    if (HIGH_OCC.test(occ)) score += 5;
    else if (MID_OCC.test(occ)) score += 3;
    else score += 1; // 会见外事兜底
    const np = s.new_phrasing || [];
    score += np.length * 2;
    // 反复 ≥2 次的提法每条额外 +5（说明已成市委话语体系）
    let repeatBoost = 0;
    np.forEach((p) => {
      const c = state.phraseCounts[(p||"").trim()] || 0;
      if (c >= 2) repeatBoost += 5;
    });
    score += repeatBoost;
    if (s.policy_implications && s.policy_implications.length > 20) score += 3;
    // 新鲜度加分：让最新一天的活动至少有机会进 Top 3
    score += recencyBoost(s.date);
    return { ...s, _score: score, _repeatBoost: repeatBoost };
  });

  // 同主题最多保留 2 条（避免 Top 3 全是同主题）
  const top3 = [];
  const themeCount = {};
  scored.sort((a, b) => b._score - a._score);
  for (const x of scored) {
    const t = x.theme || "未分类";
    if ((themeCount[t] || 0) >= 2) continue;
    themeCount[t] = (themeCount[t] || 0) + 1;
    top3.push(x);
    if (top3.length === 3) break;
  }

  top3.forEach((sig, idx) => {
    const card = document.createElement("article");
    card.className = "focus-card";
    card.tabIndex = 0;
    const roleShort = (sig.role === "市委书记" || sig.leader === "陈吉宁") ? "书记" : "市长";
    const dateShort = (sig.date || "").slice(5).replace("-", "/");
    const np = sig.new_phrasing || [];
    // 找出 反复 ≥2 次的提法（"已立住的"）
    const repeatedPhrases = np.filter((p) => (state.phraseCounts[(p||"").trim()] || 0) >= 2);
    const newPhrases = np.filter((p) => (state.phraseCounts[(p||"").trim()] || 0) < 2).slice(0, 2);

    const phraseSection = (repeatedPhrases.length || newPhrases.length) ? `
      <ul class="focus-phrases">
        ${repeatedPhrases.slice(0,2).map((p) => `
          <li class="phrase-repeat">
            <span class="phrase-tag">已立住 ${state.phraseCounts[p.trim()]}×</span>
            <span class="phrase-text">${p}</span>
          </li>
        `).join("")}
        ${newPhrases.map((p) => `
          <li class="phrase-new">
            <span class="phrase-tag">新提法</span>
            <span class="phrase-text">${p}</span>
          </li>
        `).join("")}
      </ul>
    ` : "";

    card.innerHTML = `
      <span class="rank">${String(idx + 1).padStart(2, "0")}</span>
      <span class="theme">${sig.theme || ""}</span>
      <div class="focus-meta-top">
        <span class="focus-date">${dateShort}</span>
        <span class="focus-role">${roleShort}</span>
        <span class="focus-occasion">${sig.occasion || ""}</span>
      </div>
      <h3>${sig.headline || sig.title || "—"}</h3>
      ${phraseSection}
      ${sig.policy_implications ? `
        <div class="focus-implications">
          <span class="impl-label">民盟切入</span>
          <p>${sig.policy_implications}</p>
        </div>
      ` : ""}
      <div class="focus-meta-bot">
        <span class="score-chip">关注度 ${sig._score}</span>
        ${sig.url ? `<a href="${sig.url}" target="_blank" rel="noreferrer" class="focus-link">查看来源 →</a>` : ""}
      </div>
    `;
    // 点击/回车：滚到时间轴近一周区
    const scrollToTimeline = () => {
      document.getElementById("leaders")?.scrollIntoView({ behavior: "smooth", block: "start" });
    };
    card.addEventListener("click", scrollToTimeline);
    card.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") { e.preventDefault(); scrollToTimeline(); }
    });
    els.focusGrid.append(card);
  });
}

/* ------------------------------------------------------------
   渲染：切口孵化库
   ------------------------------------------------------------ */

/* ===== 切口台账：本地标记（采纳/已转化/搁置）+ 导出研究简报 ===== */
const LEDGER_KEY = "cz_cut_ledger_v1";
const CUT_STATUSES = ["采纳", "已转化", "搁置"];
function cutLedger() { try { return JSON.parse(localStorage.getItem(LEDGER_KEY)) || {}; } catch (e) { return {}; } }
function setCutStatus(id, st) {
  const m = cutLedger();
  if (st) m[id] = st; else delete m[id];
  try { localStorage.setItem(LEDGER_KEY, JSON.stringify(m)); } catch (e) {}
}
function statusKey(s) { return ({ "采纳": "adopt", "已转化": "done", "搁置": "hold" })[s] || ""; }

function exportCutMd(topic) {
  if (!topic) return;
  const L = [];
  L.push(`# 研究切口简报：${topic.cut || topic.title || ""}`, "");
  L.push(`- 主题：${topic.theme || ""}`);
  L.push(`- 核心提法：${topic.phrase || ""}（反复 ${topic.count || 1}× · 首发 ${topic.first_date || ""} ${topic.first_occasion || ""} · 至 ${topic.last_date || ""}）`);
  const st = cutLedger()[topic.id];
  if (st) L.push(`- 台账状态：${st}`);
  L.push("", "## 研判", topic.thesis || "", "", "## 切入点", topic.cut || "");
  if ((topic.mechanism || []).length) { L.push("", "## 机制层抓手"); topic.mechanism.forEach((m) => L.push(`- ${m}`)); }
  if ((topic.verification || []).length) { L.push("", "## 核验路径"); topic.verification.forEach((v) => L.push(`- ${v}`)); }
  const sl = topic.signal_links || [];
  if (sl.length) {
    L.push("", "## 证据链（来源信号）");
    sl.forEach((s) => {
      if (s && typeof s === "object") L.push(`- ${[s.date, s.headline || s.title, s.url].filter(Boolean).join(" · ")}`);
      else L.push(`- ${s}`);
    });
  }
  L.push("", "---", `导出自 CZ Agent · 切口 ${topic.id}`);
  const blob = new Blob([L.join("\n")], { type: "text/markdown;charset=utf-8" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = `切口-${(topic.phrase || topic.id || "").slice(0, 20)}.md`;
  document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}

function buildCutCardEl(topic, isAuto) {
  const card = document.createElement("article");
  card.className = "cut-card";
  const status = cutLedger()[topic.id] || "";
  if (status) card.dataset.status = statusKey(status);
  const mechChips = (topic.mechanism || [])
    .map((m) => `<span class="mech-chip">${m}</span>`)
    .join("");

  // 工作台已隐藏 → 不再展示"进入工作台"按钮，只保留指标
  let footHtml;
  if (isAuto) {
    const sources = topic.signal_links?.length || 0;
    const firstShort = (topic.first_date || "").slice(5).replace("-", "/");
    footHtml = `
      <span class="cut-score">
        <span class="count-pill">反复 ${topic.count || 1}×</span>
        <span class="first-date">首发 ${firstShort}</span>
        <span class="src-count">${sources} 条来源</span>
      </span>
    `;
  } else {
    footHtml = `
      <span class="cut-score">热度 <strong>${topic._score || ""}</strong> · ${topic._matched || 0} 信号</span>
    `;
  }
  const kwChips = isAuto && (topic.keywords || []).length
    ? `<div class="cut-keywords">${topic.keywords.slice(0, 5).map((k) => `<span class="kw-chip">${k}</span>`).join("")}</div>`
    : "";

  const statusTag = status ? `<span class="ledger-tag t-${statusKey(status)}">${status}</span>` : "";
  const ledgerBtns = ["待办", ...CUT_STATUSES].map((s) => {
    const val = s === "待办" ? "" : s;
    const on = (val === status) ? " on" : "";
    return `<button class="ledger-btn${on}" data-act="status" data-cut="${topic.id}" data-status="${val}">${s}</button>`;
  }).join("");
  const ledgerHtml = isAuto ? `
    <div class="cut-ledger">
      <div class="ledger-btns">${ledgerBtns}</div>
      <button class="cut-export" data-act="export" data-cut="${topic.id}">⤓ 导出简报</button>
    </div>` : "";

  card.innerHTML = `
    <div class="cut-head">
      <div>
        <div class="cut-theme">${topic.theme}${statusTag}</div>
        <h3>${topic.cut}</h3>
      </div>
    </div>
    <p class="cut-thesis">${topic.thesis}</p>
    <div class="mechanism">${mechChips}</div>
    ${kwChips}
    <div class="cut-foot">${footHtml}</div>
    ${ledgerHtml}
  `;
  return card;
}

function renderLedgerFilter() {
  const box = document.getElementById("ledgerFilter");
  if (!box) return;
  const led = cutLedger();
  const counts = { all: 0, 待办: 0, 采纳: 0, 已转化: 0, 搁置: 0 };
  (state.cuts || []).forEach((c) => { counts.all++; counts[led[c.id] || "待办"]++; });
  const opts = [["all", "全部"], ["采纳", "采纳"], ["已转化", "已转化"], ["搁置", "搁置"], ["待办", "待办"]];
  box.innerHTML = opts.map(([v, label]) =>
    `<button class="ledger-fchip${state.cutStatus === v ? " on" : ""}" data-lstatus="${v}">${label} <span class="lf-n">${counts[v] || 0}</span></button>`
  ).join("");
}

function renderCuts() {
  if (!els.cutGrid) return;
  renderLedgerFilter();
  const query = state.search.toLowerCase().trim();
  const led = cutLedger();
  const topics = filteredTopics().filter((t) => {
    if (state.cutStatus !== "all" && (led[t.id] || "待办") !== state.cutStatus) return false;
    if (!query) return true;
    return [t.title, t.cut, t.theme, t.thesis, ...(t.keywords || [])]
      .join(" ").toLowerCase().includes(query);
  });

  els.cutGrid.innerHTML = "";
  if (!topics.length) {
    els.cutGrid.innerHTML = `<p style="color:var(--ink-mute);font-size:14px;padding:24px 0;">没有匹配的切口，试试别的关键词。</p>`;
    return;
  }

  const isAuto = state.cuts.length > 0;
  const filterActive = state.selectedTheme !== "all" || !!query || state.cutStatus !== "all";

  // 筛选/搜索激活 或 总量很少 → 直接平铺
  if (filterActive || topics.length <= 8) {
    topics.forEach((t) => els.cutGrid.append(buildCutCardEl(t, isAuto)));
    return;
  }

  // 默认模式：Top 6 高频反复 + 其余按主题折叠
  const TOP_N = 6;
  const featured = topics.slice(0, TOP_N);
  const rest = topics.slice(TOP_N);

  // 1) 高频精选区
  const featSection = document.createElement("section");
  featSection.className = "cut-section cut-section-featured";
  featSection.innerHTML = `
    <h3 class="cut-section-head">
      <span class="section-icon">★</span>
      <span class="section-title">高频反复 · Top ${featured.length}</span>
      <span class="section-sub">市委话语体系中累计反复最多的核心切口</span>
    </h3>
    <div class="cut-section-grid"></div>
  `;
  const featGrid = featSection.querySelector(".cut-section-grid");
  featured.forEach((t) => featGrid.append(buildCutCardEl(t, isAuto)));
  els.cutGrid.append(featSection);

  // 2) 其余按主题分组折叠
  const byTheme = {};
  rest.forEach((t) => {
    const k = t.theme || "未分类";
    (byTheme[k] = byTheme[k] || []).push(t);
  });
  // 按组内最高 count 倒序
  const themeOrder = Object.keys(byTheme).sort((a, b) => {
    const ma = Math.max(...byTheme[a].map((x) => x.count || 0));
    const mb = Math.max(...byTheme[b].map((x) => x.count || 0));
    return mb - ma;
  });

  if (rest.length) {
    const restWrap = document.createElement("div");
    restWrap.className = "cut-rest-wrap";
    restWrap.innerHTML = `<div class="cut-rest-head">按主题查看其他切口（共 ${rest.length} 条 · 默认收起）</div>`;
    themeOrder.forEach((theme) => {
      const list = byTheme[theme];
      const det = document.createElement("details");
      det.className = "cut-theme-fold";
      det.innerHTML = `
        <summary class="cut-theme-summary">
          <span class="theme-chev">▸</span>
          <span class="theme-name">${theme}</span>
          <span class="theme-count">${list.length} 条切口</span>
          <span class="theme-top">Top 反复 ${Math.max(...list.map((x) => x.count || 0))}×</span>
        </summary>
        <div class="cut-section-grid"></div>
      `;
      const grid = det.querySelector(".cut-section-grid");
      list.forEach((t) => grid.append(buildCutCardEl(t, isAuto)));
      restWrap.append(det);
    });
    els.cutGrid.append(restWrap);
  }
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
  const pool = activeTopicPool();
  const topic = pool.find((t) => t.id === state.selectedTopicId) || pool[0];
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

  // 右侧：骨架 + LLM 完整初稿
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

  // AI 初稿（如果该切口已生成）
  const draftBundle = state.drafts[topic.id];
  const aiDraft = draftBundle ? draftBundle[state.selectedOutput] : null;
  const fullDraftHtml = aiDraft && !aiDraft.error
    ? renderAIDraftHTML(state.selectedOutput, aiDraft)
    : (draftBundle ? "" : `
        <div class="ai-draft-pending">
          <span class="pending-icon">⌛</span>
          <span>该切口的完整初稿尚未生成，每天 2 次定时任务会自动补齐。</span>
        </div>
      `);

  els.draftPanel.innerHTML = `
    <div class="pane-theme">${OUTPUT_LABEL[state.selectedOutput]} · 骨架</div>
    <div class="draft-title">${out.title}</div>
    ${blocks}
    ${fullDraftHtml}
  `;

  // 绑定复制按钮
  const copyBtn = els.draftPanel.querySelector(".ai-draft-copy");
  if (copyBtn) {
    copyBtn.addEventListener("click", () => {
      const txt = copyBtn.dataset.text || "";
      navigator.clipboard.writeText(txt).then(() => {
        copyBtn.textContent = "已复制 ✓";
        setTimeout(() => { copyBtn.textContent = "复制全文"; }, 1500);
      });
    });
  }
}

function renderAIDraftHTML(kind, d) {
  // 根据档位组装可读的初稿正文
  let title = d.title || "—";
  let plain = "";
  let html = "";
  if (kind === "brief") {
    plain = `${title}\n\n${d.body || ""}`;
    html = `
      <h4 class="ai-draft-title">${title}</h4>
      <div class="ai-draft-body">${(d.body || "").split(/\n\n+/).map(p => `<p>${p}</p>`).join("")}</div>
    `;
  } else if (kind === "proposal") {
    plain = `【提案】${title}\n\n【案由】${d.by_who || ""}\n\n【主要问题】${d.problems || ""}\n\n【建议措施】${d.measures || ""}\n\n【评估与公开】${d.evaluation || ""}`;
    html = `
      <h4 class="ai-draft-title">${title}</h4>
      <div class="ai-draft-section"><span class="sec-label">案由</span><p>${d.by_who || ""}</p></div>
      <div class="ai-draft-section"><span class="sec-label">主要问题</span><div>${(d.problems || "").split(/\n\n+/).map(p => `<p>${p}</p>`).join("")}</div></div>
      <div class="ai-draft-section"><span class="sec-label">建议措施</span><div>${(d.measures || "").split(/\n\n+/).map(p => `<p>${p}</p>`).join("")}</div></div>
      <div class="ai-draft-section"><span class="sec-label">评估与公开</span><p>${d.evaluation || ""}</p></div>
    `;
  } else { // research
    plain = `【课题】${title}\n\n【研究问题】${d.research_question || ""}\n\n【研究意义】${d.significance || ""}\n\n【调研路径】${d.approach || ""}\n\n【关键变量识别】${d.key_variables || ""}\n\n【成果结构】${d.outline || ""}`;
    html = `
      <h4 class="ai-draft-title">${title}</h4>
      <div class="ai-draft-section"><span class="sec-label">研究问题</span><p>${d.research_question || ""}</p></div>
      <div class="ai-draft-section"><span class="sec-label">研究意义</span><p>${d.significance || ""}</p></div>
      <div class="ai-draft-section"><span class="sec-label">调研路径</span><p>${d.approach || ""}</p></div>
      <div class="ai-draft-section"><span class="sec-label">关键变量</span><p>${d.key_variables || ""}</p></div>
      <div class="ai-draft-section"><span class="sec-label">成果结构</span><p>${d.outline || ""}</p></div>
    `;
  }
  const escaped = plain.replace(/&/g, "&amp;").replace(/"/g, "&quot;");
  return `
    <details class="ai-draft-wrap">
      <summary class="ai-draft-summary">
        <span class="ai-tag">AI 初稿</span>
        <span class="ai-summary-title">点开查看完整初稿（${plain.length} 字 · 待人工核验）</span>
        <span class="ai-chev">▸</span>
      </summary>
      <div class="ai-draft-content">
        <div class="ai-draft-warn">
          ⚠️ 本初稿由模型基于骨架自动生成，仅供起草参考。文中数字、案例、机构名 需要在落稿前由参政议政干部逐一核实。
        </div>
        ${html}
        <div class="ai-draft-actions">
          <button type="button" class="ai-draft-copy" data-text="${escaped}">复制全文</button>
        </div>
      </div>
    </details>
  `;
}

function renderSources() { /* 已下线：原『数据来源』section 已从首页移除 */ }

/* ------------------------------------------------------------
   渲染：页脚元数据
   ------------------------------------------------------------ */

function renderMeta() {
  const cutCount = state.cuts.length || DATA.topics.length;
  const leaderCount = state.leaderItems.length;
  els.metaSignals.textContent = leaderCount
    ? `${leaderCount} 条领导动态`
    : `${DATA.signals.length} 条公开信号`;
  els.metaTopics.textContent = `${cutCount} 个候选切口${state.cuts.length ? "（自动生成）" : ""}`;
}

/* ------------------------------------------------------------
   渲染：主题筛选下拉
   ------------------------------------------------------------ */

function renderThemeFilter() {
  if (!els.themeFilter) return;
  // 重渲染时清空（保留 "全部专题"）
  while (els.themeFilter.options.length > 1) els.themeFilter.remove(1);
  const pool = activeTopicPool();
  const themes = [...new Set(pool.map((t) => t.theme))];
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
  // 提法流变主题切换
  if (els.evolveThemes) {
    els.evolveThemes.addEventListener("click", (e) => {
      const btn = e.target.closest("button[data-theme]");
      if (!btn) return;
      state.evolveTheme = btn.dataset.theme;
      renderEvolution();
    });
  }
  // 切口台账：标记 / 导出（事件委托）
  if (els.cutGrid) {
    els.cutGrid.addEventListener("click", (e) => {
      const b = e.target.closest("[data-act]");
      if (!b) return;
      const id = b.dataset.cut;
      if (b.dataset.act === "status") {
        setCutStatus(id, b.dataset.status || "");
        renderCuts();
      } else if (b.dataset.act === "export") {
        exportCutMd((state.cuts || []).find((c) => String(c.id) === String(id)));
      }
    });
  }
  // 切口台账状态筛选
  const ledgerFilter = document.getElementById("ledgerFilter");
  if (ledgerFilter) {
    ledgerFilter.addEventListener("click", (e) => {
      const b = e.target.closest("[data-lstatus]");
      if (!b) return;
      state.cutStatus = b.dataset.lstatus;
      renderCuts();
    });
  }
  // 「只看有新增」开关
  if (els.evolveOnlyNew) {
    els.evolveOnlyNew.addEventListener("click", () => {
      state.evolveOnlyNew = !state.evolveOnlyNew;
      els.evolveOnlyNew.classList.toggle("active", state.evolveOnlyNew);
      els.evolveOnlyNew.setAttribute("aria-pressed", String(state.evolveOnlyNew));
      renderEvolution();
    });
  }
  // 提法生命周期筛选
  if (els.lifeFilter) {
    els.lifeFilter.addEventListener("click", (e) => {
      const btn = e.target.closest("button[data-life]");
      if (!btn) return;
      state.lifeFilter = btn.dataset.life;
      renderLifecycle();
    });
  }
}

/* ------------------------------------------------------------
   启动
   ------------------------------------------------------------ */

/* ============ 动向速递卡片 ============ */
async function renderBrief() {
  const card = document.getElementById("briefCard");
  if (!card) return;
  let b;
  try {
    const r = await fetch("./data/brief_latest.json", { cache: "no-store" });
    if (!r.ok) return;
    b = await r.json();
  } catch (e) { return; }
  if (!b || !b.summary) return;
  const dEl = document.getElementById("briefDate");
  const sEl = document.getElementById("briefSummary");
  const iEl = document.getElementById("briefItems");
  if (dEl) dEl.textContent = (b.date || "") + (b.generated_at ? " · 更新 " + b.generated_at : "");
  if (sEl) sEl.textContent = b.summary;
  const items = (b.today && b.today.items) || [];
  if (iEl) {
    iEl.innerHTML = items.map((i) => {
      const role = i.role ? `<span class="bi-role">${evoEsc(i.role)}</span>` : "";
      const ph = (i.phrases || []).map((p) => `<li>${evoEsc(p)}</li>`).join("");
      const head = i.url
        ? `<a class="bi-head" href="${evoEsc(i.url)}" target="_blank" rel="noopener">${evoEsc(i.headline)} ↗</a>`
        : `<span class="bi-head">${evoEsc(i.headline)}</span>`;
      return `<div class="brief-item">
        <div class="bi-top">${role}<span class="bi-theme">${evoEsc(i.theme)}</span></div>
        ${head}
        ${ph ? `<ul class="bi-phrases">${ph}</ul>` : '<p class="bi-keep">无新增提法</p>'}
      </div>`;
    }).join("") || '<p class="bi-keep">当日无新增信号。</p>';
  }
  card.hidden = false;
}

function init() {
  renderThemeFilter();
  renderBrief();
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
