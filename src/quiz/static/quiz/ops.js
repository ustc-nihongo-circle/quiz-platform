(() => {
  "use strict";

  const body = document.body;
  const activityId = body.dataset.activityId;
  const csrfToken = document.querySelector('meta[name="csrf-token"]')?.content || "";
  const toast = document.getElementById("toast");

  const activitySelect = document.getElementById("activitySelect");
  const sectionSelect = document.getElementById("sectionSelect");
  const sectionLinks = [...document.querySelectorAll("[data-section-link]")];
  const sections = sectionLinks.map((link) => document.getElementById(link.hash.slice(1)));
  const topbar = document.querySelector(".topbar");
  let headerHeight = 0;

  function markSection(id) {
    sectionLinks.forEach((link) => {
      if (link.hash === `#${id}`) link.setAttribute("aria-current", "location");
      else link.removeAttribute("aria-current");
    });
    if (sectionSelect) sectionSelect.value = id;
  }

  function updateSectionPosition() {
    if (!sections.length) return;
    let current = sections[0];
    for (const section of sections) {
      if (section.getBoundingClientRect().top <= headerHeight + 32) current = section;
    }
    // The final section may be too short to reach the top of the viewport.
    if (window.scrollY + window.innerHeight >= document.documentElement.scrollHeight - 2) {
      current = sections[sections.length - 1];
    }
    markSection(current.id);
  }

  function updateHeaderHeight() {
    headerHeight = topbar && getComputedStyle(topbar).position === "sticky"
      ? topbar.getBoundingClientRect().height : 0;
    document.documentElement.style.setProperty("--ops-header-height", `${headerHeight}px`);
    updateSectionPosition();
  }

  function focusHashSection() {
    const section = sections.find((item) => `#${item.id}` === window.location.hash);
    if (section) {
      section.focus({ preventScroll: true });
      markSection(section.id);
    }
  }

  if (activitySelect && sectionSelect && sections.length) {
    body.classList.add("has-ops-navigation");
    document.getElementById("activitySwitcher").hidden = false;
    activitySelect.addEventListener("change", () => {
      window.location.assign(activitySelect.value);
    });
    sectionSelect.addEventListener("change", () => {
      const section = document.getElementById(sectionSelect.value);
      window.location.hash = section.id;
      // A repeated selection of the current hash must also move back to its heading.
      window.scrollTo({ top: section.getBoundingClientRect().top + window.scrollY - headerHeight - 16 });
      focusHashSection();
    });
    let scrollPending = false;
    window.addEventListener("scroll", () => {
      if (scrollPending) return;
      scrollPending = true;
      window.requestAnimationFrame(() => {
        scrollPending = false;
        updateSectionPosition();
      });
    }, { passive: true });
    window.addEventListener("hashchange", focusHashSection);
    window.addEventListener("resize", updateHeaderHeight);
    if (topbar && "ResizeObserver" in window) new ResizeObserver(updateHeaderHeight).observe(topbar);
    updateHeaderHeight();
    focusHashSection();
  }

  function showToast(message, isError = false) {
    if (!toast) return;
    toast.textContent = message;
    toast.style.background = isError ? "#ffb7d5" : "#ffe18d";
    toast.hidden = false;
    window.clearTimeout(showToast.timer);
    showToast.timer = window.setTimeout(() => { toast.hidden = true; }, 5000);
  }

  async function postForm(url, formData, signal) {
    const response = await fetch(url, {
      method: "POST",
      credentials: "same-origin",
      headers: { "X-CSRFToken": csrfToken },
      body: formData,
      signal,
    });
    const contentType = response.headers.get("content-type") || "";
    const payload = contentType.includes("application/json")
      ? await response.json()
      : { errors: [await response.text()] };
    if (!response.ok) {
      const message = payload.errors?.join("；") || "操作失败。";
      throw new Error(message);
    }
    return payload;
  }

  document.querySelectorAll("form.ajax-form").forEach((form) => {
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const button = form.querySelector('button[type="submit"]');
      if (button) button.disabled = true;
      try {
        await postForm(form.action, new FormData(form));
        showToast("操作已保存，页面正在刷新。");
        window.setTimeout(() => window.location.reload(), 350);
      } catch (error) {
        showToast(error.message, true);
        if (button) button.disabled = false;
      }
    });
  });

  const summary = document.getElementById("activitySummary");
  async function refreshSnapshot(signal) {
    if (!summary || document.hidden) return;
    try {
      const response = await fetch(summary.dataset.snapshotUrl, {
        credentials: "same-origin",
        cache: "no-store",
        signal,
      });
      if (!response.ok) throw new Error("监控刷新失败。");
      const data = await response.json();
      const values = { participants: data.participants, ...data.attempts };
      Object.entries(values).forEach(([key, value]) => {
        const target = summary.querySelector(`[data-metric="${key}"]`);
        if (target) target.textContent = String(value);
      });
    } catch (error) {
      if (signal) throw error;
      showToast(error.message, true);
    }
  }
  refreshSnapshot();

  const participantRows = document.getElementById("participantRows");
  function addParticipantRow(participant) {
    const tr = document.createElement("tr");
    tr.dataset.participantId = participant.id;
    const selectCell = document.createElement("td");
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.checked = true;
    checkbox.className = "participant-select";
    checkbox.setAttribute("aria-label", `选择 ${participant.display_name}`);
    selectCell.append(checkbox);
    tr.append(selectCell);
    const nameCell = document.createElement("td");
    const detailUrl = `/ops/activities/${activityId}/participants/${participant.id}/`;
    const nameLink = document.createElement("a");
    nameLink.className = "participant-link";
    nameLink.href = detailUrl;
    nameLink.textContent = participant.display_name;
    nameCell.append(nameLink);
    tr.append(nameCell);
    ["identifier", "contact"].forEach((field) => {
      const td = document.createElement("td");
      td.dataset.field = field;
      td.dataset.masked = participant[field];
      td.textContent = participant[field];
      tr.append(td);
    });
    const countCell = document.createElement("td");
    countCell.textContent = participant.attempt_count;
    const recentCell = document.createElement("td");
    recentCell.textContent = participant.last_attempt_at
      ? new Date(participant.last_attempt_at).toLocaleString("zh-CN", { timeZone: "Asia/Shanghai", hour12: false })
      : "尚未答题";
    tr.append(countCell, recentCell);
    const actionCell = document.createElement("td");
    const detailLink = document.createElement("a");
    detailLink.className = "record-link";
    detailLink.href = detailUrl;
    detailLink.textContent = "全部答题";
    actionCell.append(detailLink);
    const button = document.createElement("button");
    button.type = "button";
    button.className = "link-button";
    button.dataset.deidentifyUrl = `/ops/participants/${participant.id}/deidentify/`;
    button.textContent = "去身份化";
    actionCell.append(button);
    tr.append(actionCell);
    participantRows.append(tr);
  }

  participantRows?.querySelectorAll("[data-field]").forEach((cell) => {
    cell.dataset.masked = cell.textContent.trim();
  });

  const searchForm = document.getElementById("participantSearch");
  const pageStatus = document.getElementById("participantPageStatus");
  const pageButtons = [...document.querySelectorAll("[data-participant-page]")];
  let participantPage = Number(pageStatus?.dataset.page || 1);
  let participantPages = Number(pageStatus?.dataset.pages || 1);
  let searchSequence = 0;
  let appliedSearch = searchForm ? new FormData(searchForm) : null;
  if (pageStatus) document.getElementById("participantPagination").hidden = false;

  async function searchPage(page = 1, refresh = false, signal) {
    const sequence = ++searchSequence;
    const formData = refresh ? new FormData() : new FormData(searchForm);
    if (refresh) appliedSearch.forEach((value, key) => formData.append(key, value));
    formData.set("page", String(page));
    const submitButton = searchForm.querySelector('button[type="submit"]');
    if (submitButton) submitButton.disabled = true;
    pageButtons.forEach((button) => { button.disabled = true; });
    try {
      const payload = await postForm(searchForm.action, formData, signal);
      // A slower earlier search must not replace the latest sort/filter result.
      if (sequence !== searchSequence) {
        if (refresh) throw new Error("检索条件已变化，下次刷新继续更新。");
        return;
      }
      if (refresh && (document.hidden || document.activeElement?.matches('input:not([type=checkbox]), textarea, select') || document.querySelector('[data-reveal][data-active="true"], [data-reveal]:disabled'))) {
        throw new Error("正在操作，保留当前列表，稍后刷新。");
      }
      const selected = new Set([...participantRows.querySelectorAll('tr[data-participant-id]')]
        .filter(row => row.querySelector('.participant-select')?.checked).map(row => row.dataset.participantId));
      participantRows.replaceChildren();
      payload.participants.forEach(addParticipantRow);
      if (refresh) participantRows.querySelectorAll('tr[data-participant-id]').forEach(row => {
        row.querySelector('.participant-select').checked = selected.has(row.dataset.participantId);
      });
      appliedSearch = formData;
      if (!payload.participants.length) {
        const row = document.createElement("tr");
        const cell = document.createElement("td");
        cell.colSpan = 7;
        cell.textContent = "没有匹配的参与者。";
        row.append(cell);
        participantRows.append(row);
      }
      participantPage = payload.pagination.page;
      participantPages = payload.pagination.pages;
      pageStatus.textContent = `第 ${participantPage} / ${participantPages} 页 · 共 ${payload.pagination.total} 人`;
      document.querySelectorAll("[data-reveal]").forEach((button) => {
        button.dataset.active = "false";
        button.textContent = button.dataset.reveal === "identifier" ? "显示完整编号" : "显示完整联系方式";
      });
      if (!refresh) showToast(`找到 ${payload.pagination.total} 名参与者。`);
    } catch (error) {
      if (refresh) throw error;
      if (sequence === searchSequence) showToast(error.message, true);
    } finally {
      if (sequence === searchSequence) {
        if (submitButton) submitButton.disabled = false;
        pageButtons.forEach((button) => {
          button.disabled = button.dataset.participantPage === "previous"
            ? participantPage <= 1 : participantPage >= participantPages;
        });
      }
    }
  }
  searchForm?.addEventListener("submit", (event) => {
    event.preventDefault();
    searchPage();
  });
  document.getElementById("participantSort")?.addEventListener("change", () => searchPage());
  pageButtons.forEach((button) => button.addEventListener("click", () => {
    searchPage(participantPage + (button.dataset.participantPage === "previous" ? -1 : 1));
  }));

  document.querySelectorAll("[data-reveal]").forEach((button) => {
    const field = button.dataset.reveal;
    const originalLabel = button.textContent;
    button.dataset.active = "false";
    button.addEventListener("click", async () => {
      const selectedRows = [...participantRows.querySelectorAll("tr[data-participant-id]")]
        .filter((row) => row.querySelector(".participant-select")?.checked);
      if (button.dataset.active === "true") {
        selectedRows.forEach((row) => {
          const cell = row.querySelector(`[data-field="${field}"]`);
          if (cell) cell.textContent = cell.dataset.masked;
        });
        button.dataset.active = "false";
        button.textContent = originalLabel;
        return;
      }
      if (!selectedRows.length) {
        showToast("请先选择当前页中的参与者。", true);
        return;
      }
      const data = new FormData();
      selectedRows.forEach((row) => data.append("participant_ids", row.dataset.participantId));
      data.append("fields", field);
      button.disabled = true;
      try {
        const payload = await postForm(
          `/ops/activities/${activityId}/participants/reveal/`,
          data,
        );
        Object.entries(payload.participants).forEach(([id, values]) => {
          const cell = participantRows.querySelector(`tr[data-participant-id="${id}"] [data-field="${field}"]`);
          if (cell) cell.textContent = values[field];
        });
        button.dataset.active = "true";
        button.textContent = field === "identifier" ? "恢复脱敏编号" : "恢复脱敏联系方式";
      } catch (error) {
        showToast(error.message, true);
      } finally {
        button.disabled = false;
      }
    });
  });

  async function reasonAction(url, action, promptText) {
    const reason = window.prompt(promptText);
    if (!reason) return;
    const data = new FormData();
    if (action) data.append("action", action);
    data.append("reason", reason);
    try {
      await postForm(url, data);
      showToast("操作已完成，页面正在刷新。");
      window.setTimeout(() => window.location.reload(), 350);
    } catch (error) {
      showToast(error.message, true);
    }
  }

  document.addEventListener("click", (event) => {
    const target = event.target.closest("button");
    if (!target) return;
    if (target.dataset.deidentifyUrl) {
      reasonAction(target.dataset.deidentifyUrl, "", "请填写去身份化原因。此操作不可恢复。 ");
    } else if (target.dataset.invalidateUrl) {
      reasonAction(target.dataset.invalidateUrl, "", "请填写作废答题记录的原因。");
    } else if (target.dataset.deactivateRuleUrl) {
      reasonAction(target.dataset.deactivateRuleUrl, "deactivate", "请填写停用兑奖词的原因。");
    }
  });

  const categorySelect = document.getElementById("leaderboardCategory");
  let leaderboardSequence = 0;
  async function refreshLeaderboard(signal) {
    if (!categorySelect) return;
    const sequence = ++leaderboardSequence;
    const url = new URL(categorySelect.dataset.url, window.location.origin);
    url.searchParams.set("category", categorySelect.value);
    try {
      const response = await fetch(url, { credentials: "same-origin", cache: "no-store", signal });
      if (!response.ok) throw new Error("榜单读取失败。");
      const data = await response.json();
      if (sequence !== leaderboardSequence) return;
      const list = document.getElementById("leaderboardRows");
      list.replaceChildren();
      data.rows.forEach((row) => {
        const li = document.createElement("li");
        const rank = document.createElement("b");
        rank.textContent = row.rank;
        const name = document.createElement("span");
        name.textContent = row.display_name;
        const score = document.createElement("strong");
        score.textContent = row.score;
        li.append(rank, name, score);
        list.append(li);
      });
    } catch (error) {
      if (signal) throw error;
      showToast(error.message, true);
    }
  }
  categorySelect?.addEventListener("change", () => refreshLeaderboard());
  document.addEventListener("ops:refresh", event => {
    const { tasks, signal } = event.detail;
    tasks.push(refreshSnapshot(signal), refreshLeaderboard(signal));
    if (searchForm) tasks.push(searchPage(participantPage, true, signal));
  });
})();
