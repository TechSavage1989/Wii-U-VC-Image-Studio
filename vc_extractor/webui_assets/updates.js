"use strict";

class StudioUpdates {
  constructor(host, document, call) {
    this.host = host;
    this.document = document;
    this.call = call;
    this.lastKey = "";
    this.manualVisible = false;
    this.lastFocusCheck = 0;
    this.inflight = false;
  }

  async check(reason) {
    if (this.inflight || this.host.navigator.onLine === false) {
      if (reason === "manual") this.render({status: "error", message: "Offline. Updates could not be checked."});
      return;
    }
    this.inflight = true;
    try {
      const result = await this.call("check_updates", reason);
      this.render(result.update);
    } catch (error) {
      this.render({status: "error", message: String(error.message || error)});
    } finally { this.inflight = false; }
  }

  start() {
    this.document.getElementById("checkUpdatesButton").addEventListener("click", () => {
      this.manualVisible = true;
      this.check("manual");
    });
    this.check("startup");
    // Bind timers to the actual browser window. Backend independently enforces
    // single-flight and elapsed-time limits, including native focus callbacks.
    this.timer = this.host.setInterval(() => this.check("interval"), 300000);
    this.focus = () => {
      const now = Date.now();
      if (now - this.lastFocusCheck >= 120000) {
        this.lastFocusCheck = now;
        this.check("focus");
      }
    };
    this.host.addEventListener("focus", this.focus);
    this.host.addEventListener("online", this.focus);
    this.document.addEventListener("visibilitychange", () => {
      if (this.document.visibilityState === "visible") this.focus();
    });
    this.host.addEventListener("pagehide", () => this.host.clearInterval(this.timer), {once: true});
  }

  render(state, busy = false) {
    if (!state) return;
    const key = JSON.stringify([state, busy, this.manualVisible]);
    if (key === this.lastKey) return;
    this.lastKey = key;
    const banner = this.document.getElementById("updateBanner");
    const visible = this.manualVisible || ["available", "downloading", "downloaded", "installing", "error"].includes(state.status);
    banner.classList.toggle("hidden", !visible);
    this.document.getElementById("app").classList.toggle("has-update-banner", visible);
    banner.replaceChildren();
    if (!visible) return;
    const copy = this.document.createElement("div");
    const title = this.document.createElement("strong");
    title.textContent = state.latest && state.latest !== state.installed
      ? `Installed ${state.installed || ""} · Latest ${state.latest}`
      : `Updates${state.installed ? " · Installed " + state.installed : ""}`;
    const message = this.document.createElement("div");
    message.textContent = state.message + (state.retry_after ? ` Retry in ${state.retry_after}s.` : "");
    copy.append(title, message);
    const notes = state.notes || [];
    if (notes.length) {
      const list = this.document.createElement("ul");
      for (const note of notes.slice(0, 2)) {
        const item = this.document.createElement("li"); item.textContent = note; list.append(item);
      }
      copy.append(list);
      if (notes.length > 2) {
        const details = this.document.createElement("details");
        const summary = this.document.createElement("summary"); summary.textContent = "Read more";
        details.append(summary);
        for (const note of notes.slice(2)) {
          const item = this.document.createElement("p"); item.textContent = note; details.append(item);
        }
        copy.append(details);
      }
    }
    banner.append(copy);
    if (state.status === "downloading") {
      const progress = this.document.createElement("progress");
      progress.max = 100; progress.value = state.progress || 0; banner.append(progress);
    }
    const button = (label, handler, disabled = false) => {
      const element = this.document.createElement("button"); element.className = "button small";
      element.textContent = label; element.disabled = disabled;
      element.addEventListener("click", handler); banner.append(element);
    };
    if (state.status === "available") button("Download", async () => {
      try { this.render((await this.call("download_update")).update); }
      catch (e) { this.render({...state, status: "error", message: e.message}); }
    });
    if (state.status === "downloaded") button("Install and restart", async () => {
      if (!this.host.confirm("Restart to install? The current ROM list will close. Exported files and settings are retained.")) return;
      try { await this.call("install_update"); }
      catch (e) { this.host.alert(e.message); }
    }, busy);
    button("Dismiss", () => {
      this.manualVisible = false;
      banner.classList.add("hidden");
      this.document.getElementById("app").classList.remove("has-update-banner");
      // Keep hidden until the state changes, not on every polling repaint.
      this.lastKey = JSON.stringify([state, busy, false]);
    });
  }
}
