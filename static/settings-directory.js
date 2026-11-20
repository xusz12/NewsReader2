import { $, $$, escapeHTML as e, singlePane } from "./ui.js";

export const settingsPages = {
  collection: {title:"采集与处理", first:"pipeline"},
  research: {title:"研读与助手", first:"catchup"},
  services: {title:"API 与运行环境", first:"deepseek"},
  application: {title:"阅读偏好与维护", first:"appearance"},
};

const entries = [
  ["pipeline", "collection", "采集排期", "collectionSchedule", "collection-schedule", "自动 定时 北京时间 每天 交易日 A股 立即采集 小时 分钟"],
  ["x", "collection", "X 账号订阅", "xAccountSettings", "collection-x-accounts", "来源 信源 抓取 twitter 推特 添加账号 移除账号 帖子 评论 总结 翻译"],
  ["translation", "collection", "正文翻译", "translationRouting", "collection-translation", "翻译并发 上限 首选 DeepSeek OpenCode Go 切换 API Pi 后备 重试"],
  ["classification", "collection", "自动分类与判断", "classificationSettings", "services-decisions", "新闻分类 判断服务 仅未读 并发 备用 Jev OpenAI Decisions 主题归属"],
  ["progress", "collection", "分类进度", "classificationSettingsProgress", "services-decisions", "队列 覆盖率 失败 重试 处理中"],
  ["catchup", "research", "新闻总结", "summaryPreferences", "research-summary", "总结范围 最多自然日 未读条数 限制 总结模型 Pi Codex 推理强度"],
  ["schedules", "research", "日报与周月报", "scheduleSettings", "research-reports", "报告排期 报告生成 早报 午报 晚报 开始生成 新闻窗口 周报 月报 暂停定时"],
  ["report-models", "research", "报告模型", "reportModelSettings", "research-reports", "日报模型 早报模型 午报模型 晚报模型 周报模型 月报模型 推理强度 Pi Codex"],
  ["report-status", "research", "报告运行状态", "reportTaskStatus", "research-reports", "报告任务 调度 队列 失败 交易日历"],
  ["topics", "research", "主题追踪", "topicModelSettings", "services-models", "主题模型 主题归属 重要进展 推理强度 Pi Codex"],
  ["models", "research", "文章问答", "modelSettings", "services-models", "Chat Agent 文章助手 对话模型 临时会话保留时间 TTL Pi 后备模型 Codex 推理强度"],
  ["personal-assistant", "research", "个人助手", "personalAssistantSettings", "research-assistant", "身份 名字 职责 工作区 独立模型 推理强度"],
  ["memory", "research", "助手长期记忆", "personalMemorySettings", "research-assistant", "编辑记忆 删除记忆 记忆文件 MEMORY.md"],
  ["deepseek", "services", "API 密钥与连接", "translationSettings", "services-api", "API key 钥匙串 密钥 服务商 连接测试 获取模型"],
  ["environment", "services", "采集环境检查", "collectionEnvironment", "collection-environment", "opencli Chrome Chromium Browser Bridge 扩展 检查 X 登录 twitter 安装 诊断"],
  ["appearance", "application", "主题与字号", "readingAppearance", "local-appearance", "外观主题 深色 浅色 跟随系统 墨水屏 夜间 暗色 阅读字号 正文字号 字体 大小"],
  ["display", "application", "新闻流显示", "sourceVisibility", "deletion-visibility", "阅读 来源 信源 隐藏 勾选 Reuters Bloomberg X 账号"],
  ["shortcuts", "application", "阅读快捷键", "shortcutSettings", "local-shortcuts", "键盘 底部快捷键栏 按键 恢复默认"],
  ["storage", "application", "数据库位置", "databaseSettings", "local-storage", "SQLite 存储路径 下次启动 文件"],
  ["cleanup", "application", "数据清理", "dataCleanup", "deletion-cleanup", "删除信源 来源数据 清空临时会话 Agent 报告材料快照 清除快照 缓存"],
  ["system", "application", "版本与更新", "systemSettings", "local-updates", "系统 版本记录 升级 检查更新"],
];
const targets = [
  ["deepseek", "测试全部 API 服务", "testAllApisBtn", "测试全部服务 测试所有服务 测试所有API服务 API测试 API连接测试 批量测试 检查所有连接"],
  ["catchup", "新闻总结模型", "summaryModelSettings", "新闻总结模型 摘要 Pi Codex 推理强度"],
  ["report-models", "日报模型（早／午／晚）", "reportDailyModel", "日报模型 早报模型 午报模型 晚报模型"],
  ["report-models", "周报与月报模型", "reportPeriodicModel", "周报模型 月报模型 定期报告"],
  ["models", "临时会话保留时间", "chatRetention", "保留多久 24小时 72小时 TTL"],
  ["classification", "首选与备用判断服务", "decisionPreferences", "首选判断 备用判断 Jev OpenAI Decisions"],
  ["environment", "检查 X 登录", "checkCollectionXBtn", "推特 twitter x登录 账号登录"],
  ["cleanup", "清理来源数据", "sourceCleanup", "删除新闻 信源 删除来源"],
  ["cleanup", "清除报告材料快照", "reportSnapshotSettings", "清理快照 报告快照 材料快照"],
  ["cleanup", "清空临时会话", "sessionCleanup", "清理会话 清除对话 删除聊天 Agent 临时会话"],
  ...[["deepseek","DeepSeek"], ["opencode-go","OpenCode Go"], ["jev","Jev"], ["openai-decisions","OpenAI Decisions"]].map(([id,name]) =>
    ["deepseek", name + " 密钥与连接", "apiTitle-" + id, name + " API Key 密钥 模型 目录 获取 测试 " + (["deepseek","opencode-go"].includes(id) ? "翻译密钥 正文翻译密钥 翻译模型" : "分类密钥 判断密钥 判断模型")]),
];

export const settingsItems = Object.fromEntries(entries.map(([id,page,title,target,help,keywords]) =>
  [id, {id,page,title,target,help:"help-"+help,keywords}]));
settingsItems.help = {id:"help",page:"help",title:"帮助说明",target:"settingsHelp"};
const normalize = text => text.toLowerCase().replace(/[\s／/·_-]/g, "");
const arrow = '<svg class="settings-category-arrow" aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="m9 5 7 7-7 7"/></svg>';

export class SettingsDirectory {
  constructor(host, search, select) {
    this.host = host;
    this.search = search;
    this.select = select;
    host.innerHTML = Object.entries(settingsPages).map(([page, {title}]) => `<section class="nav-section">
      <button type="button" class="settings-category-card" data-settings-page="${page}" aria-controls="settingsOutline-${page}" aria-expanded="false"><strong class="settings-category-copy">${title}</strong><span class="muted" data-unsaved hidden>未保存</span>${arrow}</button>
      <div class="settings-outline" id="settingsOutline-${page}">${Object.values(settingsItems).filter(item => item.page === page).map(item =>
        `<button type="button" class="settings-category-card" id="settingsCategory-${item.id}" data-settings-tab="${item.id}" aria-controls="settingsContent">${item.title}</button>`).join("")}</div></section>`).join("") +
      '<button class="settings-category-card" id="settingsCategory-help" data-settings-tab="help" aria-controls="settingsContent" type="button">帮助说明</button>';
    this.results = $("#settingsSearchResults");
    host.addEventListener("click", event => {
      const page = event.target.closest("[data-settings-page]")?.dataset.settingsPage;
      const item = event.target.closest("[data-settings-tab]")?.dataset.settingsTab;
      if (page && singlePane.matches && !$("#feedView").classList.contains("settings-detail-active")) {
        const expandPage = event.target.closest("[data-settings-page]").getAttribute("aria-expanded") !== "true";
        for (const button of $$("[data-settings-page]", host)) {
          const expanded = expandPage && button.dataset.settingsPage === page;
          button.setAttribute("aria-expanded", String(expanded));
          document.getElementById(button.getAttribute("aria-controls")).hidden = !expanded;
        }
        return;
      }
      if (page || item) select(item || settingsPages[page].first);
    });
    this.results.onclick = event => {
      const result = event.target.closest("[data-result]");
      if (result) select(result.dataset.result, result.dataset.target);
    };
    search.oninput = () => this.filter();
    search.onkeydown = event => {
      if (event.isComposing) return;
      if (event.key === "Escape" && search.value) {
        event.preventDefault(); event.stopPropagation(); search.value = ""; this.filter();
      } else if (["Enter","ArrowDown"].includes(event.key) && search.value.trim()) {
        event.preventDefault();
        const first = $("button", this.results);
        if (event.key === "Enter") first?.click(); else first?.focus();
      }
    };
  }
  show(item) {
    for (const button of $$("[data-settings-page]", this.host)) {
      const active = button.dataset.settingsPage === item.page;
      button.classList.toggle("active", active);
      button.setAttribute("aria-expanded", String(active));
      document.getElementById(button.getAttribute("aria-controls")).hidden = !active;
    }
    for (const button of $$("[data-settings-tab]", this.host)) {
      const active = button.dataset.settingsTab === item.id;
      button.setAttribute("aria-current", String(active));
      button.classList.toggle("active", active);
    }
  }
  filter() {
    const terms = this.search.value.toLowerCase().trim().split(/\s+/).filter(Boolean).map(normalize);
    this.host.hidden = !!terms.length;
    this.results.hidden = !terms.length;
    if (!terms.length) { this.results.replaceChildren(); return; }
    const matches = ([id,title,,keywords]) => {
      const haystack = normalize([title,keywords,settingsPages[settingsItems[id].page].title].join(" "));
      return terms.every(term => haystack.includes(term));
    };
    const rows = targets.filter(matches);
    const covered = new Set(rows.map(([id]) => id));
    for (const [id,,title,target,,keywords] of entries) {
      const entry = [id,title,target,keywords];
      if (!covered.has(id) && matches(entry)) rows.push(entry);
    }
    this.results.innerHTML = rows.map(([id,title,target]) => `<button type="button" class="settings-category-card" data-result="${id}" data-target="${target}"><span class="settings-category-copy"><strong>${e(title)}</strong><span class="muted">${settingsPages[settingsItems[id].page].title}</span></span>${arrow}</button>`).join("") ||
      '<p class="list-message" role="status">没有匹配的设置</p>';
  }
}
