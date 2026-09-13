const MOCK_MODE = false;
const API_ROOT = "/api/v1";
const MOCK_STATE_KEY = "ustc-nihongo-quiz:mock-server:v4";
const UI_PARTICIPANT_KEY = "ustc-nihongo-quiz:participant";
const UI_LAST_ATTEMPT_KEY = "ustc-nihongo-quiz:last-attempt";
const DRAFT_PREFIX = "ustc-nihongo-quiz:draft:";

const endpoint = {
  activity: `${API_ROOT}/activity`,
  participantSession: `${API_ROOT}/participant-session`,
  attempts: `${API_ROOT}/attempts`,
  currentAttempt: `${API_ROOT}/attempts/current`,
  history: (page) => `${API_ROOT}/attempts/history?page=${page}`,
  attempt: (attemptId) => `${API_ROOT}/attempts/${attemptId}`,
  submission: (attemptId) => `${API_ROOT}/attempts/${attemptId}/submission`,
};

function renderQuestionText(target, value) {
  // Legacy reading questions use exactly this underline annotation. Build DOM
  // nodes ourselves so question content never becomes executable HTML or CSS.
  const text = String(value ?? "");
  const annotation = /<span\s+style=(["'])\s*text-decoration\s*:\s*underline\s*;?\s*\1\s*>([\s\S]*?)<\/span\s*>/gi;
  const fragment = document.createDocumentFragment();
  let cursor = 0;
  for (const match of text.matchAll(annotation)) {
    fragment.append(document.createTextNode(text.slice(cursor, match.index)));
    const underline = document.createElement("u");
    underline.textContent = match[2];
    fragment.append(underline);
    cursor = match.index + match[0].length;
  }
  fragment.append(document.createTextNode(text.slice(cursor)));
  target.replaceChildren(fragment);
}

const categoryPalette = ["cyan-card", "pink-card", "white-card", "yellow-card"];
const itemIds = [
  "f4138c5d-45cb-45b4-9d4e-48ac36bb2a01",
  "53f695ac-9ae3-4497-b859-aee15f86e2bd",
  "fb4f7626-c943-48e0-ad73-221adfe3dd13",
  "0b376b3b-89f9-47d2-97dc-873abf7601fb",
  "f08290c1-826f-402c-a734-5bd35814d2d0",
  "45f39ad1-4861-435b-b32f-9c82213ed81e",
  "df06be1b-34ed-42b0-8bbd-5585254ab083",
  "c7af2262-b6ea-4e88-9dc9-7488ea3d5a18",
  "5b7e3f40-d751-4fd2-8c88-9fc763cf8a2a",
  "1344c385-3628-4f87-baf3-355ac77b897a",
  "2e1bf4c0-35d6-4d4c-b474-ea5d327dd945",
  "6e98a79c-0cc9-4daf-8be6-5f01c50a17b4",
  "c51e4377-0cd6-45f7-a7c1-300ddf563020",
  "8c2576ff-4510-492f-8daa-3bf89db5a5a2",
  "3d572fa0-7bb9-4092-aa83-76f922cb77c8",
];
const correctAnswers = ["B", "A", "D", "C", "B", "A", "C", "D", "A", "B", "C", "D", "B", "A", "C"];

class ApiError extends Error {
  constructor(error = {}, status = 0) {
    super(error.message || "请求失败，请稍后重试。");
    this.name = "ApiError";
    this.code = error.code || "request_failed";
    this.fieldErrors = error.field_errors || {};
    this.retryable = Boolean(error.retryable);
    this.status = status;
    this.retryAfter = Math.max(1, Math.ceil(Number(error.retry_after_seconds) || 3));
    this.operation = error.operation || "read";
  }
}

const cooldowns = {
  deadlines: new Map(),
  key(path, method) {
    if (method === "DELETE") return "exit";
    if (method === "PUT") return "submit";
    if (method === "POST") return path.endsWith("participant-session") ? "registration" : "start";
    return "read";
  },
  remaining(key) { return Math.max(0, Math.ceil(((this.deadlines.get(key) || 0) - Date.now()) / 1000)); },
  set(key, seconds) { this.deadlines.set(key, Date.now() + seconds * 1000); this.sync(); },
  button(button, key, disabled = false) {
    if (!button) return;
    button.dataset.rateOperation = key;
    button.dataset.requestDisabled = String(disabled);
    button.disabled = disabled || this.remaining(key) > 0;
  },
  sync() {
    document.querySelectorAll("[data-rate-operation]").forEach(button => {
      button.disabled = button.dataset.requestDisabled === "true" || this.remaining(button.dataset.rateOperation) > 0;
    });
    document.querySelectorAll("[data-cooldown-message]").forEach(span => {
      const remaining = this.remaining(span.dataset.cooldownMessage);
      const text = remaining ? `请求较频繁，请在 ${remaining} 秒后重试。` : "现在可以重试。";
      if (span.textContent !== text) span.textContent = text;
    });
  },
};
window.setInterval(() => cooldowns.sync(), 250);

function validateRegistrationForm(form) {
  const rules = {display_name: [400, 100, "显示名"], identifier: [128, 64, "学号或工号"], contact: [512, 254, "联系方式"]};
  for (const [field, [rawLimit, limit, label]] of Object.entries(rules)) {
    const input = form.elements[field];
    let normalized = input.value.normalize("NFKC").trim();
    if (field === "identifier") normalized = normalized.toUpperCase();
    const invalid = Array.from(input.value).length > rawLimit || Array.from(normalized).length > limit;
    input.setCustomValidity(invalid ? `${label}最多 ${limit} 个字符。` : "");
  }
  return form.checkValidity();
}

function deepClone(value) {
  return structuredClone(value);
}

function csrfToken() {
  const match = document.cookie.match(/(?:^|; )csrftoken=([^;]+)/);
  return match ? decodeURIComponent(match[1]) : "";
}

function createQuestions() {
  const prompts = [
    "虚构桌面中，哪一个窗口负责显示活动状态？",
    "选择答案后，当前标签页会把草稿保存在哪里？",
    "刷新页面时，哪一项时间信息不能被客户端重置？",
    "活动暂停时，已有进行中答题应当如何处理？",
    "观察题图中的四个窗口。假设它们需要在手机上保持编号清楚、不能越过题目窗口边界，同时题干还要说明服务端计时、刷新恢复和最终整批提交规则，哪项描述符合当前原型？",
    "活动关闭后，哪类参与者仍可能继续已有答题？",
    "交卷时，未作答题目应如何处理？",
    "结果页允许向参与者显示什么？",
    "再次提交同一答题记录时，服务端应返回什么？",
    "超过 deadline 后，客户端首先应做什么？",
    "哪个字段用于标识一道本轮题目？",
    "板块卡片展示的题量和限时来自哪里？",
    "参与者更换身份时，前端应清理什么？",
    "减少动画偏好开启后，页面切换应如何滚动？",
    "像素 Yuriko 的作者来源记录应保留在哪里？",
  ];
  const optionSets = [
    ["活动状态窗口", "浏览器地址栏", "题目编号", "兑奖词"],
    ["当前标签页 sessionStorage", "服务端逐题接口", "图片缓存", "URL 参数"],
    ["deadline_at", "页面打开时间", "最后选择时间", "动画开始时间"],
    ["恢复原答题与原截止时间", "重新抽题", "清空倒计时", "直接给出答案"],
    ["题图完整显示且页面无横向溢出", "题图裁掉一半", "窗口撑出屏幕", "隐藏题干"],
    ["仍持有有效 Session 和进行中答题者", "任何新访客", "没有 Session 的访客", "只看结果者"],
    ["省略并按未答处理", "自动选择 A", "阻止提交", "补成空字符串"],
    ["本次得分与逐题对错", "正确答案全文", "其他参与者信息", "题库答案表"],
    ["首次封存结果", "覆盖为新答案", "新建第二条记录", "删除旧结果"],
    ["读取服务端状态", "自行增加三秒", "重置五分钟", "继续离线计分"],
    ["响应中的不透明 item id", "题号文本", "数组下标拼接值", "选项字母"],
    ["GET /api/v1/activity", "本地固定数组", "结果页缓存", "管理员截图"],
    ["参与者 Session 与当前标签页草稿", "正式题库", "活动配置", "其他人的结果"],
    ["使用即时滚动", "继续平滑滚动", "禁止页面切换", "隐藏焦点"],
    ["原型资产说明", "题目答案", "浏览器 Cookie", "参与者显示名"],
  ];
  return itemIds.map((id, index) => ({
    id,
    position: index + 1,
    prompt: prompts[index],
    type: "single_choice",
    image_url: index === 4 ? "assets/mock-question-diagram.svg" : null,
    options: optionSets[index].map((text, optionIndex) => ({
      id: ["A", "B", "C", "D"][optionIndex],
      text,
    })),
  }));
}

function createAttempt(category, { status = "in_progress", remainingMs = 5 * 60 * 1000 } = {}) {
  const startedAt = new Date(Date.now() - 45_000);
  const deadlineAt = new Date(Date.now() + remainingMs);
  return {
    id: "a03171e2-f46e-4649-a0e8-d87b46d8ad6f",
    status,
    category: { code: category.code, title: category.title },
    started_at: startedAt.toISOString(),
    deadline_at: deadlineAt.toISOString(),
    question_count: category.question_count,
    questions: createQuestions(),
  };
}

function submitMockAttempt(attempt) {
  const answers = attempt.mock_answers || {};
  const { mock_answers: _internalAnswers, ...publicAttempt } = attempt;
  const questions = attempt.questions.map((question, index) => {
    const answer = answers[question.id] ?? null;
    return {
      ...question,
      answer,
      correct: answer !== null && answer === correctAnswers[index],
    };
  });
  const score = questions.filter((question) => question.correct).length;
  return {
    ...publicAttempt,
    status: "submitted",
    questions,
    score,
    score_rate: score / attempt.question_count,
    category_high_score: Math.max(score, 12),
    reward_phrase: score >= 10 ? "薄荷汽水" : null,
  };
}

function baseMockState() {
  return {
    scenario: "open-new",
    activity: {
      code: "autumn-2026-demo",
      title: "2026 秋季游园会 · 脱敏原型",
      status: "open",
      categories: [
        { code: "acg", title: "ACG", question_count: 15, time_limit_seconds: 300 },
        { code: "language", title: "日语知识", question_count: 15, time_limit_seconds: 300 },
        { code: "culture", title: "文化杂谈", question_count: 15, time_limit_seconds: 300 },
        { code: "history", title: "历史", question_count: 15, time_limit_seconds: 300 },
      ],
    },
    knownParticipant: {
      id: "bcf75d6c-676e-476a-998d-e360430691f0",
      display_name: "林檎同学",
      identifier: "PB00000000",
      contact: "mock@example.invalid",
    },
    sessionParticipant: null,
    attempt: null,
    failNext: {},
  };
}

function mockScenario(name) {
  const state = baseMockState();
  state.scenario = name;
  const culture = state.activity.categories.find((category) => category.code === "culture");
  if (name === "draft") state.activity.status = "draft";
  if (name === "paused-new" || name === "paused-current") state.activity.status = "paused";
  if (name === "closed-new" || name === "closed-current") state.activity.status = "closed";
  if (["open-ready", "open-current", "paused-current", "closed-current", "timed-out", "submitted"].includes(name)) {
    state.sessionParticipant = {
      id: state.knownParticipant.id,
      display_name: state.knownParticipant.display_name,
    };
  }
  if (["open-current", "paused-current", "closed-current"].includes(name)) {
    state.attempt = createAttempt(culture, { remainingMs: 4 * 60 * 1000 });
  }
  if (name === "timed-out") {
    state.attempt = createAttempt(culture, { status: "timed_out", remainingMs: -2_000 });
  }
  if (name === "submitted") {
    const attempt = createAttempt(culture, { remainingMs: 120_000 });
    attempt.mock_answers = Object.fromEntries(attempt.questions.map((question, index) => [
      question.id,
      index < 11 ? correctAnswers[index] : ["A", "B", "C", "D"][index % 4],
    ]));
    state.attempt = submitMockAttempt(attempt);
  }
  return state;
}

class MockTransport {
  constructor() {
    this.state = this.load();
  }

  load() {
    try {
      const stored = sessionStorage.getItem(MOCK_STATE_KEY);
      if (stored) return JSON.parse(stored);
    } catch {
      // A fresh in-memory state remains usable when storage is unavailable.
    }
    return mockScenario("open-new");
  }

  save() {
    try {
      sessionStorage.setItem(MOCK_STATE_KEY, JSON.stringify(this.state));
    } catch {
      // The mock remains usable for the current document without persistence.
    }
  }

  setScenario(name) {
    this.state = mockScenario(name);
    this.save();
    return {
      participant: this.state.sessionParticipant,
      attemptId: this.state.attempt?.id || null,
    };
  }

  failNext(operation, retryable = true) {
    this.state.failNext[operation] = { retryable };
    this.save();
  }

  expireSoon(milliseconds = 800) {
    if (!this.state.attempt || this.state.attempt.status !== "in_progress") return;
    this.state.attempt.deadline_at = new Date(Date.now() + milliseconds).toISOString();
    this.save();
  }

  operationFor(path, method) {
    if (path === endpoint.participantSession && method === "POST") return "register";
    if (path === endpoint.participantSession && method === "DELETE") return "clear";
    if (path === endpoint.attempts && method === "POST") return "start";
    if (path === endpoint.currentAttempt) return "resume";
    if (path.endsWith("/submission")) return "submit";
    return "read";
  }

  maybeFail(operation) {
    const failure = this.state.failNext[operation];
    if (!failure) return;
    delete this.state.failNext[operation];
    this.save();
    throw new ApiError({
      code: "mock_transport_failure",
      message: `${operation} 的模拟网络请求失败。`,
      field_errors: {},
      retryable: failure.retryable,
    }, 503);
  }

  refreshTimeout() {
    if (!this.state.attempt || this.state.attempt.status !== "in_progress") return;
    if (Date.now() >= Date.parse(this.state.attempt.deadline_at)) {
      this.state.attempt.status = "timed_out";
      this.save();
    }
  }

  async request(path, options = {}) {
    const method = (options.method || "GET").toUpperCase();
    const operation = this.operationFor(path, method);
    await new Promise((resolve) => window.setTimeout(resolve, 180));
    this.maybeFail(operation);

    if (path === endpoint.activity && method === "GET") {
      return { activity: deepClone(this.state.activity) };
    }

    if (path === endpoint.participantSession && method === "POST") {
      if (!["open", "paused", "closed"].includes(this.state.activity.status)) {
        throw new ApiError({ code: "activity_unavailable", message: "当前活动不接受参与者进入。", retryable: false }, 409);
      }
      const payload = JSON.parse(options.body || "{}");
      const known = this.state.knownParticipant;
      const sameIdentifier = payload.identifier.trim().toUpperCase() === known.identifier;
      if (sameIdentifier && payload.contact.trim().toLowerCase() !== known.contact.toLowerCase()) {
        throw new ApiError({
          code: "participant_recovery_required",
          message: "登记信息无法匹配，请联系活动管理员处理。",
          field_errors: {},
          retryable: false,
        }, 409);
      }
      if (this.state.activity.status !== "open" && !sameIdentifier) {
        throw new ApiError({
          code: "participant_recovery_required",
          message: "活动当前只允许已登记参与者再次进入。",
          field_errors: {},
          retryable: false,
        }, 409);
      }
      const created = !sameIdentifier;
      if (created) {
        this.state.knownParticipant = {
          id: crypto.randomUUID(),
          display_name: payload.display_name,
          identifier: payload.identifier.trim().toUpperCase(),
          contact: payload.contact.trim(),
        };
      }
      this.state.sessionParticipant = {
        id: this.state.knownParticipant.id,
        display_name: this.state.knownParticipant.display_name,
      };
      this.save();
      return { participant: deepClone(this.state.sessionParticipant), created };
    }

    if (path === endpoint.participantSession && method === "DELETE") {
      this.state.sessionParticipant = null;
      this.save();
      return null;
    }

    if (!this.state.sessionParticipant) {
      throw new ApiError({ code: "participant_session_required", message: "请先登记或再次进入。", retryable: false }, 401);
    }

    if (path === endpoint.currentAttempt && method === "GET") {
      this.refreshTimeout();
      if (!this.state.attempt || this.state.attempt.status !== "in_progress") {
        if (this.state.attempt?.status === "timed_out") {
          return { attempt: deepClone(this.state.attempt) };
        }
        throw new ApiError({ code: "attempt_not_found", message: "当前没有进行中的答题。", retryable: false }, 404);
      }
      return { attempt: deepClone(this.state.attempt) };
    }

    if (path === endpoint.attempts && method === "POST") {
      this.refreshTimeout();
      if (this.state.attempt?.status === "in_progress") {
        return { attempt: deepClone(this.state.attempt) };
      }
      if (this.state.activity.status !== "open") {
        throw new ApiError({ code: "activity_not_open", message: "当前活动不接受新答题。", retryable: false }, 409);
      }
      const payload = JSON.parse(options.body || "{}");
      const category = this.state.activity.categories.find((entry) => entry.code === payload.category_code);
      if (!category) {
        throw new ApiError({
          code: "validation_error",
          message: "请检查提交字段。",
          field_errors: { category_code: ["板块不存在。"] },
          retryable: false,
        }, 400);
      }
      this.state.attempt = createAttempt(category, { remainingMs: category.time_limit_seconds * 1000 });
      this.save();
      return { attempt: deepClone(this.state.attempt) };
    }

    if (path === endpoint.attempt(this.state.attempt?.id) && method === "GET") {
      this.refreshTimeout();
      return { attempt: deepClone(this.state.attempt) };
    }

    if (path === endpoint.submission(this.state.attempt?.id) && method === "PUT") {
      this.refreshTimeout();
      if (this.state.attempt.status === "timed_out") {
        throw new ApiError({ code: "attempt_expired", message: "本次答题已经超时。", retryable: false }, 409);
      }
      if (this.state.attempt.status === "submitted") {
        return { attempt: deepClone(this.state.attempt) };
      }
      const payload = JSON.parse(options.body || "{}");
      const validIds = new Set(this.state.attempt.questions.map((question) => question.id));
      const answers = {};
      for (const entry of payload.answers || []) {
        if (!validIds.has(entry.item_id) || Object.hasOwn(answers, entry.item_id)) {
          throw new ApiError({
            code: "validation_error",
            message: "请检查提交字段。",
            field_errors: { answers: ["答案包含无效或重复的题目 ID。"] },
            retryable: false,
          }, 400);
        }
        answers[entry.item_id] = entry.answer;
      }
      this.state.attempt.mock_answers = answers;
      this.state.attempt = submitMockAttempt(this.state.attempt);
      this.save();
      return { attempt: deepClone(this.state.attempt) };
    }

    throw new ApiError({ code: "not_found", message: "模拟接口不存在。", retryable: false }, 404);
  }
}

class HttpTransport {
  async request(path, options = {}) {
    const method = (options.method || "GET").toUpperCase();
    const operation = cooldowns.key(path, method);
    const waiting = cooldowns.remaining(operation);
    if (waiting) throw new ApiError({code: "rate_limited", message: "请稍后重试。", retryable: true, retry_after_seconds: waiting, operation}, 429);
    const headers = { "Content-Type": "application/json", ...(options.headers || {}) };
    if (!new Set(["GET", "HEAD", "OPTIONS"]).has(method)) headers["X-CSRFToken"] = csrfToken();
    // Retry only reads: a lost response does not mean a write failed on the server.
    const attempts = method === "GET" ? 2 : 1;
    for (let attempt = 0; attempt < attempts; attempt++) {
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), method === "GET" ? 12000 : 20000);
      try {
        const response = await fetch(path, {
          credentials: "same-origin", cache: "no-store", ...options, method, headers,
          signal: controller.signal,
        });
        if (response.status === 204) return null;
        let body;
        try {
          body = await response.json();
        } catch (error) {
          if (controller.signal.aborted) throw error;
          if (response.status === 429) body = {error: {code: "rate_limited", retryable: true}};
          else
          throw new ApiError({ code: "invalid_response", message: "未收到有效的服务响应，请稍后重试。", retryable: true }, response.status);
        }
        if (response.status === 429) {
          const seconds = Math.max(1, Math.ceil(Number(response.headers.get("Retry-After")) || Number(body?.error?.retry_after_seconds) || 3));
          cooldowns.set(operation, seconds);
          throw new ApiError({...body.error, code: "rate_limited", retryable: true, retry_after_seconds: seconds, operation}, 429);
        }
        if (!response.ok) throw new ApiError(body?.error || {
          code: "request_failed", message: "服务暂时不可用，请稍后重试。", retryable: response.status >= 500,
        }, response.status);
        return body;
      } catch (error) {
        const failure = error instanceof ApiError ? error : new ApiError({
          code: controller.signal.aborted ? "request_timeout" : "network_error",
          message: controller.signal.aborted
            ? "请求超时，请重试。若刚才提交过，重试会向服务端确认结果。"
            : "网络连接失败，请检查连接后重试。",
          retryable: true,
        });
        if (failure.status === 429 || attempt + 1 === attempts || !failure.retryable) throw failure;
      } finally {
        clearTimeout(timer);
      }
      await new Promise(resolve => setTimeout(resolve, 500));
    }
  }
}

class QuizApi {
  constructor(transport) {
    this.transport = transport;
  }
  activity() { return this.transport.request(endpoint.activity); }
  register(payload) { return this.transport.request(endpoint.participantSession, { method: "POST", body: JSON.stringify(payload) }); }
  clearSession() { return this.transport.request(endpoint.participantSession, { method: "DELETE" }); }
  currentAttempt() { return this.transport.request(endpoint.currentAttempt); }
  history(page) { return this.transport.request(endpoint.history(page)); }
  attempt(attemptId) { return this.transport.request(endpoint.attempt(attemptId)); }
  startAttempt(categoryCode) { return this.transport.request(endpoint.attempts, { method: "POST", body: JSON.stringify({ category_code: categoryCode }) }); }
  submitAttempt(attemptId, answers) { return this.transport.request(endpoint.submission(attemptId), { method: "PUT", body: JSON.stringify({ answers }) }); }
}

class UiSessionStore {
  participant() {
    try { return JSON.parse(sessionStorage.getItem(UI_PARTICIPANT_KEY) || "null"); } catch { return null; }
  }
  saveParticipant(participant) { sessionStorage.setItem(UI_PARTICIPANT_KEY, JSON.stringify(participant)); }
  lastAttemptId() { return sessionStorage.getItem(UI_LAST_ATTEMPT_KEY); }
  saveLastAttemptId(attemptId) { sessionStorage.setItem(UI_LAST_ATTEMPT_KEY, attemptId); }
  clear() {
    sessionStorage.removeItem(UI_PARTICIPANT_KEY);
    sessionStorage.removeItem(UI_LAST_ATTEMPT_KEY);
  }
  draft(attemptId) {
    try { return JSON.parse(sessionStorage.getItem(`${DRAFT_PREFIX}${attemptId}`) || "{}"); } catch { return {}; }
  }
  saveDraft(attemptId, answers) { sessionStorage.setItem(`${DRAFT_PREFIX}${attemptId}`, JSON.stringify(answers)); }
  clearDraft(attemptId) { sessionStorage.removeItem(`${DRAFT_PREFIX}${attemptId}`); }
  clearAllDrafts() {
    for (const key of Object.keys(sessionStorage)) {
      if (key.startsWith(DRAFT_PREFIX)) sessionStorage.removeItem(key);
    }
  }
}

class ModalController {
  constructor(layer, pageRoot) {
    this.layer = layer;
    this.dialog = layer.querySelector('[role="dialog"]');
    this.pageRoot = pageRoot;
    this.trigger = null;
    this.scrollY = 0;
    this.onKeydown = this.onKeydown.bind(this);
  }

  focusable() {
    return [...this.dialog.querySelectorAll('button:not([disabled]), [href], input:not([disabled]), [tabindex]:not([tabindex="-1"])')];
  }

  open({ trigger, windowTitle, eyebrow, title, message, detail, actions }) {
    this.trigger = trigger || document.activeElement;
    document.getElementById("modalWindowTitle").textContent = windowTitle;
    document.getElementById("modalEyebrow").textContent = eyebrow;
    document.getElementById("modalTitle").textContent = title;
    document.getElementById("modalMessage").textContent = message;
    document.getElementById("modalDetail").textContent = detail;
    const actionRoot = document.getElementById("modalActions");
    actionRoot.replaceChildren();
    actions.forEach((action) => {
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = action.label;
      if (action.secondary) button.classList.add("is-secondary");
      button.addEventListener("click", safeAsync(async () => action.run(button)));
      actionRoot.append(button);
    });

    this.scrollY = window.scrollY;
    this.pageRoot.inert = true;
    document.body.classList.add("modal-open");
    document.body.style.top = `-${this.scrollY}px`;
    this.layer.hidden = false;
    document.addEventListener("keydown", this.onKeydown, true);
    requestAnimationFrame(() => this.focusable()[0]?.focus());
  }

  close({ restoreFocus = true } = {}) {
    if (this.layer.hidden) return;
    this.layer.hidden = true;
    this.pageRoot.inert = false;
    document.body.classList.remove("modal-open");
    document.body.style.top = "";
    document.removeEventListener("keydown", this.onKeydown, true);
    window.scrollTo({ top: this.scrollY, behavior: "auto" });
    const trigger = this.trigger;
    this.trigger = null;
    if (restoreFocus) {
      requestAnimationFrame(() => {
        if (trigger?.isConnected && trigger.getClientRects().length) trigger.focus();
      });
    }
  }

  onKeydown(event) {
    if (event.key === "Escape") {
      event.preventDefault();
      this.close();
      return;
    }
    if (event.key !== "Tab") return;
    const focusable = this.focusable();
    if (!focusable.length) {
      event.preventDefault();
      this.dialog.focus();
      return;
    }
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  }
}

const transport = MOCK_MODE ? new MockTransport() : new HttpTransport();
const api = new QuizApi(transport);
const uiStore = new UiSessionStore();
const modal = new ModalController(document.getElementById("modalLayer"), document.getElementById("pageRoot"));
const initialParticipantElement = document.getElementById("initialParticipant");
const initialParticipant = initialParticipantElement
  ? JSON.parse(initialParticipantElement.textContent)
  : null;
const initialAttemptElement = document.getElementById("initialAttempt");
const initialAttempt = initialAttemptElement
  ? JSON.parse(initialAttemptElement.textContent)
  : null;
// A previous tab's UI cache cannot authenticate the current server session.
if (!MOCK_MODE) {
  if (initialParticipant) uiStore.saveParticipant(initialParticipant);
  else uiStore.clear();
}

function safeAsync(action) {
  return async (...args) => {
    try {
      await action(...args);
    } catch (error) {
      app.handleUnexpected(error);
    }
  };
}

function statusLabel(status) {
  return {
    draft: "活动尚未开放",
    open: "活动进行中",
    paused: "活动已暂停",
    closed: "活动已关闭",
  }[status] || `活动状态：${status}`;
}

function deadlineText(isoValue) {
  if (!isoValue) return "—";
  return new Intl.DateTimeFormat("zh-CN", { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false }).format(new Date(isoValue));
}

function errorMessage(error) {
  if (!(error instanceof ApiError)) return "发生未预期错误，请重试。";
  const firstField = Object.values(error.fieldErrors).flat()[0];
  return firstField || error.message;
}

const app = {
  activity: null,
  participant: null,
  currentAttempt: null,
  resultAttempt: null,
  currentQuestionIndex: 0,
  answers: {},
  countdownTimer: null,
  checkingTimeout: false,
  initialStateConsumed: false,
  retryActions: new Map(),
  historyPage: 1,
  historyPages: 1,
  historySequence: 0,
  historyOwner: null,

  screen(name) {
    if (name !== "history") this.historySequence++;
    document.body.dataset.screen = name;
    document.querySelectorAll("[data-screen-panel]").forEach((panel) => {
      panel.classList.toggle("is-active", panel.dataset.screenPanel === name);
    });
    const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;
    window.scrollTo({ top: 0, behavior: reduced ? "auto" : "smooth" });
  },

  announce(message) {
    document.getElementById("globalAnnouncer").textContent = message;
  },

  feedback(id, kind = "", message = "", retry = null) {
    const root = document.getElementById(id);
    root.className = `operation-feedback${kind ? ` is-${kind}` : ""}`;
    root.replaceChildren();
    if (!message) return;
    const text = document.createElement("span");
    text.textContent = message;
    root.append(text);
    if (retry) {
      const retryKey = crypto.randomUUID();
      this.retryActions.set(retryKey, retry);
      const button = document.createElement("button");
      button.type = "button";
      button.dataset.retryKey = retryKey;
      button.textContent = "重试";
      root.append(button);
    }
  },

  errorFeedback(id, error, retry = null) {
    this.feedback(id, "error", errorMessage(error), error?.retryable ? retry : null);
    if (error?.status !== 429) return;
    const root = document.getElementById(id);
    root.querySelector("span").dataset.cooldownMessage = error.operation;
    cooldowns.button(root.querySelector("button"), error.operation);
    cooldowns.sync();
  },

  handleUnexpected(error) {
    this.errorFeedback("quizFeedback", error, () => this.bootstrap());
    this.announce(errorMessage(error));
  },

  renderActivity() {
    const activity = this.activity;
    document.getElementById("activityStatusText").textContent = statusLabel(activity.status);
    const firstCategory = activity.categories[0];
    document.getElementById("activityRuleText").textContent = firstCategory
      ? `${firstCategory.question_count} 题 · ${Math.round(firstCategory.time_limit_seconds / 60)} 分钟 · 服务端计时`
      : "暂无可用板块";
    document.getElementById("activityCodeText").textContent = `${activity.code} / PARTICIPANT SESSION`;
    const chip = document.getElementById("activityChip");
    chip.dataset.status = activity.status;

    const notice = document.getElementById("activityNotice");
    const form = document.getElementById("registrationForm");
    const entryAllowed = ["open", "paused", "closed"].includes(activity.status);
    [...form.elements].forEach((control) => { control.disabled = !entryAllowed; });
    cooldowns.button(document.getElementById("registrationSubmit"), "registration", !entryAllowed);
    notice.hidden = activity.status === "open";
    if (activity.status !== "open") {
      notice.textContent = {
        draft: "活动尚未开放，暂时不能登记或开始挑战。",
        paused: "活动暂时暂停，仅允许已登记参与者再次进入并恢复已有答题。",
        closed: "活动已经关闭，仅允许已登记参与者再次进入、完成原截止时间内的答题或查看结果。",
      }[activity.status] || "当前活动不可进入。";
    }
  },

  async bootstrap() {
    this.stopCountdown();
    this.currentAttempt = null;
    this.resultAttempt = null;
    this.currentQuestionIndex = 0;
    this.answers = {};
    this.screen("boot");
    try {
      const activityResponse = await api.activity();
      this.activity = activityResponse.activity;
      this.participant = uiStore.participant();
      this.renderActivity();

      if (!this.participant) {
        this.initialStateConsumed = true;
        this.screen("register");
        return;
      }

      if (!this.initialStateConsumed) {
        this.initialStateConsumed = true;
        if (initialAttempt) {
          this.currentAttempt = initialAttempt;
          uiStore.saveLastAttemptId(initialAttempt.id);
          this.routeAttempt(initialAttempt);
        } else {
          this.renderSections();
        }
        return;
      }

      try {
        const currentResponse = await api.currentAttempt();
        this.currentAttempt = currentResponse.attempt;
        uiStore.saveLastAttemptId(this.currentAttempt.id);
        this.routeAttempt(this.currentAttempt);
        return;
      } catch (error) {
        if (!(error instanceof ApiError) || !["attempt_not_found", "participant_session_required"].includes(error.code)) throw error;
        if (error.code === "participant_session_required") {
          uiStore.clear();
          uiStore.clearAllDrafts();
          this.participant = null;
          this.screen("register");
          return;
        }
      }

      const lastAttemptId = uiStore.lastAttemptId();
      if (lastAttemptId) {
        try {
          const detail = await api.attempt(lastAttemptId);
          if (["submitted", "timed_out", "invalid"].includes(detail.attempt.status)) {
            this.routeAttempt(detail.attempt);
            return;
          }
        } catch (error) {
          if (!(error instanceof ApiError) || error.code !== "attempt_not_found") throw error;
        }
      }
      this.renderSections();
    } catch (error) {
      document.getElementById("activityStatusText").textContent = "活动信息加载失败";
      this.errorFeedback("registrationFeedback", error, () => this.bootstrap());
      this.screen("register");
    }
  },

  routeAttempt(attempt) {
    if (attempt.status === "in_progress") this.renderQuiz(attempt);
    else if (attempt.status === "submitted") this.renderResult(attempt);
    else if (attempt.status === "timed_out") this.renderTimeout(attempt);
    else this.renderSections();
  },

  renderSections() {
    this.stopCountdown();
    const participantName = this.participant?.display_name || "参与者";
    document.getElementById("participantWelcome").textContent = `CATEGORY_SELECT / 欢迎回来，${participantName}`;
    document.getElementById("sectionsDescription").textContent = "板块卡片只显示活动响应提供的题量和限时。板块最高分在交卷结果中显示。";
    const current = this.currentAttempt?.status === "in_progress" ? this.currentAttempt : null;
    const resumeBanner = document.getElementById("resumeBanner");
    resumeBanner.hidden = !current;
    if (current) {
      document.getElementById("resumeTitle").textContent = `发现未完成的「${current.category.title}」`;
      document.getElementById("resumeSummary").textContent = `截止 ${deadlineText(current.deadline_at)} · 本机暂存 ${Object.keys(uiStore.draft(current.id)).length} / ${current.question_count} 题`;
    }

    const canStart = this.activity.status === "open" && !current;
    const grid = document.getElementById("categoryGrid");
    grid.replaceChildren();
    this.activity.categories.forEach((category, index) => {
      const card = document.createElement("article");
      card.className = `category-card ${categoryPalette[index % categoryPalette.length]}`;
      const textElement = (tag, text, className = "") => {
        const element = document.createElement(tag);
        element.className = className;
        element.textContent = text;
        return element;
      };
      const scoreLine = document.createElement("div");
      scoreLine.className = "score-line";
      scoreLine.append(textElement("span", "本轮规则"), textElement("b", `${category.question_count} 题 · ${Math.round(category.time_limit_seconds / 60)} 分钟`));
      card.append(
        textElement("div", `FILE ${String(index + 1).padStart(2, "0")}`, "file-tab"),
        textElement("span", String(category.code).toUpperCase(), "category-code"),
        textElement("h2", category.title),
        textElement("p", "题目与选项由服务端在开始挑战时返回。"), scoreLine,
      );
      const button = document.createElement("button");
      button.type = "button";
      button.dataset.categoryCode = category.code;
      button.textContent = current ? "先继续当前答题" : canStart ? "开始挑战" : "当前不可开始";
      cooldowns.button(button, "start", !canStart);
      button.setAttribute("aria-disabled", String(!canStart));
      card.append(button);
      grid.append(card);
    });
    this.screen("sections");
  },

  historyControls(loading = false) {
    cooldowns.button(document.getElementById("historyRefresh"), "read", loading);
    cooldowns.button(document.getElementById("historyPrevious"), "read", loading || this.historyPage <= 1);
    cooldowns.button(document.getElementById("historyNext"), "read", loading || this.historyPage >= this.historyPages);
    document.querySelectorAll("[data-history-attempt]").forEach(button => cooldowns.button(button, "read", loading));
  },

  async loadHistory(page = 1) {
    if (!this.participant) { this.screen("register"); return; }
    this.stopCountdown();
    this.screen("history");
    const sequence = ++this.historySequence;
    const owner = this.participant.id;
    if (this.historyOwner !== owner) {
      document.getElementById("historyList").replaceChildren();
      document.getElementById("historyPageStatus").textContent = "";
      this.historyOwner = owner;
      this.historyPage = this.historyPages = 1;
    }
    this.historyControls(true);
    this.feedback("historyFeedback", "", "正在读取记录…");
    try {
      const data = await api.history(page);
      if (sequence !== this.historySequence || this.participant?.id !== owner) return;
      if (data.current_attempt_id === null && this.currentAttempt?.status === "in_progress") {
        // History can confirm an expiry while the quiz timer is not on screen.
        this.currentAttempt = null;
      }
      this.historyPage = data.pagination.page;
      this.historyPages = data.pagination.pages;
      document.getElementById("historyPageStatus").textContent = `第 ${this.historyPage} / ${this.historyPages} 页 · 共 ${data.pagination.total} 次答题`;
      const list = document.getElementById("historyList");
      list.replaceChildren();
      const text = (tag, value, className = "") => {
        const node = document.createElement(tag); node.textContent = value; node.className = className; return node;
      };
      const labels = { submitted: "已提交", in_progress: "进行中", timed_out: "已超时", invalid: "已作废" };
      data.attempts.forEach(attempt => {
        const card = document.createElement("article");card.className = "window-card history-card";
        const heading = document.createElement("div");heading.className = "window-title";
        heading.append(text("span", attempt.category.title), text("span", labels[attempt.status] || attempt.status));
        const content = document.createElement("div");content.className = "window-body";
        content.append(text("p", attempt.status === "submitted" ? `${attempt.score} / ${attempt.question_count}` : attempt.status === "invalid" ? "不计入有效成绩" : "未形成有效成绩", "history-score"));
        content.append(text("p", `开始：${recordTime(attempt.started_at)}`));
        content.append(text("p", attempt.submitted_at ? `交卷：${recordTime(attempt.submitted_at)}` : `截止：${recordTime(attempt.deadline_at)}`));
        if (["submitted", "in_progress"].includes(attempt.status)) {
          const button = text("button", attempt.status === "submitted" ? "查看结果" : "继续答题", "secondary-button");
          button.type = "button";button.dataset.historyAttempt = attempt.id;content.append(button);
        }
        card.append(heading, content);list.append(card);
      });
      if (!data.attempts.length) list.append(text("p", "还没有答题记录，返回板块即可开始挑战。", "history-notice"));
      this.feedback("historyFeedback");
    } catch (error) {
      if (sequence !== this.historySequence || this.participant?.id !== owner) return;
      if (error.status === 401) {
        document.getElementById("historyList").replaceChildren();
        document.getElementById("historyPageStatus").textContent = "";
        this.feedback("historyFeedback", "error", "登录状态已失效，请返回板块并用原登记信息再次进入。");
      } else {
        this.errorFeedback("historyFeedback", error, () => this.loadHistory(page));
      }
    } finally {
      if (sequence === this.historySequence) this.historyControls();
    }
  },

  async openHistoryAttempt(attemptId) {
    const sequence = ++this.historySequence;
    const owner = this.participant?.id;
    this.historyControls(true);
    this.feedback("historyFeedback", "", "正在读取本次答题…");
    try {
      const { attempt } = await api.attempt(attemptId);
      if (sequence !== this.historySequence || this.participant?.id !== owner) return;
      if (attempt.status === "submitted") this.renderResult(attempt, { historical: true });
      else if (attempt.status === "in_progress") this.routeAttempt(attempt);
      else if (attempt.status === "timed_out" && this.currentAttempt?.id === attempt.id) this.routeAttempt(attempt);
      else await this.loadHistory(this.historyPage);
    } catch (error) {
      if (sequence === this.historySequence && this.participant?.id === owner) {
        this.errorFeedback("historyFeedback", error, () => this.openHistoryAttempt(attemptId));
      }
    } finally {
      if (sequence === this.historySequence) this.historyControls();
    }
  },

  async register(form) {
    const submit = document.getElementById("registrationSubmit");
    if (submit.disabled) return;
    if (!validateRegistrationForm(form)) {
      this.feedback("registrationFeedback", "error", "请完整填写显示名、学号或工号和一种联系方式。");
      form.reportValidity();
      return;
    }
    const payload = Object.fromEntries(new FormData(form));
    delete payload.contact_type;
    cooldowns.button(submit, "registration", true);
    submit.querySelector("span:first-child").textContent = "正在登记…";
    this.feedback("registrationFeedback", "loading", "正在建立参与者 Session…");
    try {
      // Bootstrap can fail independently of registration on an unstable connection.
      if (!this.activity) {
        this.activity = (await api.activity()).activity;
        this.renderActivity();
        cooldowns.button(submit, "registration", true);
        if (!["open", "paused", "closed"].includes(this.activity.status)) {
          throw new ApiError({code: "activity_unavailable", message: "活动尚未开放，暂时不能登记。"});
        }
      }
      const response = await api.register(payload);
      this.participant = response.participant;
      uiStore.saveParticipant(response.participant);
      this.feedback("registrationFeedback", "success", response.created ? "登记成功。" : "已恢复原参与者记录。正在检查进行中答题…");
      if (response.created) {
        this.renderSections();
        return;
      }
      try {
        const current = await api.currentAttempt();
        this.currentAttempt = current.attempt;
        uiStore.saveLastAttemptId(current.attempt.id);
        this.routeAttempt(current.attempt);
      } catch (error) {
        if (error instanceof ApiError && error.code === "attempt_not_found") this.renderSections();
        else throw error;
      }
    } catch (error) {
      this.errorFeedback("registrationFeedback", error, () => this.register(form));
    } finally {
      cooldowns.button(submit, "registration", this.activity ? !["open", "paused", "closed"].includes(this.activity.status) : false);
      submit.querySelector("span:first-child").textContent = "登记并继续";
    }
  },

  async clearSession(trigger) {
    cooldowns.button(trigger, "exit", true);
    const feedbackId = {
      sections: "sessionFeedback",
      quiz: "quizFeedback",
      timeout: "timeoutFeedback",
      result: "resultFeedback",
    }[document.body.dataset.screen] || "registrationFeedback";
    this.feedback(feedbackId, "loading", "正在清除参与者 Session…");
    try {
      await api.clearSession();
      uiStore.clearAllDrafts();
      uiStore.clear();
      this.participant = null;
      this.currentAttempt = null;
      this.resultAttempt = null;
      document.getElementById("registrationForm").reset();
      this.syncContactMode();
      this.feedback(feedbackId);
      await this.bootstrap();
    } catch (error) {
      this.errorFeedback(feedbackId, error, () => this.clearSession(trigger));
    } finally {
      cooldowns.button(trigger, "exit");
    }
  },

  async startAttempt(categoryCode, trigger) {
    const original = trigger.textContent;
    cooldowns.button(trigger, "start", true);
    trigger.textContent = "正在开始…";
    this.feedback("attemptFeedback", "loading", "正在向服务端申请题目和截止时间…");
    try {
      const response = await api.startAttempt(categoryCode);
      this.currentAttempt = response.attempt;
      uiStore.saveLastAttemptId(response.attempt.id);
      this.feedback("attemptFeedback", "success", "答题记录已准备。将使用服务端返回的题目顺序和截止时间。");
      this.renderQuiz(response.attempt);
    } catch (error) {
      this.errorFeedback("attemptFeedback", error, () => this.startAttempt(categoryCode, trigger));
    } finally {
      cooldowns.button(trigger, "start");
      trigger.textContent = original;
    }
  },

  async resumeAttempt(trigger) {
    cooldowns.button(trigger, "read", true);
    const original = trigger.textContent;
    trigger.textContent = "正在恢复…";
    this.feedback("attemptFeedback", "loading", "正在读取当前答题和服务端状态…");
    try {
      const response = await api.currentAttempt();
      this.currentAttempt = response.attempt;
      uiStore.saveLastAttemptId(response.attempt.id);
      this.routeAttempt(response.attempt);
    } catch (error) {
      this.errorFeedback("attemptFeedback", error, () => this.resumeAttempt(trigger));
    } finally {
      cooldowns.button(trigger, "read");
      trigger.textContent = original;
    }
  },

  renderQuiz(attempt) {
    this.currentAttempt = attempt;
    this.answers = uiStore.draft(attempt.id);
    this.currentQuestionIndex = Math.min(this.currentQuestionIndex, attempt.questions.length - 1);
    document.getElementById("quizCategoryTitle").textContent = attempt.category.title;
    document.getElementById("quizQuestionCount").textContent = attempt.question_count;
    this.renderQuestionMap();
    this.renderCurrentQuestion();
    this.startCountdown();
    this.feedback("quizFeedback");
    this.screen("quiz");
  },

  renderQuestionMap() {
    const map = document.getElementById("questionMap");
    map.replaceChildren();
    this.currentAttempt.questions.forEach((question, index) => {
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = String(question.position).padStart(2, "0");
      const answered = Object.hasOwn(this.answers, question.id);
      button.classList.toggle("is-answered", answered);
      button.classList.toggle("is-current", index === this.currentQuestionIndex);
      button.setAttribute("aria-current", index === this.currentQuestionIndex ? "step" : "false");
      button.setAttribute("aria-label", `第 ${question.position} 题${answered ? "，已暂存" : "，未作答"}`);
      button.addEventListener("click", () => {
        this.currentQuestionIndex = index;
        this.renderQuestionMap();
        this.renderCurrentQuestion();
      });
      map.append(button);
    });
  },

  renderCurrentQuestion() {
    const question = this.currentAttempt.questions[this.currentQuestionIndex];
    document.getElementById("questionNumber").textContent = question.position;
    document.getElementById("questionWindowTitle").textContent = `QUESTION_${String(question.position).padStart(3, "0")}.json`;
    document.getElementById("questionTypeText").textContent = question.type === "single_choice" ? "单选题" : "填空题";
    renderQuestionText(document.getElementById("questionPrompt"), question.prompt);

    const media = document.getElementById("questionMedia");
    const image = document.getElementById("questionImage");
    media.hidden = !question.image_url;
    if (question.image_url) {
      media.classList.remove("is-error");
      document.getElementById("questionImageCaption").textContent = "题目配图";
      image.onload = () => media.classList.remove("is-error");
      image.onerror = () => {
        media.classList.add("is-error");
        document.getElementById("questionImageCaption").textContent = "题图加载失败，请检查网络后刷新页面。";
      };
      image.src = question.image_url;
      image.alt = `第 ${question.position} 题题图`;
    } else {
      image.onload = null;
      image.onerror = null;
      image.removeAttribute("src");
      image.alt = "";
    }

    const optionRoot = document.getElementById("answerOptions");
    optionRoot.replaceChildren();
    const savedAnswer = this.answers[question.id] ?? null;
    if (question.type === "fill_blank") {
      const label = document.createElement("label");
      label.className = "fill-blank-answer";
      const prompt = document.createElement("span");
      prompt.textContent = "填写答案";
      const input = document.createElement("input");
      input.type = "text";
      input.name = `answer-${question.id}`;
      input.maxLength = 500;
      input.autocomplete = "off";
      input.value = savedAnswer || "";
      input.addEventListener("input", () => {
        if (input.value) this.answers[question.id] = input.value;
        else delete this.answers[question.id];
        uiStore.saveDraft(this.currentAttempt.id, this.answers);
        this.renderQuestionMap();
        this.updateQuestionStatus();
      });
      label.append(prompt, input);
      optionRoot.append(label);
    } else {
      question.options.forEach((option) => {
        const label = document.createElement("label");
        const input = document.createElement("input");
        input.type = "radio";
        input.name = `answer-${question.id}`;
        input.value = option.id;
        input.checked = savedAnswer === option.id;
        input.addEventListener("change", () => {
          this.answers[question.id] = option.id;
          uiStore.saveDraft(this.currentAttempt.id, this.answers);
          this.renderQuestionMap();
          this.updateQuestionStatus();
        });
        const key = document.createElement("span");
        key.className = "option-key";
        key.textContent = option.id;
        const text = document.createElement("span");
        renderQuestionText(text, option.text);
        label.append(input, key, text);
        optionRoot.append(label);
      });
    }
    this.updateQuestionStatus();
    this.updateNavigationButtons();
  },

  updateQuestionStatus() {
    const question = this.currentAttempt.questions[this.currentQuestionIndex];
    const answered = Object.hasOwn(this.answers, question.id);
    document.getElementById("saveState").textContent = answered ? "本机暂存 · 当前标签页" : "尚未作答";
    document.getElementById("answerProgress").textContent = `已暂存 ${Object.keys(this.answers).length} / ${this.currentAttempt.question_count}`;
  },

  updateNavigationButtons() {
    const previous = document.getElementById("previousQuestion");
    const next = document.getElementById("nextQuestion");
    const first = this.currentQuestionIndex === 0;
    const last = this.currentQuestionIndex === this.currentAttempt.questions.length - 1;
    previous.disabled = first;
    previous.setAttribute("aria-disabled", String(first));
    next.disabled = last;
    next.setAttribute("aria-disabled", String(last));
    next.textContent = last ? "已经是最后一题" : "下一题 →";
  },

  moveQuestion(delta) {
    const target = this.currentQuestionIndex + delta;
    if (target < 0 || target >= this.currentAttempt.questions.length) return;
    this.currentQuestionIndex = target;
    this.renderQuestionMap();
    this.renderCurrentQuestion();
  },

  startCountdown() {
    this.stopCountdown();
    const tick = () => {
      const remainingMs = Date.parse(this.currentAttempt.deadline_at) - Date.now();
      const remainingSeconds = Math.max(0, Math.ceil(remainingMs / 1000));
      const minutes = Math.floor(remainingSeconds / 60);
      const seconds = remainingSeconds % 60;
      document.getElementById("countdown").textContent = `${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;
      document.getElementById("timerBox").classList.toggle("is-urgent", remainingSeconds <= 30);
      if (remainingMs <= 0) {
        this.stopCountdown();
        this.confirmTimeoutFromServer();
      }
    };
    tick();
    this.countdownTimer = window.setInterval(tick, 250);
  },

  stopCountdown() {
    if (this.countdownTimer) window.clearInterval(this.countdownTimer);
    this.countdownTimer = null;
  },

  async confirmTimeoutFromServer() {
    if (this.checkingTimeout) return;
    this.checkingTimeout = true;
    this.feedback("quizFeedback", "loading", "倒计时已归零，正在读取服务端状态…");
    try {
      const response = await api.currentAttempt();
      this.currentAttempt = response.attempt;
      if (response.attempt.status === "timed_out") this.renderTimeout(response.attempt);
      else {
        this.feedback("quizFeedback", "loading", "服务端仍将本轮视为进行中，正在再次确认…");
        window.setTimeout(() => this.confirmTimeoutFromServer(), 500);
      }
    } catch (error) {
      if (error instanceof ApiError && error.code === "attempt_not_found") {
        const detail = await api.attempt(this.currentAttempt.id);
        this.routeAttempt(detail.attempt);
      } else {
        this.errorFeedback("quizFeedback", error, () => this.confirmTimeoutFromServer());
      }
    } finally {
      this.checkingTimeout = false;
    }
  },

  openSubmitModal(trigger) {
    cooldowns.button(trigger, "submit");
    if (cooldowns.remaining("submit")) return;
    const unanswered = this.currentAttempt.question_count - Object.keys(this.answers).length;
    modal.open({
      trigger,
      windowTitle: "SUBMISSION_CONFIRM.notice",
      eyebrow: `SUBMISSION / ${unanswered} UNANSWERED`,
      title: "现在交卷吗？",
      message: "交卷会把当前标签页中的答案一次提交给服务端。未作答题目按未答处理。",
      detail: "首次有效提交封存结果。重复提交只返回第一次结果，不会覆盖答案。",
      actions: [
        { label: "确认交卷", run: async (button) => this.submit(button) },
        { label: "继续作答", secondary: true, run: async () => modal.close() },
      ],
    });
  },

  async submit(button) {
    cooldowns.button(button, "submit", true);
    button.textContent = "正在提交…";
    this.feedback("quizFeedback", "loading", "正在一次提交全部答案…");
    const answers = Object.entries(this.answers).map(([itemId, answer]) => ({ item_id: itemId, answer }));
    try {
      const response = await api.submitAttempt(this.currentAttempt.id, answers);
      modal.close({ restoreFocus: false });
      uiStore.clearDraft(this.currentAttempt.id);
      uiStore.saveLastAttemptId(response.attempt.id);
      this.renderResult(response.attempt);
    } catch (error) {
      modal.close();
      if (error instanceof ApiError && error.code === "attempt_expired") {
        await this.confirmTimeoutFromServer();
      } else {
        this.errorFeedback("quizFeedback", error, () => this.openSubmitModal(document.getElementById("submitAttempt")));
      }
    } finally {
      cooldowns.button(button, "submit");
      cooldowns.button(document.getElementById("submitAttempt"), "submit");
      button.textContent = "确认交卷";
    }
  },

  renderTimeout(attempt) {
    this.stopCountdown();
    if (!modal.layer.hidden) modal.close({ restoreFocus: false });
    this.currentAttempt = attempt;
    uiStore.clearDraft(attempt.id);
    uiStore.saveLastAttemptId(attempt.id);
    document.getElementById("timeoutCategory").textContent = attempt.category.title;
    document.getElementById("timeoutDeadline").textContent = deadlineText(attempt.deadline_at);
    document.getElementById("timeoutStatus").textContent = attempt.status;
    this.screen("timeout");
    const heading = document.getElementById("timeoutTitle");
    heading.tabIndex = -1;
    requestAnimationFrame(() => heading.focus());
  },

  renderResult(attempt, { historical = false } = {}) {
    this.stopCountdown();
    if (!modal.layer.hidden) modal.close({ restoreFocus: false });
    this.resultAttempt = attempt;
    if (!historical) {
      this.currentAttempt = null;
      uiStore.saveLastAttemptId(attempt.id);
    }
    document.getElementById("resultCategory").textContent = `${attempt.category.title} / ${historical ? "历史答题" : "本次挑战"}`;
    document.getElementById("resultScore").textContent = attempt.score;
    document.getElementById("resultTotal").textContent = attempt.question_count;
    document.getElementById("resultHighScore").textContent = `${attempt.category_high_score} / ${attempt.question_count}`;
    document.getElementById("resultStatus").textContent = attempt.status;
    document.getElementById("resultSubmittedAt").textContent = attempt.submitted_at
      ? `交卷时间：${recordTime(attempt.submitted_at)}（北京时间）` : "";
    const rewardBox = document.getElementById("rewardBox");
    rewardBox.hidden = !attempt.reward_phrase;
    document.getElementById("rewardPhrase").textContent = attempt.reward_phrase || "";

    const correctCount = attempt.questions.filter((question) => question.correct).length;
    document.getElementById("resultBreakdown").textContent = `${correctCount} 对 · ${attempt.question_count - correctCount} 错`;
    const list = document.getElementById("reviewList");
    list.replaceChildren();
    attempt.questions.forEach((question) => {
      const item = document.createElement("li");
      item.className = question.correct ? "is-correct" : "is-wrong";
      const position = document.createElement("span");
      position.textContent = String(question.position).padStart(2, "0");
      const prompt = document.createElement("b");
      renderQuestionText(prompt, question.prompt);
      const result = document.createElement("em");
      result.textContent = question.correct ? "✓" : "×";
      result.setAttribute("aria-label", question.correct ? "正确" : "错误");
      item.append(position, prompt, result);
      list.append(item);
    });
    this.screen("result");
    const heading = document.getElementById("resultTitle");
    heading.tabIndex = -1;
    requestAnimationFrame(() => heading.focus());
  },

  syncContactMode() {
    const form = document.getElementById("registrationForm");
    const selected = form.elements.contact_type.value || "phone";
    const contact = form.elements.contact;
    const phone = selected === "phone";
    contact.autocomplete = phone ? "tel" : "email";
    contact.inputMode = phone ? "tel" : "email";
    contact.placeholder = phone ? "例如：138 0000 0000" : "例如：name@example.com";
  },
};

function syncReviewSession(metadata) {
  uiStore.clear();
  uiStore.clearAllDrafts();
  if (metadata.participant) uiStore.saveParticipant(metadata.participant);
  if (metadata.attemptId) uiStore.saveLastAttemptId(metadata.attemptId);
}

function recordTime(value) {
  if (!value || !Number.isFinite(new Date(value).getTime())) return "—";
  return new Date(value).toLocaleString("zh-CN", { timeZone: "Asia/Shanghai", hour12: false });
}

function bindEvents() {
  document.querySelectorAll("[data-open-history]").forEach(button => button.addEventListener("click", safeAsync(() => app.loadHistory())));
  document.getElementById("historyBackToSections").addEventListener("click", () => app.renderSections());
  document.getElementById("historyRefresh").addEventListener("click", safeAsync(() => app.loadHistory(app.historyPage)));
  document.getElementById("historyPrevious").addEventListener("click", safeAsync(() => app.loadHistory(app.historyPage - 1)));
  document.getElementById("historyNext").addEventListener("click", safeAsync(() => app.loadHistory(app.historyPage + 1)));
  document.getElementById("historyList").addEventListener("click", safeAsync(event => {
    const button = event.target.closest("[data-history-attempt]");
    if (button && !button.disabled) return app.openHistoryAttempt(button.dataset.historyAttempt);
  }));
  document.querySelector("#registrationForm")?.addEventListener("input", event => { if (event.target.setCustomValidity) event.target.setCustomValidity(""); });
  const form = document.getElementById("registrationForm");
  form.addEventListener("submit", safeAsync(async (event) => {
    event.preventDefault();
    await app.register(form);
  }));
  form.addEventListener("change", (event) => {
    if (event.target.name === "contact_type") app.syncContactMode();
  });
  form.addEventListener("reset", () => requestAnimationFrame(() => app.syncContactMode()));

  document.getElementById("categoryGrid").addEventListener("click", safeAsync(async (event) => {
    const button = event.target.closest("[data-category-code]");
    if (button) await app.startAttempt(button.dataset.categoryCode, button);
  }));
  document.getElementById("resumeAttemptButton").addEventListener("click", safeAsync(async (event) => app.resumeAttempt(event.currentTarget)));
  document.getElementById("clearSessionButton").addEventListener("click", safeAsync(async (event) => app.clearSession(event.currentTarget)));
  document.getElementById("timeoutClearSession").addEventListener("click", safeAsync(async (event) => app.clearSession(event.currentTarget)));
  document.getElementById("resultClearSession").addEventListener("click", safeAsync(async (event) => app.clearSession(event.currentTarget)));
  document.getElementById("backToSections").addEventListener("click", () => app.renderSections());
  document.getElementById("timeoutBackToSections").addEventListener("click", () => app.renderSections());
  document.getElementById("resultBackToSections").addEventListener("click", () => app.renderSections());
  document.getElementById("previousQuestion").addEventListener("click", () => app.moveQuestion(-1));
  document.getElementById("nextQuestion").addEventListener("click", () => app.moveQuestion(1));
  document.getElementById("submitAttempt").addEventListener("click", (event) => app.openSubmitModal(event.currentTarget));

  document.getElementById("modalLayer").addEventListener("click", (event) => {
    if (event.target.closest("[data-close-modal]")) modal.close();
  });

  document.addEventListener("click", safeAsync(async (event) => {
    const retryButton = event.target.closest("[data-retry-key]");
    if (!retryButton) return;
    const retry = app.retryActions.get(retryButton.dataset.retryKey);
    app.retryActions.delete(retryButton.dataset.retryKey);
    if (retry) await retry();
  }));

  const reviewControls = document.getElementById("reviewControls");
  if (reviewControls) {
    reviewControls.addEventListener("click", safeAsync(async (event) => {
      const button = event.target.closest("[data-review-scenario]");
      if (!button || !MOCK_MODE) return;
      const metadata = transport.setScenario(button.dataset.reviewScenario);
      syncReviewSession(metadata);
      const url = new URL(window.location.href);
      url.searchParams.set("scenario", button.dataset.reviewScenario);
      history.replaceState(null, "", url);
      document.querySelectorAll("[data-review-scenario]").forEach((entry) => {
        entry.classList.toggle("is-current", entry === button);
      });
      await app.bootstrap();
    }));
  }
}

function initializeReviewScenario() {
  if (!MOCK_MODE) {
    const reviewControls = document.getElementById("reviewControls");
    if (reviewControls) reviewControls.hidden = true;
    return;
  }
  const requested = new URLSearchParams(window.location.search).get("scenario");
  if (requested && requested !== transport.state.scenario) {
    syncReviewSession(transport.setScenario(requested));
  } else if (transport.state.sessionParticipant && !uiStore.participant()) {
    uiStore.saveParticipant(transport.state.sessionParticipant);
    if (transport.state.attempt) uiStore.saveLastAttemptId(transport.state.attempt.id);
  }
  document.querySelectorAll("[data-review-scenario]").forEach((button) => {
    button.classList.toggle("is-current", button.dataset.reviewScenario === transport.state.scenario);
  });
}

window.addEventListener("unhandledrejection", (event) => {
  event.preventDefault();
  app.handleUnexpected(event.reason);
});
window.addEventListener("error", (event) => {
  if (!event.error) return;
  event.preventDefault();
  app.handleUnexpected(event.error);
});

bindEvents();
initializeReviewScenario();
app.syncContactMode();

window.__prototypeReview = MOCK_MODE ? {
  setScenario: async (name) => {
    const metadata = transport.setScenario(name);
    syncReviewSession(metadata);
    await app.bootstrap();
  },
  failNext: (operation, retryable = true) => transport.failNext(operation, retryable),
  expireSoon: async (milliseconds = 800) => {
    transport.expireSoon(milliseconds);
    await app.bootstrap();
  },
  state: () => deepClone({ app: {
    activity: app.activity,
    participant: app.participant,
    currentAttempt: app.currentAttempt,
    resultAttempt: app.resultAttempt,
    answers: app.answers,
  }, mock: transport.state }),
} : null;

app.bootstrap().finally(() => {
  window.__prototypeReady = true;
});
