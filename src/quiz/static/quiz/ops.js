(() => {
  "use strict";

  const body = document.body;
  const activityId = body.dataset.activityId;
  const csrfToken = document.querySelector('meta[name="csrf-token"]')?.content || "";
  const toast = document.getElementById("toast");

  function showToast(message, isError = false) {
    if (!toast) return;
    toast.textContent = message;
    toast.style.background = isError ? "#ffb7d5" : "#ffe18d";
    toast.hidden = false;
    window.clearTimeout(showToast.timer);
    showToast.timer = window.setTimeout(() => { toast.hidden = true; }, 5000);
  }

  async function postForm(url, formData) {
    const response = await fetch(url, {
      method: "POST",
      credentials: "same-origin",
      headers: { "X-CSRFToken": csrfToken },
      body: formData,
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
  async function refreshSnapshot() {
    if (!summary || document.hidden) return;
    try {
      const response = await fetch(summary.dataset.snapshotUrl, {
        credentials: "same-origin",
        cache: "no-store",
      });
      if (!response.ok) throw new Error("监控刷新失败。");
      const data = await response.json();
      const values = { participants: data.participants, ...data.attempts };
      Object.entries(values).forEach(([key, value]) => {
        const target = summary.querySelector(`[data-metric="${key}"]`);
        if (target) target.textContent = String(value);
      });
    } catch (error) {
      showToast(error.message, true);
    }
  }
  refreshSnapshot();
  window.setInterval(refreshSnapshot, 10000);
  document.addEventListener("visibilitychange", () => { if (!document.hidden) refreshSnapshot(); });

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
    nameCell.textContent = participant.display_name;
    tr.append(nameCell);
    ["identifier", "contact"].forEach((field) => {
      const td = document.createElement("td");
      td.dataset.field = field;
      td.dataset.masked = participant[field];
      td.textContent = participant[field];
      tr.append(td);
    });
    const actionCell = document.createElement("td");
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
  searchForm?.addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      const payload = await postForm(searchForm.action, new FormData(searchForm));
      participantRows.replaceChildren();
      payload.participants.forEach(addParticipantRow);
      document.querySelectorAll("[data-reveal]").forEach((button) => {
        button.dataset.active = "false";
      });
      showToast(`找到 ${payload.participants.length} 名参与者。`);
    } catch (error) {
      showToast(error.message, true);
    }
  });

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
  categorySelect?.addEventListener("change", async () => {
    const url = new URL(categorySelect.dataset.url, window.location.origin);
    url.searchParams.set("category", categorySelect.value);
    try {
      const response = await fetch(url, { credentials: "same-origin", cache: "no-store" });
      if (!response.ok) throw new Error("榜单读取失败。");
      const data = await response.json();
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
      showToast(error.message, true);
    }
  });
})();
