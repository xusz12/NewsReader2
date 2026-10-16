import { $, api, storage, persist } from "./ui.js";

const RUN_KEY = "newsreader:update-run";
const ACTIVE = new Set(["validating", "fetching", "stopping_old", "merging", "starting_new", "checking_health", "rolling_back"]);
const TERMINAL = new Set(["healthy", "failed", "rolled_back", "rollback_failed"]);
const STAGES = {
  validating: "正在检查更新条件…", fetching: "正在下载目标版本…",
  stopping_old: "正在停止旧服务…", merging: "正在更新应用文件…",
  starting_new: "正在启动新版本…", checking_health: "正在确认新版本可用…",
  rolling_back: "新版本未能启动，正在恢复旧版本…",
  healthy: "更新完成，新版本已通过健康检查。",
  failed: "更新未完成。", rolled_back: "更新失败，已恢复旧版本并重新启动。",
  rollback_failed: "更新失败，自动恢复未完成。请在终端检查项目并重新启动。",
};
const ERRORS = {
  update_repository_not_configured: "尚未配置本项目的更新仓库。",
  update_check_failed: "无法读取 GitHub 版本信息，请检查网络或稍后重试。",
  dirty_worktree: "项目有未提交的修改或未忽略的文件，请先提交或妥善处理后重新检查。",
  detached_head: "项目当前未处于分支，请切换回本项目分支后重新检查。",
  git_state_unavailable: "无法读取 Git 状态，请确认 Git 已安装且项目通过 Git 克隆。",
  managed_launcher_required: "请关闭直接启动的服务，使用「启动NewsReader.command」或 python3 updater.py --serve 启动后更新。",
  expired_check_token: "检查结果已过期，请重新检查更新。",
  missing_check_token: "请先检查更新，再确认目标版本。",
  current_commit_changed: "本地版本已变化，请重新检查更新。",
  update_target_mismatch: "更新目标已变化，请重新检查更新。",
  no_update_available: "没有可用的新版本，请重新检查更新。",
  update_in_progress: "已有更新正在进行，请等待当前更新结束。",
  target_not_descendant: "本地分支与目标版本已分叉，无法自动更新，请在终端处理 Git 分支。",
  tag_commit_mismatch: "远端版本标签已变化，请重新检查更新。",
  target_version_mismatch: "目标版本的版本号或更新源不一致，已停止更新。",
  target_version_manifest_missing: "目标版本缺少有效的版本信息，已停止更新。",
  worktree_changed: "更新期间项目文件发生变化，已停止更新，请重新检查。",
  git_timeout: "Git 操作超时，请检查仓库连接后重新检查更新。",
  health_check_failed: "新版本未通过健康检查。",
  updater_launch_failed: "更新进程未能启动，请重新通过启动器打开应用。",
  update_interrupted: "更新进程意外退出，请检查当前版本后重试。",
};
const explain = error => ERRORS[error?.code || error?.message || error] || "操作未完成，请检查服务、Git 状态和运行依赖后重试。";
const request = (path, options = {}) => api(path, { ...options, signal: AbortSignal.timeout(options.method ? 100000 : path.includes("/check") ? 90000 : 5000) });

export class UpdatePanel {
  constructor({ hasUnsavedChanges }) {
    this.hasUnsavedChanges = hasUnsavedChanges;
    this.sequence = 0;
    this.runId = storage(RUN_KEY, "");
    this.message = "点击「检查更新」获取最新稳定版本。";
  }
  render() {
    if (!$("#updateResult")) return;
    $("#updateResult").textContent = this.message;
    $("#updateResult").setAttribute("aria-busy", String(!!this.running || !!this.checking));
    $("#updateCurrent").textContent = `News Reader ${this.version?.version || $(".brand-version").textContent}`;
    $("#updateRepository").textContent = this.version ? this.version.repository || "尚未配置更新源" : this.data?.repository || "读取更新源中…";
    $("#checkUpdateBtn").disabled = !!this.running || !!this.checking || !!this.runId;
    $("#checkUpdateBtn").textContent = this.checking ? "正在检查…" : "检查更新";
    $("#applyUpdateBtn").hidden = !this.data?.available || !this.data?.can_update || !this.data?.check_token || !!this.running || !!this.checking || !!this.runId;
    $("#refreshUpdateBtn").hidden = !this.refreshReady;
    $("#resumeUpdateBtn").hidden = !this.runId || !!this.running;
  }
  async restore() {
    if (this.restoring || this.running || this.checking) return;
    this.restoring = true;
    const sequence = this.sequence;
    try {
      const [version, status] = await Promise.all([request("/api/version"), request("/api/update/status")]);
      if (sequence !== this.sequence) return;
      this.version = version;
      if (status.run_id && (ACTIVE.has(status.status) || this.runId === status.run_id)) {
        this.data = null;
        this.runId = status.run_id;
        persist(RUN_KEY, this.runId);
        this.poll();
      } else if (!this.runId && TERMINAL.has(status.status)) {
        this.message = `上次更新：${STAGES[status.status]}${status.error ? " " + explain(status.error) : ""}`;
      } else if (this.runId) {
        this.message = "正在恢复更新状态…";
        this.poll();
      }
    } catch {
      if (sequence === this.sequence) this.message = "暂时无法读取服务状态，请重试。";
    } finally {
      this.restoring = false;
      this.render();
    }
  }
  async check() {
    if (this.running || this.checking || this.runId) return;
    this.sequence++;
    this.checking = true;
    this.data = null;
    this.message = "正在检查最新稳定版本…";
    this.render();
    try {
      const data = this.data = await request("/api/update/check?force=1");
      this.version = { version: data.current_version, repository: data.repository };
      this.message = data.enabled === false ? data.message
        : data.available ? `发现新版本 ${data.latest.version}（${data.latest.commit.slice(0, 8)}）。确认后将更新并重启服务。`
        : data.status === "no_releases" ? "更新仓库尚未发布稳定版本。"
        : data.status === "ahead" ? `当前 ${data.current_version} 高于已发布的 ${data.latest.version}，无需更新。`
        : `当前 ${data.current_version} 已是最新稳定版本。`;
      if (data.available && data.blockers?.length) this.message += "\n" + data.blockers.map(explain).join("\n");
      if (data.checked_at) this.message += `\n检查时间：${new Date(data.checked_at * 1000).toLocaleString()}`;
    } catch (error) {
      this.message = explain(error);
    } finally {
      this.checking = false;
      this.render();
    }
  }
  async apply() {
    const data = this.data;
    if (this.running || this.runId || !data?.available || !data.can_update || !data.check_token) return;
    if (this.hasUnsavedChanges()) {
      this.message = "设置有未保存的更改，请先保存或重置，再执行更新。";
      this.render();
      return;
    }
    if (!confirm(`确认从 ${data.current_version} 更新到 ${data.latest.version}（${data.latest.commit.slice(0, 8)}）并重启？`)) return;
    this.sequence++;
    this.running = true;
    this.message = "正在提交更新…";
    this.render();
    try {
      const result = await request("/api/update/apply", { method: "POST", body: { check_token: data.check_token, ...data.latest } });
      this.runId = result.run_id;
      persist(RUN_KEY, this.runId);
      this.data = null;
    } catch (error) {
      this.message = explain(error);
      this.data = null;
      // A response can be lost during restart. Recover the server's run before retrying.
      this.running = false;
      await this.restore();
      this.render();
      return;
    }
    this.running = false;
    return this.poll();
  }
  async poll() {
    if (this.running || !this.runId) return;
    this.running = true;
    this.message = "正在读取更新进度…";
    this.render();
    const deadline = Date.now() + 180000;
    try {
      while (Date.now() < deadline) {
        try {
          const status = await request("/api/update/status");
          if (status.run_id === this.runId) {
            this.message = STAGES[status.status] || "正在等待更新进程…";
            if (status.error) this.message += " " + explain(status.error);
            if (TERMINAL.has(status.status)) {
              if (status.status === "healthy") {
                const version = await request("/api/version");
                if (version.commit !== status.commit || version.version !== status.version) {
                  this.message = "正在等待目标版本恢复服务…";
                  this.render();
                  await new Promise(resolve => setTimeout(resolve, 1000));
                  continue;
                }
                this.version = version;
                this.refreshReady = version.version !== $(".brand-version").textContent;
                if (this.refreshReady) this.message += " 点击「刷新使用新版本」加载最新界面。";
              }
              this.runId = "";
              persist(RUN_KEY, "");
              return;
            }
          }
        } catch {
          this.message = "服务正在重启或暂时无法连接，正在等待恢复…";
        }
        this.render();
        await new Promise(resolve => setTimeout(resolve, 1000));
      }
      this.message = "尚未确认更新结果，可点击「继续查看进度」。请勿重复提交更新。";
    } finally {
      this.running = false;
      this.render();
    }
  }
  refresh() {
    if (this.hasUnsavedChanges() && !confirm("设置有未保存的更改，仍要刷新页面？")) return;
    location.reload();
  }
}
