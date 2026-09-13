(() => {
  "use strict";
  const button = document.getElementById("opsRefreshNow");
  const automatic = document.getElementById("opsAutoRefresh");
  const status = document.getElementById("opsRefreshStatus");
  if (!button || !automatic || !status) return;
  let timer;
  let busy = false;
  let failures = 0;
  let lastSuccess = "";
  let stopped = false;
  try { automatic.checked = sessionStorage.getItem("ops-auto-refresh") !== "off"; } catch (_) { /* Storage is optional. */ }

  function blocked() {
    if (document.hidden) return "页面未显示，已暂停自动刷新";
    if (document.querySelector('[data-reveal][data-active="true"], [data-reveal]:disabled')) return "查看完整身份信息时暂缓刷新，收起后继续";
    if (document.activeElement?.matches('input:not([type=checkbox]), textarea, select, [contenteditable="true"]')) return "正在输入，暂缓自动刷新";
    if (window.getSelection()?.toString()) return "正在选取文字，暂缓自动刷新";
    if (document.querySelector('form.ajax-form button[type=submit]:disabled, #participantSearch button[type=submit]:disabled')) return "正在操作，暂缓刷新";
    return "";
  }

  function schedule() {
    clearTimeout(timer);
    if (automatic.checked && !document.hidden && !stopped) {
      timer = setTimeout(() => refresh(false), Math.min(120000, 30000 * (2 ** failures)));
    }
  }

  async function refresh() {
    if (busy) return;
    const reason = blocked();
    if (reason) {
      status.textContent = reason;
      schedule();
      return;
    }
    busy = true;
    button.disabled = true;
    status.textContent = "正在刷新…";
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 15000);
    try {
      const response = await fetch(location.href, {
        credentials: "same-origin", cache: "no-store", signal: controller.signal,
      });
      if (response.redirected || response.status === 401 || response.status === 403) {
        stopped = true;
        throw new Error("登录状态已变化，请重新登录后台。");
      }
      if (!response.ok) throw new Error("刷新失败，保留当前内容，稍后重试。");
      const next = new DOMParser().parseFromString(await response.text(), "text/html");
      const regions = [...document.querySelectorAll("[data-refresh-region]")];
      if (!next.getElementById("opsRefreshControls") || regions.some(region => !next.getElementById(region.id))) {
        throw new Error("页面内容已变化，请重新载入页面。");
      }
      // An input, search or privacy action may begin while the response is in flight.
      if (blocked()) { status.textContent = blocked(); return; }
      const tasks = [];
      document.dispatchEvent(new CustomEvent("ops:refresh", { detail: { tasks, signal: controller.signal } }));
      await Promise.all(tasks);
      if (blocked()) { status.textContent = blocked(); return; }
      const scroll = { x: window.scrollX, y: window.scrollY };
      const expanded = new Set([...document.querySelectorAll(".attempt-record:has(details[open])")].map(record => record.id));
      const focusedId = document.activeElement?.closest(".attempt-record")?.id;
      regions.forEach(region => {
        const fresh = next.getElementById(region.id);
        region.replaceChildren(...fresh.childNodes);
      });
      expanded.forEach(id => {
        const detail = document.getElementById(id)?.querySelector("details");
        if (detail) detail.open = true;
      });
      if (focusedId) document.getElementById(focusedId)?.querySelector("summary")?.focus({ preventScroll: true });
      window.scrollTo(scroll.x, scroll.y);
      failures = 0;
      lastSuccess = new Date().toLocaleTimeString("zh-CN", { timeZone: "Asia/Shanghai", hour12: false });
      status.textContent = `已更新 ${lastSuccess}（北京时间）${automatic.checked ? "" : " · 自动刷新已暂停"}`;
      document.dispatchEvent(new Event("ops:refreshed"));
    } catch (error) {
      controller.abort();
      failures = Math.min(failures + 1, 2);
      status.textContent = `${error.name === "AbortError" ? "刷新超时，保留当前内容。" : error.message}${lastSuccess ? ` 上次成功 ${lastSuccess}` : ""}`;
    } finally {
      clearTimeout(timeout);
      busy = false;
      button.disabled = false;
      schedule();
    }
  }
  button.addEventListener("click", () => { stopped = false; refresh(true); });
  automatic.addEventListener("change", () => {
    try { sessionStorage.setItem("ops-auto-refresh", automatic.checked ? "on" : "off"); } catch (_) { /* Storage is optional. */ }
    status.textContent = automatic.checked ? "自动刷新已开启（30秒）" : "自动刷新已暂停，可手动刷新";
    schedule();
  });
  document.addEventListener("visibilitychange", () => {
    clearTimeout(timer);
    if (!document.hidden && automatic.checked && !stopped) refresh(false);
  });
  status.textContent = automatic.checked ? "自动刷新已开启（30秒）" : "自动刷新已暂停，可手动刷新";
  schedule();
})();
