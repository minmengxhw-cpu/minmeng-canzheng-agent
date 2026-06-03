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
};

/* ------------------------------------------------------------
   元素引用
   ------------------------------------------------------------ */

const $ = (sel) => document.querySelector(sel);

const els = {
  themeFilter: $("#themeFilter"),
  leaderStream: $("#leaderStream"),
  leadersEyebrow: $("#leadersEyebrow"),
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
  const items = (await loadLeaderSignals())
    .sort((a, b) => {
      // 优先级：rank（书记 1 > 市长 2 > 其他）→ date 倒序
      const r = (a.role_rank || 9) - (b.role_rank || 9);
      if (r !== 0) return r;
      return (b.date || "").localeCompare(a.date || "");
    });

  if (!els.leaderStream) return;
  els.leaderStream.innerHTML = "";

  // eyebrow：含最近更新时间
  if (items.length && els.leadersEyebrow) {
    const latest = items[0].date;
    els.leadersEyebrow.textContent = `市委关注 · 最高优先 · 最近更新 ${latest}`;
  }

  items.forEach((sig) => {
    const card = document.createElement("article");
    card.className = `leader-card rank-${sig.role_rank || 3}`;
    const kwHtml = (sig.keywords || [])
      .map((k) => `<span class="kw-chip">${k}</span>`)
      .join("");
    const changeBlock = sig.change_note
      ? `
        <div class="leader-change">
          <div class="change-label">表述变化</div>
          <p class="change-note">${sig.change_note}</p>
          ${
            sig.compared_to
              ? `<p class="change-compared">对比 ${sig.compared_to.date}：${sig.compared_to.headline}</p>`
              : ""
          }
        </div>
      `
      : "";

    const sourceKindTag = sig._source_kind === "real"
      ? `<span class="src-kind src-real">实抓</span>`
      : `<span class="src-kind src-mock">演示</span>`;
    card.innerHTML = `
      <div class="leader-meta">
        <span class="leader-name">${sig.leader} ${sourceKindTag}</span>
        <span class="leader-role">${sig.role}</span>
        <span class="leader-date">${sig.date}</span>
        <span class="leader-occasion">${sig.occasion || ""}</span>
      </div>
      <div class="leader-body">
        <h3 class="leader-headline">${sig.headline}</h3>
        <p class="leader-summary">${sig.summary || ""}</p>
        ${changeBlock}
        <div class="leader-foot">
          ${kwHtml}
          <a href="${sig.url}" target="_blank" rel="noreferrer">查看来源 →</a>
        </div>
      </div>
    `;
    els.leaderStream.append(card);
  });
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
