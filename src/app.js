const state = {
  selectedTheme: "all",
  selectedTopicId: window.PLATFORM_DATA.topics[0].id,
  selectedOutput: "brief",
  search: "",
};

const themeFilter = document.querySelector("#themeFilter");
const topicGrid = document.querySelector("#topicGrid");
const signalList = document.querySelector("#signalList");
const signalScope = document.querySelector("#signalScope");
const cutGrid = document.querySelector("#cutGrid");
const cutSearch = document.querySelector("#cutSearch");
const activeCut = document.querySelector("#activeCut");
const draftPanel = document.querySelector("#draftPanel");
const outputTabs = document.querySelector("#outputTabs");
const sourceGrid = document.querySelector("#sourceGrid");

function init() {
  hydrateMetrics();
  renderThemeFilter();
  renderSources();
  renderAll();
  bindEvents();
}

function hydrateMetrics() {
  document.querySelector("#signalCount").textContent = window.PLATFORM_DATA.signals.length;
  document.querySelector("#topicCount").textContent = window.PLATFORM_DATA.topics.length;
  document.querySelector("#sourceCount").textContent = window.PLATFORM_DATA.sources.length;
}

function renderThemeFilter() {
  const themes = [...new Set(window.PLATFORM_DATA.topics.map((topic) => topic.theme))];
  themes.forEach((theme) => {
    const option = document.createElement("option");
    option.value = theme;
    option.textContent = theme;
    themeFilter.append(option);
  });
}

function bindEvents() {
  themeFilter.addEventListener("change", (event) => {
    state.selectedTheme = event.target.value;
    renderAll();
  });

  cutSearch.addEventListener("input", (event) => {
    state.search = event.target.value.trim();
    renderCuts();
  });

  outputTabs.addEventListener("click", (event) => {
    const button = event.target.closest("button[data-output]");
    if (!button) return;
    state.selectedOutput = button.dataset.output;
    outputTabs.querySelectorAll(".pill").forEach((tab) => {
      tab.classList.toggle("active", tab === button);
    });
    renderWorkbench();
  });
}

function renderAll() {
  renderTopics();
  renderSignals();
  renderCuts();
  renderWorkbench();
}

function getVisibleTopics() {
  return window.PLATFORM_DATA.topics.filter((topic) => {
    return state.selectedTheme === "all" || topic.theme === state.selectedTheme;
  });
}

function getVisibleSignals() {
  return window.PLATFORM_DATA.signals.filter((signal) => {
    return state.selectedTheme === "all" || signal.theme === state.selectedTheme;
  });
}

function getMatchedSignals(topic) {
  return window.PLATFORM_DATA.signals.filter((signal) => {
    return topic.keywords.some((keyword) => signal.keywords.includes(keyword));
  });
}

function getTopicScore(topic) {
  const matchedSignals = getMatchedSignals(topic);
  const base = matchedSignals.reduce((total, signal) => total + signal.intensity, 0);
  const levelBonus = matchedSignals.reduce((total, signal) => {
    if (signal.level === "中央") return total + 8;
    if (signal.level === "市级") return total + 6;
    if (signal.level === "部委") return total + 5;
    return total + 3;
  }, 0);
  return Math.min(98, Math.round(base / Math.max(matchedSignals.length, 1) + levelBonus));
}

function renderTopics() {
  const topics = getVisibleTopics();
  topicGrid.innerHTML = "";

  if (!topics.some((topic) => topic.id === state.selectedTopicId) && topics[0]) {
    state.selectedTopicId = topics[0].id;
  }

  topics.forEach((topic) => {
    const score = getTopicScore(topic);
    const matchedSignals = getMatchedSignals(topic);
    const card = document.createElement("article");
    card.className = `topic-card ${topic.id === state.selectedTopicId ? "active" : ""}`;
    card.tabIndex = 0;
    card.innerHTML = `
      <div class="topic-top">
        <h3>${topic.title}</h3>
        <span class="tag">${topic.theme}</span>
      </div>
      <p class="body-copy">${topic.thesis}</p>
      <div class="score-bar" aria-label="关注热度 ${score}">
        <span style="width: ${score}%"></span>
      </div>
      <div class="meta-line">
        <span>关注热度 ${score}</span>
        <span>匹配信号 ${matchedSignals.length}</span>
        <span>${topic.mechanism.slice(0, 2).join(" / ")}</span>
      </div>
    `;
    card.addEventListener("click", () => selectTopic(topic.id));
    card.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        selectTopic(topic.id);
      }
    });
    topicGrid.append(card);
  });
}

function selectTopic(topicId) {
  state.selectedTopicId = topicId;
  renderTopics();
  renderWorkbench();
  document.querySelector("#workbench").scrollIntoView({ block: "start" });
}

function renderSignals() {
  const signals = getVisibleSignals();
  signalList.innerHTML = "";
  signalScope.textContent = state.selectedTheme === "all" ? "全部" : state.selectedTheme;

  signals.forEach((signal) => {
    const item = document.createElement("article");
    item.className = "signal-item";
    item.innerHTML = `
      <h4>${signal.title}</h4>
      <p>${signal.summary}</p>
      <div class="meta-line">
        <span class="tag blue">${signal.level}</span>
        <span>${signal.date}</span>
        <span>${signal.source}</span>
      </div>
      <div class="signal-foot">
        <span>${signal.evidence}</span>
        <a href="${signal.url}" target="_blank" rel="noreferrer">查看来源</a>
      </div>
    `;
    signalList.append(item);
  });
}

function renderCuts() {
  const query = state.search.toLowerCase();
  const topics = getVisibleTopics().filter((topic) => {
    if (!query) return true;
    return [topic.title, topic.cut, topic.theme, topic.thesis, ...topic.keywords]
      .join(" ")
      .toLowerCase()
      .includes(query);
  });

  cutGrid.innerHTML = "";
  topics.forEach((topic) => {
    const score = getTopicScore(topic);
    const card = document.createElement("article");
    card.className = "cut-card";
    card.innerHTML = `
      <div class="cut-top">
        <h3>${topic.cut}</h3>
        <span class="tag ${score > 88 ? "red" : "gold"}">${score}分</span>
      </div>
      <p>${topic.thesis}</p>
      <ul>
        ${topic.mechanism.map((item) => `<li>${item}</li>`).join("")}
      </ul>
      <button type="button">进入成果转化</button>
    `;
    card.querySelector("button").addEventListener("click", () => selectTopic(topic.id));
    cutGrid.append(card);
  });
}

function renderWorkbench() {
  const topic = window.PLATFORM_DATA.topics.find((item) => item.id === state.selectedTopicId);
  if (!topic) return;

  activeCut.innerHTML = `
    <span class="tag">${topic.theme}</span>
    <h3>${topic.cut}</h3>
    <p class="body-copy">${topic.thesis}</p>
    <h4>需核验的问题</h4>
    <ul>
      ${topic.verification.map((item) => `<li>${item}</li>`).join("")}
    </ul>
    <h4>机制拆解</h4>
    <ul>
      ${topic.mechanism.map((item) => `<li>${item}</li>`).join("")}
    </ul>
  `;

  const output = topic.outputs[state.selectedOutput];
  const outputLabel = {
    brief: "社情民意信息",
    proposal: "提案建议",
    research: "参政议政课题",
  }[state.selectedOutput];

  draftPanel.innerHTML = `
    <span class="tag blue">${outputLabel}</span>
    <h3>${output.title}</h3>
    ${output.blocks
      .map(
        ([heading, body]) => `
          <div class="draft-block">
            <h4>${heading}</h4>
            <p class="body-copy">${body}</p>
          </div>
        `
      )
      .join("")}
  `;
}

function renderSources() {
  sourceGrid.innerHTML = "";
  window.PLATFORM_DATA.sources.forEach((source) => {
    const card = document.createElement("article");
    card.className = "source-card";
    card.innerHTML = `
      <div class="source-top">
        <h3>${source.name}</h3>
        <span class="tag">${source.type}</span>
      </div>
      <p>${source.scope}</p>
      <div class="meta-line">
        <span>抓取频率：${source.cadence}</span>
      </div>
      <a href="${source.url}" target="_blank" rel="noreferrer">打开公开来源</a>
    `;
    sourceGrid.append(card);
  });
}

init();
