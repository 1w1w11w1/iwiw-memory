window.__ModuleLoader__.load({
  id: "@iwiw/dsh-iwiw-memory",
  factory: (require) => {
    var module = { exports: {} };
    var exports = module.exports;

"use strict";
var __create = Object.create;
var __defProp = Object.defineProperty;
var __getOwnPropDesc = Object.getOwnPropertyDescriptor;
var __getOwnPropNames = Object.getOwnPropertyNames;
var __getProtoOf = Object.getPrototypeOf;
var __hasOwnProp = Object.prototype.hasOwnProperty;
var __export = (target, all) => {
  for (var name in all)
    __defProp(target, name, { get: all[name], enumerable: true });
};
var __copyProps = (to, from, except, desc) => {
  if (from && typeof from === "object" || typeof from === "function") {
    for (let key of __getOwnPropNames(from))
      if (!__hasOwnProp.call(to, key) && key !== except)
        __defProp(to, key, { get: () => from[key], enumerable: !(desc = __getOwnPropDesc(from, key)) || desc.enumerable });
  }
  return to;
};
var __toESM = (mod, isNodeMode, target) => (target = mod != null ? __create(__getProtoOf(mod)) : {}, __copyProps(
  // If the importer is in node compatibility mode or this is not an ESM
  // file that has been converted to a CommonJS file using a Babel-
  // compatible transform (i.e. "__esModule" has not been set), then set
  // "default" to the CommonJS "module.exports" for node compatibility.
  isNodeMode || !mod || !mod.__esModule ? __defProp(target, "default", { value: mod, enumerable: true }) : target,
  mod
));
var __toCommonJS = (mod) => __copyProps(__defProp({}, "__esModule", { value: true }), mod);

// src/client.ts
var client_exports = {};
__export(client_exports, {
  MemoryFoldDock: () => MemoryFoldDock,
  apply: () => apply,
  inject: () => inject
});
module.exports = __toCommonJS(client_exports);
var import_react = require("react");

// src/client-fold.ts
var REFLECT_MARKER = "[iwiw-memory-reflect]";
var DREAM_MARKER = "[iwiw-memory-dream]";
var PLUGIN_NAME = "dsh-iwiw-memory";
function turnOf(node) {
  const location = node.location;
  if (location?.kind === "turn") return location.turn?.turn;
  if (location?.kind === "step") return location.turn?.turn;
  return void 0;
}
function contextText(node) {
  return blocksToText(node.data.content ?? []);
}
function isMemoryPrompt(node) {
  if (node.kind !== "context") return false;
  const source = node.data.source;
  if (source?.kind !== "plugin" || source.plugin !== PLUGIN_NAME) return false;
  const text = contextText(node);
  return text.includes(REFLECT_MARKER) || text.includes(DREAM_MARKER);
}
function variantOf(node) {
  const text = contextText(node);
  return text.includes(DREAM_MARKER) ? "dream" : "reflect";
}
function toolNameOf(node) {
  if (node.kind !== "tool-call") return void 0;
  const root = node.data.root;
  if (root === void 0) return void 0;
  if ("name" in root) return root.name;
  return root.call?.name;
}
function computeFoldGroups(snapshot) {
  if (snapshot?.chat === void 0) return [];
  const order = snapshot.chat.order;
  const nodes = snapshot.chat.nodes;
  const groups = [];
  for (const key of order) {
    const node = nodes.get(key);
    if (node === void 0 || !isMemoryPrompt(node)) continue;
    const turn = turnOf(node);
    if (turn === void 0) continue;
    const turnKeys = snapshot.chat.locations.getTurn(turn);
    const startIdx = turnKeys.indexOf(key);
    const keys = turnKeys.slice(startIdx === -1 ? 0 : startIdx).filter((k) => {
      const n = nodes.get(k);
      return n !== void 0 && n.kind !== "user" && n.kind !== "steering" && n.kind !== "turn-tail";
    });
    let rememberCount = 0;
    let updateCount = 0;
    let status = "done";
    for (const k of keys) {
      const n = nodes.get(k);
      if (n === void 0) continue;
      const name = toolNameOf(n);
      if (name === "memory_remember") rememberCount++;
      else if (name === "memory_update") updateCount++;
      if (n.kind === "assistant") {
        const data = n.data;
        if (data.status === "running") status = "running";
        else if (data.status === "interrupted" && status !== "running") status = "interrupted";
      }
    }
    groups.push({ id: key, variant: variantOf(node), keys, rememberCount, updateCount, status });
  }
  return groups;
}
function foldLabel(group, expanded) {
  const arrow = expanded ? "\u25BE" : "\u25B8";
  const title = group.variant === "dream" ? "\u8BB0\u5FC6\u68A6\u5883\u4EFB\u52A1" : "\u8BB0\u5FC6\u53CD\u601D";
  if (group.status === "running") return `${arrow} ${title}\u8FDB\u884C\u4E2D\u2026`;
  if (group.status === "interrupted") return `${arrow} ${title}\u5DF2\u4E2D\u65AD`;
  if (group.rememberCount > 0) return `${arrow} ${title} \xB7 \u65B0\u589E\u8BB0\u5FC6 ${group.rememberCount} \u6761`;
  if (group.updateCount > 0) return `${arrow} ${title} \xB7 \u5DF2\u66F4\u65B0 ${group.updateCount} \u6761`;
  return `${arrow} ${title} \xB7 \u65E0\u9700\u8BB0\u5FC6`;
}
function toolCallDetail(block) {
  let args = block.argsRaw;
  try {
    args = JSON.stringify(JSON.parse(block.argsRaw), null, 2);
  } catch {
  }
  return args.length > 0 ? `${block.name}
${args}` : block.name;
}
function blocksToText(blocks) {
  return blocks.map((block) => block.text ?? "").join("\n").trim();
}
var FIRST_INJECTION_MARKER = "===== \u957F\u671F\u8BB0\u5FC6 =====";
var HIT_INJECTION_MARKER = "\u53EF\u80FD\u76F8\u5173\u7684\u8BB0\u5FC6\uFF0C\u4EC5\u4F9B\u53C2\u8003\uFF1A";
var PROMPT_SEPARATOR = "\u672C\u8F6E\u7528\u6237prompt\uFF1A";
var EN_PROMPT_SEPARATOR = "This turn's user prompt:";
function computeInjectionGroups(snapshot) {
  if (snapshot?.chat === void 0) return [];
  const groups = [];
  for (const key of snapshot.chat.order) {
    const node = snapshot.chat.nodes.get(key);
    if (node === void 0) continue;
    if (node.kind === "context") {
      const source = node.data.source;
      if (source?.kind !== "plugin" || source.plugin !== PLUGIN_NAME) continue;
      const memKind = source.memory?.kind;
      if (memKind === "reflect") {
        groups.push({ id: key, kind: "reflect", injectedText: contextText(node) });
        continue;
      }
      if (memKind === "dream-report") {
        groups.push({ id: key, kind: "dream", injectedText: contextText(node) });
        continue;
      }
      if (memKind === "initial" || memKind === "reinjection") {
        groups.push({ id: key, kind: "first", injectedText: contextText(node) });
        continue;
      }
      if (memKind === "hit") {
        groups.push({ id: key, kind: "hit", injectedText: contextText(node) });
        continue;
      }
      if (source.form === "snapshot") {
        const injectedText = contextText(node);
        const isFirst = injectedText.startsWith(FIRST_INJECTION_MARKER) || injectedText.includes("LONG-TERM MEMORY");
        groups.push({ id: key, kind: isFirst ? "first" : "hit", injectedText });
      }
      continue;
    }
    if (node.kind !== "user") continue;
    const content = node.data.content ?? [];
    if (content.length === 0 || content.some((b) => b.type !== "text")) continue;
    const text = blocksToText(content);
    if (text.length === 0) continue;
    let kind = null;
    if (text.startsWith(FIRST_INJECTION_MARKER) || text.includes("LONG-TERM MEMORY") || text.includes("===== \u957F\u671F\u8BB0\u5FC6 =====")) kind = "first";
    else if (text.startsWith(HIT_INJECTION_MARKER) || text.includes("Possibly relevant memories") || text.includes("\u53EF\u80FD\u76F8\u5173\u7684\u8BB0\u5FC6")) kind = "hit";
    if (kind === null) continue;
    const sep = text.includes(PROMPT_SEPARATOR) ? PROMPT_SEPARATOR : text.includes(EN_PROMPT_SEPARATOR) ? EN_PROMPT_SEPARATOR : null;
    if (sep === null) continue;
    const sepIdx = text.lastIndexOf(sep);
    const userText = text.slice(sepIdx + sep.length).replace(/^\n+/, "");
    const time = typeof node.data.time === "number" ? node.data.time : void 0;
    groups.push({ id: key, kind, injectedText: text.slice(0, sepIdx + sep.length), userText, time });
  }
  return groups;
}
function formatInjectionClock(time, now = Date.now()) {
  const d = new Date(time);
  const n = new Date(now);
  const pad = (value) => String(value).padStart(2, "0");
  const clock = `${pad(d.getHours())}:${pad(d.getMinutes())}`;
  const sameDay = d.getFullYear() === n.getFullYear() && d.getMonth() === n.getMonth() && d.getDate() === n.getDate();
  if (sameDay) return clock;
  const md = d.getFullYear() === n.getFullYear() ? `${d.getMonth() + 1}\u6708${d.getDate()}\u65E5` : `${d.getFullYear()}\u5E74${d.getMonth() + 1}\u6708${d.getDate()}\u65E5`;
  return `${md} ${clock}`;
}

// src/client-delegate-vanish.ts
var DELEGATE_LABEL_PREFIX = "meow-memory ";
function isMeowDelegateLabel(label) {
  return typeof label === "string" && label.startsWith(DELEGATE_LABEL_PREFIX);
}
function indexSubagentDescendantCount(byId, rootId) {
  if (byId === void 0 || rootId === void 0) return 0;
  let total = 0;
  for (const descendant of Object.values(byId)) {
    if (descendant?.origin !== "subagent") continue;
    const seen = /* @__PURE__ */ new Set();
    let node = descendant;
    while (node?.origin === "subagent" && typeof node.parentId === "string" && !seen.has(node.id ?? "")) {
      seen.add(node.id ?? "");
      if (node.parentId === rootId) {
        total += 1;
        break;
      }
      node = byId[node.parentId];
    }
  }
  return total;
}
function computeVanishDecision(state) {
  const current = state?.current;
  const catalog = current === void 0 ? void 0 : state?.subagentsByParent?.[current];
  const entries = catalog?.state === "ready" ? (catalog.entries ?? []).filter((e) => e?.kind === "child") : [];
  if (catalog?.state !== "ready") {
    return { known: false, hideTrigger: false, mineCount: 0, othersCount: 0 };
  }
  const mine = entries.filter((e) => isMeowDelegateLabel(e.label));
  const others = entries.length - mine.length;
  const lineageTotal = indexSubagentDescendantCount(state?.byId, current);
  const hideTrigger = others === 0 && mine.length > 0 && lineageTotal <= mine.length;
  return { known: true, hideTrigger, mineCount: mine.length, othersCount: others };
}
var SENTINEL_ATTR = "data-iwiw-vanish";
var ROW_ATTR = "data-iwiw-vanish-row";
function locateLineageRoot(sentinel) {
  let scope = sentinel?.parentElement ?? null;
  for (let hop = 0; scope !== null && hop < 5; hop += 1, scope = scope.parentElement) {
    const candidates = scope.querySelectorAll(':scope [class*="_root"]');
    for (const candidate of Array.from(candidates)) {
      if (candidate.contains(sentinel ?? null)) continue;
      if (candidate.querySelector(':scope [class*="_trigger"]') !== null) return candidate;
    }
  }
  return null;
}
function vanishRows(root = document) {
  const rows = root.querySelectorAll('[role="treeitem"]');
  for (const row of Array.from(rows)) {
    const match = isMeowDelegateLabel(row.getAttribute("aria-label"));
    const marked = row.getAttribute(ROW_ATTR) === "1";
    if (match && !marked) {
      row.setAttribute(ROW_ATTR, "1");
      row.style.display = "none";
    } else if (!match && marked) {
      row.removeAttribute(ROW_ATTR);
      row.style.display = "";
    }
  }
}
function applyTriggerVisibility(root, hide) {
  if (root === null || root === void 0) return;
  const trigger = root.querySelector(':scope [class*="_trigger"]');
  if (trigger === null) return;
  const next = hide ? "none" : "";
  if (trigger.style.display !== next) trigger.style.display = next;
}
function applyVanishDom(decision, sentinel) {
  try {
    vanishRows();
    if (decision.known) applyTriggerVisibility(locateLineageRoot(sentinel), decision.hideTrigger);
  } catch (e) {
    console.warn("[meow-memory] vanish DOM \u5E94\u7528\u5931\u8D25\uFF08fail-open\uFF0C\u4FDD\u6301\u5B98\u65B9\u663E\u793A\uFF09\uFF1A", e);
  }
}
function startVanishObserver(getApply) {
  let timer = 0;
  const observer = new MutationObserver(() => {
    window.clearTimeout(timer);
    timer = window.setTimeout(() => {
      const { decision, sentinel } = getApply();
      applyVanishDom(decision, sentinel);
    }, 80);
  });
  observer.observe(document.body, { childList: true, subtree: true });
  return () => {
    window.clearTimeout(timer);
    observer.disconnect();
  };
}

// src/client-dream-events.ts
var listeners = /* @__PURE__ */ new Set();
var pollTimer = 0;
var lastStates = /* @__PURE__ */ new Map();
var lastSkipped = /* @__PURE__ */ new Set();
var hasBaseline = false;
function emit(sessionId, state) {
  for (const listener of [...listeners]) {
    try {
      listener({ sessionId, state });
    } catch {
    }
  }
}
async function pollDreamEventsOnce() {
  let states;
  let skipped2;
  try {
    const [statesRes, skipRes] = await Promise.all([
      fetch("/meow-memory/dreamed-sessions", { cache: "no-store" }),
      fetch("/meow-memory/skip-dreams", { cache: "no-store" })
    ]);
    if (!statesRes.ok || !skipRes.ok) return;
    const statesData = await statesRes.json();
    const skipData = await skipRes.json();
    states = /* @__PURE__ */ new Map();
    if (Array.isArray(statesData.sessionIds)) {
      for (const id of statesData.sessionIds) if (typeof id === "string") states.set(id, "dreamed");
    }
    if (Array.isArray(statesData.dreamingIds)) {
      for (const id of statesData.dreamingIds) if (typeof id === "string") states.set(id, "dreaming");
    }
    skipped2 = /* @__PURE__ */ new Set();
    if (Array.isArray(skipData.sessionIds)) {
      for (const id of skipData.sessionIds) if (typeof id === "string") skipped2.add(id);
    }
  } catch {
    return;
  }
  if (!hasBaseline) {
    lastStates = states;
    lastSkipped = skipped2;
    hasBaseline = true;
    return;
  }
  for (const [sessionId, state] of states) {
    if (lastStates.get(sessionId) !== state) emit(sessionId, state);
  }
  for (const [sessionId, state] of lastStates) {
    if (!states.has(sessionId)) emit(sessionId, "active");
  }
  for (const sessionId of skipped2) {
    if (!lastSkipped.has(sessionId)) emit(sessionId, "skip");
  }
  for (const sessionId of lastSkipped) {
    if (!skipped2.has(sessionId)) emit(sessionId, "unskip");
  }
  lastStates = states;
  lastSkipped = skipped2;
}
function subscribeDreamEvents(listener) {
  listeners.add(listener);
  if (listeners.size === 1 && pollTimer === 0) {
    pollTimer = window.setInterval(() => {
      void pollDreamEventsOnce();
    }, 6e4);
    void pollDreamEventsOnce();
  }
  return () => {
    listeners.delete(listener);
    if (listeners.size === 0 && pollTimer !== 0) {
      window.clearInterval(pollTimer);
      pollTimer = 0;
    }
  };
}

// src/client-delegate-notice.ts
var REFLECT_DELEGATE_MARKER = "\u3010\u8BB0\u5FC6\u53CD\u601D\u6807\u8BB0\u3011";
var REFLECT_DONE_DELEGATE_MARKER = "\u3010\u8BB0\u5FC6\u53CD\u601D\u5B8C\u6210\u6807\u8BB0\u3011";
var DREAM_DELEGATE_MARKER = "\u3010\u8BB0\u5FC6\u6574\u7406\u6807\u8BB0\u3011";
var PLUGIN_NAME2 = "dsh-iwiw-memory";
var HIDDEN_ATTR = "data-iwiw-delegate-hidden";
var ANCHOR_ATTR = "data-iwiw-delegate-anchor";
var CSS_ID = "meow-meow-delegate-notice-css";
var NOTICE_CSS = `[${HIDDEN_ATTR}="true"]{display:none!important}`;
var REFLECT_FRESH_MS = 30 * 6e4;
var DREAM_RUNNING_STALE_MS = 30 * 6e4;
function noticeText(node) {
  const blocks = node.data?.content ?? [];
  return blocks.map((block) => block.text ?? "").join("\n").trim();
}
function delegateVariantOf(node) {
  if (node.kind !== "context") return void 0;
  const source = node.data?.source;
  if (source === void 0 || source === null || typeof source !== "object") return void 0;
  if (source.kind !== "plugin" || source.plugin !== PLUGIN_NAME2) return void 0;
  const memKind = source.memory?.kind;
  if (memKind === "reflect-marker") return "reflect";
  if (memKind === "reflect-done-marker") return "reflect-done";
  if (memKind === "dream-marker") return "dream";
  const text = noticeText(node);
  if (text.includes(REFLECT_DONE_DELEGATE_MARKER)) return "reflect-done";
  if (text.includes(REFLECT_DELEGATE_MARKER)) return "reflect";
  if (text.includes(DREAM_DELEGATE_MARKER)) return "dream";
  return void 0;
}
function delegateSessionIdOf(node) {
  const sid = node.data?.source?.memory?.sessionId;
  return typeof sid === "string" && sid !== "" ? sid : void 0;
}
function delegateTimeOf(node) {
  const time = node.data?.time;
  return typeof time === "number" ? time : void 0;
}
var dreamStateBySession = /* @__PURE__ */ new Map();
var lastApplied = [];
function delegateNoticeLabelFor(variant, running, interrupted = false) {
  if (variant === "dream") {
    if (interrupted) return "\u25B8 \u68A6\u5883\u8BB0\u5FC6\u6574\u7406\u5DF2\u4E2D\u65AD\uFF0C\u7A0D\u540E\u81EA\u52A8\u91CD\u8BD5\u3002";
    return running ? "\u25B8 \u68A6\u5883\u8BB0\u5FC6\u6574\u7406\u4EFB\u52A1\u8FDB\u884C\u4E2D\u2026\u2026" : "\u25B8 \u68A6\u5883\u8BB0\u5FC6\u6574\u7406\u4EFB\u52A1\u5DF2\u5B8C\u6210\u3002";
  }
  if (variant === "reflect-done") return "\u25B8 \u8BB0\u5FC6\u53CD\u601D\u4EFB\u52A1\u5DF2\u5B8C\u6210\u3002";
  return running ? "\u25B8 \u8BB0\u5FC6\u53CD\u601D\u4EFB\u52A1\u8FDB\u884C\u4E2D\u2026\u2026" : "\u25B8 \u8BB0\u5FC6\u53CD\u601D\u4EFB\u52A1\u5DF2\u5B8C\u6210\u3002";
}
function delegateNoticeLabel(notice) {
  return delegateNoticeLabelFor(notice.variant, notice.running, notice.interrupted === true);
}
function computeDelegateNotices(snapshot, now = Date.now()) {
  if (snapshot?.chat === void 0) return [];
  const found = [];
  for (const key of snapshot.chat.order) {
    const node = snapshot.chat.nodes.get(key);
    if (node === void 0) continue;
    const variant = delegateVariantOf(node);
    if (variant === void 0) continue;
    found.push({ key, variant, sessionId: delegateSessionIdOf(node), time: delegateTimeOf(node) });
  }
  let lastReflectIdx = -1;
  for (let i = 0; i < found.length; i++) {
    if (found[i].variant === "reflect" || found[i].variant === "reflect-done") lastReflectIdx = i;
  }
  const out = found.map((f, i) => {
    if (f.variant !== "dream") {
      return {
        id: f.key,
        variant: f.variant,
        sessionId: f.sessionId,
        running: f.variant === "reflect" && i === lastReflectIdx && f.time !== void 0 && now - f.time < REFLECT_FRESH_MS
      };
    }
    const state = f.sessionId !== void 0 ? dreamStateBySession.get(f.sessionId) : void 0;
    if (state === "dreaming") return { id: f.key, variant: f.variant, sessionId: f.sessionId, running: true };
    if (state === "dreamed") return { id: f.key, variant: f.variant, sessionId: f.sessionId, running: false };
    const stale = f.time !== void 0 && now - f.time >= DREAM_RUNNING_STALE_MS;
    return { id: f.key, variant: f.variant, sessionId: f.sessionId, running: !stale, interrupted: stale };
  });
  if (out.length > 0) console.debug("[meow-dg] notices:", out.map((n) => `${n.id.slice(0, 8)}:${n.variant}:${n.running ? "run" : "done"}`).join(", "));
  return out;
}
function ensureCss() {
  if (typeof document === "undefined") return;
  for (const stale of Array.from(document.querySelectorAll(`style[data-plugin-css="${CSS_ID}"]`))) {
    stale.remove();
  }
  const tag = document.createElement("style");
  tag.dataset.plugin = "meow-memory-delegate-notice";
  tag.dataset.pluginCss = CSS_ID;
  tag.textContent = NOTICE_CSS;
  document.head.appendChild(tag);
}
function applyDelegateNotices(groups) {
  if (typeof document === "undefined") return;
  lastApplied = groups;
  ensureCss();
  const containers = Array.from(document.querySelectorAll("[data-chat-flow]"));
  if (containers.length === 0) return;
  if (groups.length > 0) console.debug("[meow-dg] apply:", groups.length, "group(s),", containers.length, "container(s)");
  const liveIds = new Set(groups.map((group) => group.id));
  for (const container of containers) {
    for (const stale of Array.from(container.querySelectorAll(`[${ANCHOR_ATTR}]`))) {
      if (!liveIds.has(stale.getAttribute(ANCHOR_ATTR) ?? "")) stale.remove();
    }
    for (const row of Array.from(container.querySelectorAll(`[${HIDDEN_ATTR}]`))) {
      if (!liveIds.has(row.getAttribute("data-chat-flow-key") ?? "")) row.removeAttribute(HIDDEN_ATTR);
    }
    for (const group of groups) {
      const row = container.querySelector(`[data-chat-flow-key="${CSS.escape(group.id)}"]`);
      if (row === null || row.parentElement === null) continue;
      row.setAttribute(HIDDEN_ATTR, "true");
      if (group.variant === "reflect-done") continue;
      let anchor = container.querySelector(`[${ANCHOR_ATTR}="${CSS.escape(group.id)}"]`);
      if (anchor === null) {
        anchor = document.createElement("div");
        anchor.setAttribute(ANCHOR_ATTR, group.id);
        row.parentElement.insertBefore(anchor, row);
      }
      let bubble = anchor.querySelector(":scope > [data-iwiw-delegate-bubble]");
      if (bubble === null) {
        bubble = document.createElement("div");
        bubble.setAttribute("data-iwiw-delegate-bubble", "true");
        bubble.style.cssText = [
          "display:block;margin:4px 0;padding:5px 12px;",
          "font-size:12px;line-height:1.6;text-align:left;",
          "color:var(--dsw-text-secondary, rgba(127,127,127,.9));",
          "background:rgba(127,127,127,.07);border:1px solid rgba(127,127,127,.14);",
          "border-radius:999px;"
        ].join("");
        anchor.appendChild(bubble);
      }
      const label = delegateNoticeLabel(group);
      if (bubble.textContent !== label) bubble.textContent = label;
    }
  }
}
function startDelegateStateSync() {
  if (typeof window === "undefined") return () => {
  };
  const notify = () => {
    if (lastApplied.length > 0) applyDelegateNotices(lastApplied);
  };
  const refresh = async () => {
    try {
      const response = await fetch("/meow-memory/dreamed-sessions", { cache: "no-store" });
      if (!response.ok) {
        console.debug("[meow-dg] dreamed-sessions HTTP", response.status);
        return;
      }
      const data = await response.json();
      dreamStateBySession.clear();
      if (Array.isArray(data.sessionIds)) {
        for (const id of data.sessionIds) if (typeof id === "string") dreamStateBySession.set(id, "dreamed");
      }
      if (Array.isArray(data.dreamingIds)) {
        for (const id of data.dreamingIds) if (typeof id === "string") dreamStateBySession.set(id, "dreaming");
      }
      console.debug("[meow-dg] state sync:", dreamStateBySession.size, "session(s)");
      notify();
    } catch {
    }
  };
  const unsubscribeDreamEvents = subscribeDreamEvents((event) => {
    const { sessionId, state } = event;
    if (state === "dreaming" || state === "dreamed") dreamStateBySession.set(sessionId, state);
    else dreamStateBySession.delete(sessionId);
    notify();
  });
  void refresh();
  return () => {
    unsubscribeDreamEvents();
    dreamStateBySession.clear();
    lastApplied = [];
  };
}

// src/client-dream-icon.ts
var DREAM_ICON_ATTR = "data-iwiw-dreamed";
var DREAMING_ATTR = "data-iwiw-dreaming";
var SKIPPED_ATTR = "data-iwiw-skip-dream";
var MOON_SVG = '<svg width="10" height="10" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M12 3a6 6 0 0 0 9 9 9 9 0 1 1-9-9Z"/></svg>';
var SKIP_MOON_PATH = "M12 3a6 6 0 0 0 9 9 9 9 0 1 1-9-9Z";
var SKIP_SLASH_PATH = "M2.5 2.5l19 19";
function makeSkipMoonSvg() {
  const id = `meow-skip-${Math.random().toString(36).slice(2, 10)}`;
  return `<svg width="10" height="10" viewBox="0 0 24 24" aria-hidden="true"><defs><mask id="${id}"><rect width="24" height="24" fill="#fff"/><path d="${SKIP_SLASH_PATH}" fill="none" stroke="#000" stroke-width="4.4" stroke-linecap="round"/></mask></defs><g mask="url(#${id})"><path fill="currentColor" d="${SKIP_MOON_PATH}"/></g><path d="${SKIP_SLASH_PATH}" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"/></svg>`;
}
var ICON_CSS = `[${DREAM_ICON_ATTR}],
[${DREAMING_ATTR}],
[${SKIPPED_ATTR}] {
  display: inline-flex;
  flex: none;
  align-items: center;
  justify-content: center;
  width: 10px;
  height: 10px;
  margin-right: 4px; /* \u69FD\u4F4D\u5185\u5C45\u72B6\u6001\u70B9\u5DE6\u4FA7 / flat \u884C\u9996\u5185\u8054\uFF1A\u4E0E\u53F3\u4FA7\u5185\u5BB9\u4FDD\u6301\u95F4\u8DDD */
}
[${DREAM_ICON_ATTR}] { color: #e9c46a; opacity: 0.9; } /* \u6DE1\u9EC4\u505C\u9A7B */
[${DREAMING_ATTR}] {
  color: #f2c14e;
  animation: meow-dream-breathe 2.4s ease-in-out infinite;
}
@keyframes meow-dream-breathe {
  0%, 100% { color: #fff8e6; opacity: 0.55; }
  50% { color: #f2c14e; opacity: 1; }
}
[${SKIPPED_ATTR}] { color: #94a3b8; opacity: 0.85; } /* \u9759\u97F3\u7070\uFF1A\u8FD9\u6247\u7A97\u4E0D\u505A\u68A6 */
`;
var FIBER_KEY_RE = /^__reactFiber\$/;
function readSessionId(row) {
  let fiber = null;
  for (const key of Object.keys(row)) {
    if (FIBER_KEY_RE.test(key)) {
      fiber = row[key];
      break;
    }
  }
  let cur = fiber;
  for (let depth = 0; depth < 8 && cur !== null && cur !== void 0; depth++) {
    const f = cur;
    if (typeof f.key === "string" && f.key.length > 0) return f.key;
    cur = f.return;
  }
  return null;
}
function makeIcon(state) {
  const icon = document.createElement("span");
  icon.setAttribute(attrForState(state) ?? DREAM_ICON_ATTR, "true");
  icon.setAttribute("aria-hidden", "true");
  icon.innerHTML = state === "skipped" ? makeSkipMoonSvg() : MOON_SVG;
  return icon;
}
function attrForState(state) {
  if (state === "dreaming") return DREAMING_ATTR;
  if (state === "dreamed") return DREAM_ICON_ATTR;
  if (state === "skipped") return SKIPPED_ATTR;
  return null;
}
var ANY_ICON_SEL = `[${DREAM_ICON_ATTR}], [${DREAMING_ATTR}], [${SKIPPED_ATTR}]`;
var SESSION_ROWS_SEL = 'div[role="treeitem"][class*="_sessionRow"]';
function mergeIconStates(dreamStates, skippedIds) {
  const merged = /* @__PURE__ */ new Map();
  for (const [id, state] of dreamStates) merged.set(id, state);
  for (const id of skippedIds) {
    if (merged.get(id) !== "dreaming") merged.set(id, "skipped");
  }
  return merged;
}
function applyDreamIcons(states, rows) {
  const all = rows ?? document.querySelectorAll(SESSION_ROWS_SEL);
  for (const row of all) {
    const id = readSessionId(row);
    const state = id !== null ? states.get(id) : void 0;
    const wantAttr = attrForState(state);
    const slot = row.querySelector('[class$="_slot"]');
    if (slot !== null) {
      const cur = slot.querySelector(ANY_ICON_SEL);
      const consistent = cur !== null && wantAttr !== null && cur.getAttribute(wantAttr) === "true";
      if (state !== void 0 && !consistent) {
        cur?.remove();
        slot.insertBefore(makeIcon(state), slot.firstChild);
      } else if (state === void 0 && cur !== null) {
        cur.remove();
      }
    } else if (state !== void 0) {
      const cur = row.querySelector(ANY_ICON_SEL);
      const consistent = cur !== null && cur.getAttribute(wantAttr ?? "") === "true";
      if (!consistent) {
        cur?.remove();
        const icon = makeIcon(state);
        icon.setAttribute("data-iwiw-inline-icon", "true");
        row.insertBefore(icon, row.firstChild);
      }
    } else {
      row.querySelector(ANY_ICON_SEL)?.remove();
    }
  }
}
function startDreamIconManager() {
  const dreamStates = /* @__PURE__ */ new Map();
  const skippedIds = /* @__PURE__ */ new Set();
  let timer = 0;
  const replay = () => applyDreamIcons(mergeIconStates(dreamStates, skippedIds));
  const refreshSkips = async () => {
    try {
      const response = await fetch("/meow-memory/skip-dreams", { cache: "no-store" });
      if (!response.ok) return;
      const data = await response.json();
      skippedIds.clear();
      if (Array.isArray(data.sessionIds)) {
        for (const id of data.sessionIds) {
          if (typeof id === "string") skippedIds.add(id);
        }
      }
    } catch {
    }
  };
  const refresh = async () => {
    try {
      const response = await fetch("/meow-memory/dreamed-sessions", { cache: "no-store" });
      if (!response.ok) return;
      const data = await response.json();
      dreamStates.clear();
      if (Array.isArray(data.sessionIds)) {
        for (const id of data.sessionIds) {
          if (typeof id === "string") dreamStates.set(id, "dreamed");
        }
      }
      if (Array.isArray(data.dreamingIds)) {
        for (const id of data.dreamingIds) {
          if (typeof id === "string") dreamStates.set(id, "dreaming");
        }
      }
    } catch {
    }
    await refreshSkips();
    replay();
  };
  const unsubscribeDreamEvents = subscribeDreamEvents((event) => {
    const { sessionId, state } = event;
    if (state === "dreamed" || state === "dreaming") dreamStates.set(sessionId, state);
    else if (state === "skip") skippedIds.add(sessionId);
    else if (state === "unskip") skippedIds.delete(sessionId);
    else dreamStates.delete(sessionId);
    replay();
  });
  for (const stale of Array.from(document.querySelectorAll("style[data-iwiw-dream-icon-css]"))) {
    stale.remove();
  }
  const style = document.createElement("style");
  style.dataset.meowDreamIconCss = "true";
  style.textContent = ICON_CSS;
  document.head.appendChild(style);
  const observer = new MutationObserver(() => {
    window.clearTimeout(timer);
    timer = window.setTimeout(replay, 120);
  });
  observer.observe(document.body, { childList: true, subtree: true });
  void refresh();
  return () => {
    observer.disconnect();
    window.clearTimeout(timer);
    unsubscribeDreamEvents();
    style.remove();
    for (const el2 of Array.from(document.querySelectorAll(`[${DREAM_ICON_ATTR}], [${DREAMING_ATTR}], [${SKIPPED_ATTR}]`))) el2.remove();
  };
}

// src/client-dream-skip.ts
var SKIP_ITEM_ATTR = "data-iwiw-skip-item";
var ROW_ACTIONS_SEL = '[class*="_rowActions"]';
var SESSION_ROW_SEL = '[role="treeitem"][class*="_sessionRow"]';
var MENU_OPEN_ROW_SEL = '[role="treeitem"][class*="_sessionRow"][class*="_menuOpen"]';
var MENU_WINDOW_MS = 1500;
function skipLabel(skipped2) {
  return skipped2 ? "\u53D6\u6D88\u8DF3\u8FC7\u68A6\u5883\u6574\u7406\u8BB0\u5FC6" : "\u8DF3\u8FC7\u68A6\u5883\u6574\u7406\u8BB0\u5FC6";
}
function setMenuIcon(item, skipped2) {
  const icon = item.querySelector("svg");
  if (icon !== null) icon.outerHTML = skipped2 ? MOON_SVG : makeSkipMoonSvg();
}
function captureSessionIdFromTarget(target) {
  const el2 = target;
  if (el2 === null || el2 === void 0 || typeof el2.closest !== "function") return null;
  if (el2.closest(ROW_ACTIONS_SEL) === null) return null;
  const row = el2.closest(SESSION_ROW_SEL);
  if (row === null) return null;
  return readSessionId(row);
}
function retitleLeaf(root, text) {
  let leaf = null;
  const walk = (el2) => {
    let hasElementChild = false;
    for (const c of el2.children) {
      hasElementChild = true;
      walk(c);
    }
    if (!hasElementChild && (el2.textContent ?? "").trim().length > 0) leaf = el2;
  };
  walk(root);
  if (leaf === null) return false;
  leaf.textContent = text;
  return true;
}
function resolveMenuSessionId(doc, fallback) {
  const openRow = doc.querySelector(MENU_OPEN_ROW_SEL);
  if (openRow === null) return null;
  const sid = readSessionId(openRow);
  return sid !== null ? sid : fallback;
}
function injectSkipItem(menu, sessionId, host) {
  for (const old of Array.from(menu.querySelectorAll(`[${SKIP_ITEM_ATTR}]`))) {
    if (old.getAttribute("data-iwiw-session-id") === sessionId) return null;
    old.remove();
  }
  const template = menu.querySelector('[role="menuitem"]');
  if (template === null) return null;
  const item = template.cloneNode(true);
  item.removeAttribute("id");
  for (const el2 of Array.from(item.querySelectorAll("[id]"))) el2.removeAttribute("id");
  item.setAttribute("role", "menuitem");
  if (!retitleLeaf(item, skipLabel(readSkipped(sessionId)))) return null;
  item.setAttribute(SKIP_ITEM_ATTR, "true");
  item.setAttribute("data-iwiw-session-id", sessionId);
  setMenuIcon(item, readSkipped(sessionId));
  const onClick = (e) => {
    e.stopPropagation();
    e.preventDefault();
    const next = !readSkipped(sessionId);
    writeSkipped(sessionId, next);
    retitleLeaf(item, skipLabel(next));
    setMenuIcon(item, next);
    host.onToggle(sessionId, next, () => {
      writeSkipped(sessionId, !next);
      retitleLeaf(item, skipLabel(!next));
      setMenuIcon(item, !next);
    });
  };
  item.addEventListener("click", onClick, true);
  item.addEventListener("pointerdown", (e) => e.stopPropagation());
  menu.appendChild(item);
  return item;
}
var skipped = /* @__PURE__ */ new Set();
function readSkipped(sid) {
  return skipped.has(sid);
}
function writeSkipped(sid, val) {
  if (val) skipped.add(sid);
  else skipped.delete(sid);
}
function startDreamSkipManager() {
  let pendingSid = null;
  let pendingAt = 0;
  let observerTimer = 0;
  const syncOpenMenus = () => {
    const withinWindow = pendingSid !== null && Date.now() - pendingAt <= MENU_WINDOW_MS;
    const sid = resolveMenuSessionId(document, withinWindow ? pendingSid : null);
    if (sid === null) return;
    for (const menu of Array.from(document.querySelectorAll('[role="menu"]'))) {
      injectSkipItem(menu, sid, { onToggle: handleToggle });
    }
  };
  const observer = new MutationObserver((muts) => {
    if (pendingSid !== null && Date.now() - pendingAt <= MENU_WINDOW_MS) {
      const sid = resolveMenuSessionId(document, pendingSid);
      if (sid !== null) {
        for (const m of muts) {
          for (const node of Array.from(m.addedNodes)) {
            if (!(node instanceof HTMLElement)) continue;
            const menus = node.matches('[role="menu"]') ? [node] : Array.from(node.querySelectorAll('[role="menu"]'));
            for (const menu of menus) injectSkipItem(menu, sid, { onToggle: handleToggle });
          }
        }
        for (const menu of Array.from(document.querySelectorAll('[role="menu"]'))) {
          injectSkipItem(menu, sid, { onToggle: handleToggle });
        }
      }
    }
    window.clearTimeout(observerTimer);
    observerTimer = window.setTimeout(syncOpenMenus, 120);
  });
  const handleToggle = (sessionId, skip, rollback) => {
    void (async () => {
      try {
        const resp = await fetch("/meow-memory/skip-dreams", {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ sessionId, skip })
        });
        if (!resp.ok) throw new Error(String(resp.status));
      } catch {
        rollback();
      }
    })();
  };
  const onPointerDown = (e) => {
    const sid = captureSessionIdFromTarget(e.target);
    if (sid === null) return;
    pendingSid = sid;
    pendingAt = Date.now();
  };
  const refresh = async () => {
    try {
      const response = await fetch("/meow-memory/skip-dreams", { cache: "no-store" });
      if (!response.ok) return;
      const data = await response.json();
      skipped.clear();
      if (Array.isArray(data.sessionIds)) {
        for (const id of data.sessionIds) {
          if (typeof id === "string") skipped.add(id);
        }
      }
    } catch {
    }
  };
  const unsubscribeDreamEvents = subscribeDreamEvents((event) => {
    const { sessionId, state } = event;
    if (state === "skip") {
      skipped.add(sessionId);
    } else if (state === "unskip") {
      skipped.delete(sessionId);
    } else {
      return;
    }
    void syncOpenMenus();
  });
  document.addEventListener("pointerdown", onPointerDown, true);
  observer.observe(document.body, { childList: true, subtree: true });
  void refresh();
  return () => {
    document.removeEventListener("pointerdown", onPointerDown, true);
    observer.disconnect();
    window.clearTimeout(observerTimer);
    unsubscribeDreamEvents();
    for (const item of Array.from(document.querySelectorAll(`[${SKIP_ITEM_ATTR}]`))) item.remove();
  };
}

// src/settings-page.ts
var React = __toESM(require("react"), 1);
var SETTINGS_NS = "dsh-iwiw-memory";
var CSS_ID2 = "iwiw-memory-settings-css";
var CSS2 = `
.iwiw_set_page{color:var(--dsw-alias-label-primary);display:flex;flex-direction:column;gap:10px;max-width:760px;padding:4px 0}
.iwiw_set_title{font-size:16px;font-weight:600;margin:0}
.iwiw_set_subtitle{color:var(--dsw-alias-label-caption);font-size:12px;line-height:1.6;margin:0}
.iwiw_set_card{background:color-mix(in srgb,currentColor 3%,transparent);border:1px solid var(--dsw-alias-border-l3);border-radius:10px;display:flex;flex-direction:column;gap:8px;padding:12px}
.iwiw_set_group{color:var(--dsw-alias-label-secondary);font-size:12px;font-weight:600;margin-top:2px}
.iwiw_set_row{align-items:flex-start;display:flex;gap:10px;justify-content:space-between}
.iwiw_set_rowtext{display:flex;flex-direction:column;gap:2px;min-width:0}
.iwiw_set_label{font-size:13px;font-weight:500}
.iwiw_set_hint{color:var(--dsw-alias-label-caption);font-size:12px;line-height:1.5}
.iwiw_set_ctrl{flex:none;padding-top:2px}
.iwiw_set_input{background:transparent;border:1px solid var(--dsw-alias-border-l3);border-radius:6px;color:inherit;font-size:13px;padding:4px 8px;width:190px}
.iwiw_set_check{cursor:pointer}
.iwiw_set_badge{border-radius:999px;font-size:11px;line-height:16px;padding:0 8px;flex:none}
.iwiw_set_badge_override{background:color-mix(in srgb,#f59e0b 18%,transparent);color:#f59e0b}
.iwiw_set_badge_prefill{background:color-mix(in srgb,#60a5fa 18%,transparent);color:#60a5fa}
.iwiw_set_reset{background:transparent;border:1px solid var(--dsw-alias-border-l3);border-radius:6px;color:var(--dsw-alias-label-secondary);cursor:pointer;font-size:12px;padding:2px 8px}
.iwiw_set_reset:hover{border-color:var(--dsw-alias-border-l2);color:inherit}
.iwiw_set_err{color:#f43f5e;font-size:12px;line-height:1.5;margin:0}
.iwiw_set_saved{color:#34d399;font-size:12px}
.iwiw_set_muted{color:var(--dsw-alias-label-caption);font-size:12px}
`;
var el = React.createElement;
var FIELDS = [
  {
    title: "\u6CE8\u5165\u4E0E\u547D\u4E2D",
    fields: [
      { key: "hitTopK", label: "\u6BCF\u6761\u6D88\u606F\u547D\u4E2D\u6CE8\u5165\u6761\u6570\u4E0A\u9650", type: "num", hint: "\u6BCF\u6761\u7528\u6237\u6D88\u606F\u6700\u591A\u8054\u60F3\u6CE8\u5165\u7684\u8BB0\u5FC6\u6761\u6570\uFF0C\u6539\u5B8C\u4E0B\u4E00\u6761\u6D88\u606F\u751F\u6548" },
      { key: "coreMaxChars", label: "\u5E38\u9A7B\u8BB0\u5FC6\u6BB5\u5B57\u7B26\u9884\u7B97", type: "num", hint: "\u957F\u671F\u8BB0\u5FC6\uFF08\u5E38\u9A7B\u5C42\uFF09\u6CE8\u5165\u7684\u603B\u5B57\u7B26\u4E0A\u9650\uFF0C\u8D85\u51FA\u6309\u6761\u622A\u65AD" }
    ]
  },
  {
    title: "\u56DE\u987E\u4E0E\u6574\u7406",
    fields: [
      { key: "reflectTurns", label: "\u56DE\u987E\u63D0\u793A\u89E6\u53D1\u6B65\u6570", type: "num", hint: "\u8FDE\u7EED N \u6B65\u672A\u5199\u5165\u8BB0\u5FC6\u65F6\u6CE8\u5165\u4E00\u6B21\u6027\u56DE\u987E\u63D0\u793A\uFF1B0=\u5173\u95ED" },
      { key: "dreamIdleMinutes", label: "\u7A7A\u95F2\u6574\u7406\u9608\u503C\uFF08\u5206\u949F\uFF09", type: "num", hint: "\u7A7A\u95F2\u6EE1\u8BE5\u5206\u949F\u6570\u4E14\u975E\u5CF0\u65F6\uFF089-12/14-18\uFF09\u89E6\u53D1 dream \u6574\u7406\uFF1B0=\u5173\u95ED" }
    ]
  },
  {
    title: "\u5E38\u9A7B\u5C42\uFF08\u6A21\u5F0F\u914D\u7F6E\uFF09",
    fields: [
      { key: "standingLayers", label: "\u5E38\u9A7B\u8BB0\u5FC6\u7C7B\u578B", type: "str", hint: "\u9017\u53F7\u5206\u9694\uFF1Aprofile/fact/lesson/rules/project\u3002chat \u6A21\u5F0F=profile,rules\uFF1Bdev \u6A21\u5F0F\u5EFA\u8BAE rules\u3002\u8FD9\u4E9B\u7C7B\u578B\u5168\u91CF\u6CE8\u5165\u6BCF\u8F6E\uFF0C\u5176\u4F59\u6309\u8BDD\u9898\u68C0\u7D22\u53EC\u56DE", placeholder: "profile,rules" }
    ]
  }
];
function fieldValue(value, spec) {
  return value?.[spec.key];
}
function inUserLayer(user, spec) {
  return user !== void 0 && spec.key in user;
}
function jsonEqual(a, b) {
  if (a === b) return true;
  if (typeof a !== "object" || typeof b !== "object" || a === null || b === null) return false;
  if (Array.isArray(a) !== Array.isArray(b)) return false;
  if (Array.isArray(a) && Array.isArray(b)) {
    return a.length === b.length && a.every((entry, i) => jsonEqual(entry, b[i]));
  }
  const ka = Object.keys(a);
  const kb = Object.keys(b);
  return ka.length === kb.length && ka.every((k) => k in b && jsonEqual(a[k], b[k]));
}
function MemorySettingsSection(props) {
  const scope = props.scope;
  const subscribe = React.useCallback((cb) => scope.subscribe(cb), [scope]);
  const getSnapshot = React.useCallback(() => scope.getSnapshot(), [scope]);
  const snap = React.useSyncExternalStore(subscribe, getSnapshot, getSnapshot);
  const [savedAt, setSavedAt] = React.useState(0);
  const [error, setError] = React.useState(null);
  const [drafts, setDrafts] = React.useState({});
  const flashSaved = () => {
    setSavedAt(Date.now());
    window.setTimeout(() => setSavedAt((t) => t === 0 ? 0 : t), 4e3);
  };
  const clearDraft = (spec) => {
    const key = spec.key;
    setDrafts((prev) => {
      if (!(key in prev)) return prev;
      const next = { ...prev };
      delete next[key];
      return next;
    });
  };
  const apply2 = async (spec, newValue) => {
    setError(null);
    try {
      await scope.set(spec.key, newValue);
    } catch (e) {
      setError(`\u4FDD\u5B58\u5931\u8D25\uFF1A${e instanceof Error ? e.message : String(e)}`);
      return false;
    }
    const landed = () => {
      const v = scope.getSnapshot().user;
      return jsonEqual(v?.[spec.key], newValue);
    };
    if (landed()) {
      flashSaved();
      return true;
    }
    await new Promise((resolve) => window.setTimeout(resolve, 300));
    if (landed()) {
      flashSaved();
      return true;
    }
    clearDraft(spec);
    setError("\u4FDD\u5B58\u672A\u751F\u6548\uFF1A\u5199\u5165\u88AB\u670D\u52A1\u5668\u62D2\u7EDD\uFF08\u53EF\u80FD\u672A\u901A\u8FC7\u6821\u9A8C\uFF09\uFF0C\u5DF2\u6062\u590D\u663E\u793A\u670D\u52A1\u5668\u5F53\u524D\u503C\u3002");
    return false;
  };
  const reset = async (spec) => {
    setError(null);
    try {
      await scope.unset(spec.key);
      const gone = () => {
        const v = scope.getSnapshot().user;
        return v?.[spec.key] === void 0;
      };
      if (!gone()) await new Promise((resolve) => window.setTimeout(resolve, 300));
      if (gone()) {
        flashSaved();
      } else {
        setError("\u6062\u590D\u9ED8\u8BA4\u672A\u751F\u6548\uFF0C\u8BF7\u91CD\u8BD5\u3002");
      }
    } catch (e) {
      setError(`\u6062\u590D\u9ED8\u8BA4\u5931\u8D25\uFF1A${e instanceof Error ? e.message : String(e)}`);
    }
  };
  if (snap.status === "loading") {
    return el("div", { className: "iwiw_set_page" }, el("span", { className: "iwiw_set_muted" }, "iwiw \u8BB0\u5FC6\u914D\u7F6E\u52A0\u8F7D\u4E2D\u2026"));
  }
  if (snap.status === "unavailable") {
    return el("div", { className: "iwiw_set_page" }, el("span", { className: "iwiw_set_muted" }, "\u5F53\u524D\u8FDE\u63A5\u4E0D\u652F\u6301\u8BBE\u7F6E\u5199\u5165\uFF08\u4EC5\u672C\u673A\u56DE\u73AF\u8FDE\u63A5\u53EF\u7F16\u8F91\uFF09\u3002"));
  }
  const renderField = (spec) => {
    const raw = fieldValue(snap.value, spec);
    const overridden = inUserLayer(snap.user, spec);
    const mirrorText = typeof raw === "string" ? raw : spec.type === "num" && typeof raw === "number" ? String(raw) : "";
    const draft = drafts[spec.key];
    let control = null;
    if (spec.type === "bool") {
      const checked = typeof draft === "boolean" ? draft : raw === true;
      control = el("input", {
        className: "iwiw_set_check",
        type: "checkbox",
        checked,
        disabled: !snap.writable,
        onChange: (e) => {
          const next = e.target.checked;
          setDrafts((prev) => ({ ...prev, [spec.key]: next }));
          void apply2(spec, next).then((ok) => {
            if (ok) clearDraft(spec);
          });
        }
      });
    } else if (spec.type === "num") {
      const text = typeof draft === "string" ? draft : mirrorText;
      control = el("input", {
        className: "iwiw_set_input",
        type: "number",
        value: text,
        disabled: !snap.writable,
        onChange: (e) => setDrafts((prev) => ({ ...prev, [spec.key]: e.target.value })),
        onBlur: (e) => {
          const v = e.target.value;
          const num = Number(v);
          if (v.trim() === "" || !Number.isFinite(num) || num === raw) {
            clearDraft(spec);
            return;
          }
          void apply2(spec, num).then((ok) => {
            if (ok) clearDraft(spec);
          });
        }
      });
    } else {
      const text = typeof draft === "string" ? draft : mirrorText;
      control = el("input", {
        className: "iwiw_set_input",
        type: "text",
        value: text,
        placeholder: spec.placeholder,
        disabled: !snap.writable,
        onChange: (e) => setDrafts((prev) => ({ ...prev, [spec.key]: e.target.value })),
        onBlur: (e) => {
          const next = e.target.value;
          if (next === mirrorText) {
            clearDraft(spec);
            return;
          }
          void apply2(spec, next).then((ok) => {
            if (ok) clearDraft(spec);
          });
        }
      });
    }
    return el(
      "div",
      { key: spec.key, className: "iwiw_set_row" },
      el(
        "div",
        { className: "iwiw_set_rowtext" },
        el("span", { className: "iwiw_set_label" }, spec.label),
        spec.hint !== void 0 ? el("span", { className: "iwiw_set_hint" }, spec.hint) : null
      ),
      el(
        "div",
        { className: "iwiw_set_ctrl", style: { display: "flex", gap: "8px", alignItems: "center" } },
        control,
        el("span", { className: `iwiw_set_badge ${overridden ? "iwiw_set_badge_override" : "iwiw_set_badge_prefill"}` }, overridden ? "\u5DF2\u8986\u76D6" : "\u9ED8\u8BA4"),
        overridden && snap.writable ? el("button", { className: "iwiw_set_reset", onClick: () => {
          clearDraft(spec);
          void reset(spec);
        } }, "\u6062\u590D\u9ED8\u8BA4") : null
      )
    );
  };
  return el(
    "div",
    { className: "iwiw_set_page" },
    el("h2", { className: "iwiw_set_title" }, "iwiw \u8BB0\u5FC6"),
    el(
      "p",
      { className: "iwiw_set_subtitle" },
      "\u672C\u5730\u957F\u671F\u8BB0\u5FC6\u63D2\u4EF6\u7684\u8FD0\u884C\u53C2\u6570\u3002\u6539\u52A8\u4FDD\u5B58\u5728 DSH \u8BBE\u7F6E\u91CC\uFF08\u5B57\u6BB5\u7EA7\uFF0C\u53EF\u5355\u9879\u6062\u590D\u9ED8\u8BA4\uFF09\uFF1BhitTopK / reflectTurns / dreamIdleMinutes / standingLayers \u70ED\u751F\u6548\uFF0CcoreMaxChars \u5728\u4E0B\u6B21 core \u6BB5\u5237\u65B0\u65F6\u751F\u6548\u3002"
    ),
    !snap.writable ? el("span", { className: "iwiw_set_muted" }, "\u5F53\u524D\u8FDE\u63A5\u4E3A\u53EA\u8BFB\uFF08\u8BBE\u7F6E\u5199\u5165\u4EC5\u9650\u672C\u673A\u56DE\u73AF\u8FDE\u63A5\uFF09\u3002") : null,
    savedAt > 0 ? el("span", { className: "iwiw_set_saved" }, "\u5DF2\u4FDD\u5B58 \u2713") : null,
    error !== null ? el("div", { className: "iwiw_set_err" }, error) : null,
    ...FIELDS.map(
      (group) => el(
        "div",
        { key: group.title, className: "iwiw_set_card" },
        el("div", { className: "iwiw_set_group" }, group.title),
        ...group.fields.map(renderField)
      )
    )
  );
}
function applySettingsPage(ctx) {
  if (typeof document !== "undefined" && document.querySelector(`style[data-plugin-css="${CSS_ID2}"]`) === null) {
    const tag = document.createElement("style");
    tag.dataset.plugin = "iwiw-memory-settings";
    tag.dataset.pluginCss = CSS_ID2;
    tag.textContent = CSS2;
    document.head.appendChild(tag);
  }
  const scope = ctx.settingsScope.bind({ namespace: SETTINGS_NS });
  ctx.slots.inject(
    "settings.section",
    () => ctx.slots.register(
      {
        name: "settings.section",
        id: SETTINGS_NS,
        order: 35,
        label: () => "iwiw \u8BB0\u5FC6",
        inject: () => ({ scope })
      },
      MemorySettingsSection
    )
  );
}

// src/client.ts
var FOLDED_ATTR = "data-iwiw-memory-folded";
var ANCHOR_ATTR2 = "data-iwiw-memory-anchor";
var BODY_ATTR = "data-iwiw-memory-body";
var INJ_ANCHOR_ATTR = "data-iwiw-injection-anchor";
var INJ_BODY_ATTR = "data-iwiw-injection-body";
var INJ_PROMPT_ATTR = "data-iwiw-injection-prompt";
var FOLD_CSS = `[${FOLDED_ATTR}="true"] { display: none !important; }

[data-iwiw-detail-body] {

  display: none;

  margin: 2px 0 8px 22px;

  padding: 8px 10px;

  font-size: 13px;

  line-height: 1.7;

  white-space: pre-wrap;

  word-break: break-word;

  color: var(--dsw-alias-label-secondary, rgba(190,190,190,.9));

  background: rgba(127,127,127,.06);

  border: 1px solid rgba(127,127,127,.12);

  border-radius: 8px;

  font-family: ui-monospace, 'Cascadia Code', Consolas, 'Courier New', monospace;

}

[data-iwiw-detail-body="think"] {

  font-family: inherit;

  font-style: italic;

  opacity: .9;

}

[${INJ_BODY_ATTR}] {

  display: none;

  margin: 2px 0 8px;

  padding: 8px 10px;

  font-size: 12px;

  line-height: 1.7;

  white-space: pre-wrap;

  word-break: break-word;

  color: var(--dsw-alias-label-secondary, rgba(190,190,190,.9));

  background: rgba(127,127,127,.05);

  border: 1px solid rgba(127,127,127,.12);

  border-radius: 8px;

  font-family: ui-monospace, 'Cascadia Code', Consolas, 'Courier New', monospace;

}

[${INJ_PROMPT_ATTR}] {

  display: flex;

  flex-direction: column;

  align-items: flex-end;

  gap: 6px;

}

/* \u7528\u6237 prompt \u6C14\u6CE1\uFF1A\u6837\u5F0F\u5BF9\u9F50 dsh \u672C\u4F53 UserStyleBubble\uFF08MessageItem.module.css

 * .userStack/.bubble\uFF09\u2014\u2014\u540C token \u540C\u5C3A\u5BF8\uFF0C\u4E3B\u9898\u5207\u6362\u81EA\u52A8\u8DDF\u968F\u3002 */

[${INJ_PROMPT_ATTR}] > [data-iwiw-inj-bubble] {

  max-width: min(525px, 82%);

  padding: 10px 16px;

  border-radius: 22px;

  background: var(--dsw-specific-bubble);

  color: var(--dsw-alias-label-primary);

  font-size: 16px;

  line-height: 24px;

  white-space: pre-wrap;

  word-break: break-word;

}

[data-iwiw-inj-actions] {

  display: flex;

  align-items: center;

  gap: 10px;

  height: 28px;

  background: transparent;

}

[data-iwiw-inj-time] {

  padding-right: 12px;

  font-size: 14px;

  line-height: 24px;

  color: var(--dsw-alias-label-tertiary);

  white-space: nowrap;

  background: transparent;

}

@media (hover: hover) {

  [data-iwiw-inj-time] {

    opacity: 0;

    transition: opacity 80ms ease;

  }

  [${INJ_PROMPT_ATTR}]:hover [data-iwiw-inj-time],

  [${INJ_PROMPT_ATTR}]:focus-within [data-iwiw-inj-time] {

    opacity: 1;

  }

}

[data-iwiw-inj-copy] {

  display: inline-flex;

  align-items: center;

  justify-content: center;

  width: 28px;

  height: 28px;

  padding: 6px;

  border: none;

  border-radius: 28px;

  background: transparent;

  color: var(--dsw-alias-label-tertiary);

  cursor: pointer;

}

[data-iwiw-inj-copy]:hover {

  background: var(--dsw-alias-interactive-bg-hover);

  color: var(--dsw-alias-label-secondary);

}`;
var bodySigs = /* @__PURE__ */ new Map();
function sigOf(container, keys) {
  return keys.map((key) => flowRow(container, key)?.textContent ?? "").join("");
}
function flowRow(container, key) {
  return container.querySelector(`[data-chat-flow-key="${CSS.escape(key)}"]`);
}
function anchorOf(container, id) {
  return container.querySelector(`[${ANCHOR_ATTR2}="${CSS.escape(id)}"]`);
}
function ensureAnchor(container, group, expanded, onToggle) {
  let anchor = anchorOf(container, group.id);
  if (anchor === null) {
    const startRow = flowRow(container, group.id);
    if (startRow === null || startRow.parentElement === null) return;
    anchor = document.createElement("div");
    anchor.setAttribute(ANCHOR_ATTR2, group.id);
    startRow.parentElement.insertBefore(anchor, startRow);
  }
  let bar = anchor.querySelector(":scope > button");
  if (bar === null) {
    bar = document.createElement("button");
    bar.type = "button";
    bar.style.cssText = [
      "display:block;width:100%;margin:4px 0;padding:5px 12px;",
      "font-size:12px;line-height:1.6;text-align:left;cursor:pointer;",
      "color:var(--dsw-text-secondary, rgba(127,127,127,.9));",
      "background:rgba(127,127,127,.07);border:1px solid rgba(127,127,127,.14);",
      "border-radius:999px;"
    ].join("");
    bar.addEventListener("click", () => onToggle(group.id));
    anchor.appendChild(bar);
  }
  const label = foldLabel(group, expanded);
  if (bar.textContent !== label) bar.textContent = label;
  let body = anchor.querySelector(`:scope > [${BODY_ATTR}]`);
  if (body === null) {
    body = document.createElement("div");
    body.setAttribute(BODY_ATTR, "true");
    body.style.display = "none";
    body.style.cssText = [
      "display:none;",
      "background:rgba(127,127,127,.05);",
      "border:1px solid rgba(127,127,127,.12);",
      "border-radius:12px;",
      "margin:2px 0 6px;",
      "padding:6px 10px;",
      "max-height:70vh;",
      "overflow-y:auto;"
    ].join("");
    anchor.appendChild(body);
  }
}
function attachDisclosure(rowEl, label, text) {
  const root = rowEl.parentElement;
  if (root === null) return;
  if (root.querySelector(":scope > [data-iwiw-detail-body]") !== null) return;
  const body = document.createElement("div");
  body.setAttribute("data-iwiw-detail-body", label);
  body.textContent = text;
  body.style.display = "none";
  root.appendChild(body);
  rowEl.addEventListener("click", () => {
    const open = root.getAttribute("data-open") === "true";
    root.setAttribute("data-open", open ? "" : "true");
    body.style.display = open ? "none" : "block";
  });
}
function enhanceClone(clone, node) {
  if (node === void 0) return;
  if (node.kind === "assistant-step") {
    const blocks = node.data.blocks ?? [];
    const reasoning = blocks.filter((b) => b.kind === "reasoning");
    const toolCalls = blocks.filter((b) => b.kind === "tool-call");
    const thinkRows = Array.from(clone.querySelectorAll('[data-variant="think"]'));
    thinkRows.forEach((root, i) => {
      const text = reasoning[i]?.text;
      const rowEl = root.querySelector("[data-disclosure-row]");
      if (text !== void 0 && rowEl !== null) attachDisclosure(rowEl, "think", text);
    });
    const toolRows = Array.from(clone.querySelectorAll("[data-disclosure-row]")).filter((el2) => el2.closest('[data-variant="think"]') === null);
    toolRows.forEach((rowEl, i) => {
      const block = toolCalls[i];
      if (block !== void 0) attachDisclosure(rowEl, "tool", toolCallDetail(block));
    });
  } else if (node.kind === "tool-call") {
    const root = node.data.root;
    if (root === void 0) return;
    const rowEl = clone.querySelector("[data-disclosure-row]");
    if (rowEl === null) return;
    const name = "name" in root ? root.name : root.call?.name ?? root.callId;
    const argsRaw = "name" in root ? root.argsRaw : root.call?.argsRaw ?? "";
    const detail = toolCallDetail({ name, argsRaw });
    const resultText = "content" in root ? blocksToText(root.content) : "";
    attachDisclosure(rowEl, "tool", resultText.length > 0 ? `${detail}

\u3010\u7ED3\u679C\u3011
${resultText}` : detail);
  } else if (node.kind === "context") {
    const content = node.data.content;
    const text = blocksToText(content ?? []);
    const rowEl = clone.querySelector("[data-disclosure-row]");
    if (rowEl !== null && text.length > 0) attachDisclosure(rowEl, "context", text);
  }
}
function fillBody(id, visible, keys, session) {
  for (const container of Array.from(document.querySelectorAll("[data-chat-flow]"))) {
    const anchor = anchorOf(container, id);
    if (anchor === null) continue;
    const body = anchor.querySelector(`:scope > [${BODY_ATTR}]`);
    if (body === null) continue;
    body.replaceChildren();
    if (!visible) {
      body.style.display = "none";
      bodySigs.delete(id);
      return;
    }
    body.style.display = "block";
    for (const key of keys) {
      const row = flowRow(container, key);
      if (row === null) continue;
      const clone = row.cloneNode(true);
      clone.removeAttribute(FOLDED_ATTR);
      clone.removeAttribute("data-chat-flow-key");
      clone.removeAttribute("data-chat-anchor-key");
      clone.removeAttribute("data-chat-flow-kind");
      body.appendChild(clone);
      enhanceClone(clone, session.chat?.nodes.get(key));
    }
    bodySigs.set(id, sigOf(container, keys));
  }
}
var COPY_ICON_SVG = '<svg width="16" height="16" viewBox="0 0 16 16" fill="none" xmlns="http://www.w3.org/2000/svg"><path d="M6.14929 4.02032C7.11197 4.02032 7.87983 4.02016 8.49597 4.07598C9.12128 4.13269 9.65792 4.25188 10.1415 4.53106C10.7202 4.8653 11.2008 5.3459 11.535 5.92462C11.8142 6.40818 11.9334 6.94481 11.9901 7.57012C12.0459 8.18625 12.0458 8.95419 12.0458 9.9168C12.0458 10.8795 12.0459 11.6473 11.9901 12.2635C11.9334 12.8888 11.8142 13.4254 11.535 13.909C11.2008 14.4877 10.7202 14.9683 10.1415 15.3025C9.65792 15.5817 9.12128 15.7009 8.49597 15.7576C7.87984 15.8134 7.11196 15.8133 6.14929 15.8133C5.18667 15.8133 4.41874 15.8134 3.80261 15.7576C3.1773 15.7009 2.64067 15.5817 2.1571 15.3025C1.5784 14.9683 1.09778 14.4877 0.76355 13.909C0.484366 13.4254 0.365184 12.8888 0.308472 12.2635C0.252649 11.6473 0.252808 10.8795 0.252808 9.9168C0.252808 8.95418 0.252664 8.18625 0.308472 7.57012C0.365184 6.94481 0.484366 6.40818 0.76355 5.92462C1.09777 5.34589 1.57839 4.86529 2.1571 4.53106C2.64067 4.25188 3.1773 4.13269 3.80261 4.07598C4.41874 4.02017 5.18666 4.02032 6.14929 4.02032ZM6.14929 5.37774C5.16181 5.37774 4.46634 5.37761 3.92566 5.42657C3.39434 5.47472 3.07859 5.56574 2.83582 5.70587C2.4632 5.92106 2.15354 6.2307 1.93835 6.60333C1.79823 6.8461 1.70721 7.16185 1.65906 7.69317C1.6101 8.23385 1.61023 8.92933 1.61023 9.9168C1.61023 10.9043 1.61009 11.5998 1.65906 12.1404C1.70721 12.6717 1.79823 12.9875 1.93835 13.2303C2.15356 13.6029 2.46321 13.9126 2.83582 14.1277C3.07859 14.2679 3.39434 14.3589 3.92566 14.407C4.46634 14.456 5.16182 14.4559 6.14929 14.4559C7.13682 14.4559 7.83224 14.456 8.37292 14.407C8.90425 14.3589 9.21999 14.2679 9.46277 14.1277C9.83535 13.9126 10.145 13.6029 10.3602 13.2303C10.5004 12.9875 10.5914 12.6717 10.6395 12.1404C10.6885 11.5998 10.6884 10.9043 10.6884 9.9168C10.6884 8.92934 10.6885 8.23384 10.6395 7.69317C10.5914 7.16185 10.5004 6.8461 10.3602 6.60333C10.1451 6.23071 9.83536 5.92107 9.46277 5.70587C9.21999 5.56574 8.90424 5.47472 8.37292 5.42657C7.83224 5.3776 7.13682 5.37774 6.14929 5.37774ZM9.80164 0.367975C10.7638 0.367975 11.5314 0.36788 12.1473 0.423639C12.7726 0.480307 13.3093 0.598759 13.7928 0.877741C14.3717 1.21192 14.8521 1.69355 15.1864 2.27227C15.4655 2.75574 15.5857 3.29164 15.6425 3.9168C15.6983 4.53301 15.6971 5.3016 15.6971 6.26446V7.82989C15.6971 8.29264 15.6989 8.58993 15.6649 8.84844C15.4668 10.3525 14.401 11.5738 12.9833 11.9988V10.5467C13.6973 10.1903 14.2105 9.49662 14.3192 8.67169C14.3387 8.52347 14.3407 8.3358 14.3407 7.82989V6.26446C14.3407 5.27706 14.3398 4.58149 14.2909 4.04083C14.2428 3.50968 14.1526 3.19372 14.0126 2.95098C13.7974 2.57849 13.4876 2.26869 13.1151 2.05352C12.8724 1.91347 12.5564 1.82237 12.0253 1.77423C11.4847 1.72528 10.7888 1.7254 9.80164 1.7254H7.71472C6.7562 1.72558 5.92665 2.27697 5.52332 3.07891H4.07019C4.54221 1.51132 5.9932 0.368186 7.71472 0.367975H9.80164Z" fill="currentColor"/></svg>';
var CHECK_ICON_SVG = '<svg width="16" height="16" viewBox="0 0 16 16" fill="none" xmlns="http://www.w3.org/2000/svg"><path d="M15.0498 3.92579L8.49512 12.3818C8.25774 12.6881 8.04517 12.9645 7.84668 13.1689C7.63957 13.3823 7.38732 13.5841 7.04492 13.6719C6.86373 13.7183 6.6757 13.7346 6.48926 13.7197C6.13666 13.6915 5.8528 13.5355 5.6123 13.3604C5.38201 13.1926 5.12573 12.9567 4.83984 12.6953L1.03125 9.21289L1.96875 8.1875L5.77734 11.6699C6.08684 11.9529 6.27773 12.1249 6.43066 12.2363C6.50183 12.2882 6.54699 12.3135 6.57324 12.3252C6.58525 12.3305 6.59269 12.3322 6.5957 12.333C6.59802 12.3336 6.59961 12.334 6.59961 12.334C6.63317 12.3367 6.66758 12.3335 6.7002 12.3252C6.7002 12.3252 6.70211 12.3251 6.7041 12.3242C6.70698 12.3229 6.71348 12.319 6.72461 12.3115C6.74849 12.2956 6.78843 12.2642 6.84961 12.2012C6.98138 12.0654 7.13957 11.8628 7.39648 11.5313L13.9502 3.07422L15.0498 3.92579Z" fill="currentColor"/></svg>';
async function copyInjectionText(button, text) {
  let ok = false;
  try {
    await navigator.clipboard.writeText(text);
    ok = true;
  } catch {
    const textarea = document.createElement("textarea");
    textarea.value = text;
    textarea.style.position = "fixed";
    textarea.style.opacity = "0";
    document.body.appendChild(textarea);
    textarea.select();
    try {
      ok = document.execCommand("copy");
    } catch {
      ok = false;
    }
    textarea.remove();
  }
  if (!ok || button.dataset.meowInjState === "copied") return;
  button.dataset.meowInjState = "copied";
  button.title = "\u5DF2\u590D\u5236";
  button.innerHTML = CHECK_ICON_SVG;
  window.setTimeout(() => {
    button.dataset.meowInjState = "copy";
    button.title = "\u590D\u5236";
    button.innerHTML = COPY_ICON_SVG;
  }, 1e3);
}
function applyInjectionFold(groups, expanded, onToggle) {
  const containers = Array.from(document.querySelectorAll("[data-chat-flow]"));
  if (containers.length === 0) return;
  const liveIds = new Set(groups.map((g) => g.id));
  for (const container of containers) {
    for (const stale of Array.from(container.querySelectorAll(`[${INJ_ANCHOR_ATTR}]`))) {
      if (!liveIds.has(stale.getAttribute(INJ_ANCHOR_ATTR) ?? "")) stale.remove();
    }
    for (const group of groups) {
      const startRow = flowRow(container, group.id);
      if (startRow === null || startRow.parentElement === null) continue;
      startRow.setAttribute(FOLDED_ATTR, "true");
      let anchor = container.querySelector(`[${INJ_ANCHOR_ATTR}="${CSS.escape(group.id)}"]`);
      if (anchor === null) {
        anchor = document.createElement("div");
        anchor.setAttribute(INJ_ANCHOR_ATTR, group.id);
        startRow.parentElement.insertBefore(anchor, startRow);
      }
      let bar = anchor.querySelector(":scope > button");
      if (bar === null) {
        bar = document.createElement("button");
        bar.type = "button";
        bar.style.cssText = [
          "display:block;margin:4px 0 4px auto;max-width:82%;padding:5px 12px;",
          "font-size:12px;line-height:1.6;text-align:left;cursor:pointer;",
          "color:var(--dsw-text-secondary, rgba(127,127,127,.9));",
          "background:rgba(127,127,127,.07);border:1px solid rgba(127,127,127,.14);",
          "border-radius:999px;"
        ].join("");
        bar.addEventListener("click", () => onToggle(group.id));
        anchor.appendChild(bar);
      }
      const kindLabel = group.kind === "first" ? "\uFF08\u957F\u671F\u8BB0\u5FC6\uFF09" : group.kind === "reflect" ? "\uFF08\u56DE\u987E\u63D0\u793A\uFF09" : group.kind === "dream" ? "\uFF08\u68A6\u5883\u6574\u7406\u62A5\u544A\uFF09" : "\uFF08\u5173\u952E\u8BCD\u547D\u4E2D\uFF09";
      const label = `${expanded.has(group.id) ? "\u25BE" : "\u25B8"} \u5DF2\u6CE8\u5165\u8BB0\u5FC6${kindLabel}`;
      if (bar.textContent !== label) bar.textContent = label;
      let body = anchor.querySelector(`:scope > [${INJ_BODY_ATTR}]`);
      if (body === null) {
        body = document.createElement("div");
        body.setAttribute(INJ_BODY_ATTR, "true");
        anchor.appendChild(body);
      }
      if (expanded.has(group.id)) {
        if (body.textContent !== group.injectedText) body.textContent = group.injectedText;
        body.style.display = "block";
      } else {
        body.style.display = "none";
      }
      let prompt = anchor.querySelector(`:scope > [${INJ_PROMPT_ATTR}]`);
      if (group.userText === void 0) {
        prompt?.remove();
        continue;
      }
      if (prompt === null) {
        prompt = document.createElement("div");
        prompt.setAttribute(INJ_PROMPT_ATTR, "true");
        const bubble2 = document.createElement("div");
        bubble2.dataset.meowInjBubble = "true";
        prompt.appendChild(bubble2);
        const actions = document.createElement("div");
        actions.dataset.meowInjActions = "true";
        const timeLabel2 = document.createElement("span");
        timeLabel2.dataset.meowInjTime = "true";
        actions.appendChild(timeLabel2);
        const copyButton = document.createElement("button");
        copyButton.type = "button";
        copyButton.dataset.meowInjCopy = "true";
        copyButton.title = "\u590D\u5236";
        copyButton.innerHTML = COPY_ICON_SVG;
        copyButton.addEventListener("click", () => {
          if (group.userText !== void 0) {
            void copyInjectionText(copyButton, group.userText);
          }
        });
        actions.appendChild(copyButton);
        prompt.appendChild(actions);
        anchor.appendChild(prompt);
      }
      const bubble = prompt.querySelector(":scope > [data-iwiw-inj-bubble]");
      if (bubble !== null && bubble.textContent !== group.userText) bubble.textContent = group.userText;
      const timeLabel = prompt.querySelector(":scope > [data-iwiw-inj-actions] > [data-iwiw-inj-time]");
      if (timeLabel !== null) {
        const label2 = group.time === void 0 ? "" : formatInjectionClock(group.time);
        if (timeLabel.textContent !== label2) timeLabel.textContent = label2;
        if (label2 === "") timeLabel.style.display = "none";
        else if (timeLabel.style.display !== "") timeLabel.style.display = "";
      }
    }
  }
}
function applyFoldState(groups, expanded, onToggle, session) {
  const containers = Array.from(document.querySelectorAll("[data-chat-flow]"));
  if (containers.length === 0) return;
  const liveIds = new Set(groups.map((group) => group.id));
  for (const container of containers) {
    for (const stale of Array.from(container.querySelectorAll(`[${ANCHOR_ATTR2}]`))) {
      if (!liveIds.has(stale.getAttribute(ANCHOR_ATTR2) ?? "")) stale.remove();
    }
    for (const group of groups) {
      for (const key of group.keys) {
        const row = flowRow(container, key);
        if (row === null) continue;
        row.setAttribute(FOLDED_ATTR, "true");
      }
      ensureAnchor(container, group, expanded.has(group.id), onToggle);
      if (expanded.has(group.id)) {
        const sig = sigOf(container, group.keys);
        if (bodySigs.get(group.id) !== sig) fillBody(group.id, true, group.keys, session);
      } else {
        bodySigs.delete(group.id);
      }
    }
  }
}
function MemoryFoldDock({ session }) {
  const [expanded, setExpanded] = (0, import_react.useState)(() => /* @__PURE__ */ new Set());
  const [injExpanded, setInjExpanded] = (0, import_react.useState)(() => /* @__PURE__ */ new Set());
  const groups = (0, import_react.useMemo)(() => computeFoldGroups(session), [session]);
  const injGroups = (0, import_react.useMemo)(() => computeInjectionGroups(session), [session]);
  const dgNotices = (0, import_react.useMemo)(() => computeDelegateNotices(session), [session]);
  const toggle = (0, import_react.useCallback)((id) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      const willExpand = !next.has(id);
      if (willExpand) next.add(id);
      else next.delete(id);
      const group = groups.find((candidate) => candidate.id === id);
      fillBody(id, willExpand, group?.keys ?? [], session);
      return next;
    });
  }, [groups, session]);
  const toggleInj = (0, import_react.useCallback)((id) => {
    setInjExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }, []);
  (0, import_react.useLayoutEffect)(() => {
    applyFoldState(groups, expanded, toggle, session);
    applyInjectionFold(injGroups, injExpanded, toggleInj);
    applyDelegateNotices(dgNotices);
  }, [groups, expanded, toggle, session, injGroups, injExpanded, toggleInj, dgNotices]);
  const latest = (0, import_react.useRef)({ groups, expanded, toggle, session, injGroups, injExpanded, toggleInj, dgNotices });
  latest.current = { groups, expanded, toggle, session, injGroups, injExpanded, toggleInj, dgNotices };
  (0, import_react.useEffect)(() => {
    let timer = 0;
    const observer = new MutationObserver(() => {
      window.clearTimeout(timer);
      timer = window.setTimeout(() => {
        applyFoldState(latest.current.groups, latest.current.expanded, latest.current.toggle, latest.current.session);
        applyInjectionFold(latest.current.injGroups, latest.current.injExpanded, latest.current.toggleInj);
        applyDelegateNotices(latest.current.dgNotices);
      }, 80);
    });
    observer.observe(document.body, { childList: true, subtree: true });
    return () => {
      window.clearTimeout(timer);
      observer.disconnect();
    };
  }, []);
  return null;
}
function makeDelegateVanishDock(refreshSubagents) {
  return function DelegateVanishDock({ useSessions }) {
    const current = useSessions?.((state) => state?.current);
    const byId = useSessions?.((state) => state?.byId);
    const catalogs = useSessions?.((state) => state?.subagentsByParent);
    const sentinelRef = (0, import_react.useRef)(null);
    const decision = (0, import_react.useMemo)(
      () => computeVanishDecision({ current, byId, subagentsByParent: catalogs }),
      [current, byId, catalogs]
    );
    const latest = (0, import_react.useRef)({ decision });
    latest.current = { decision };
    (0, import_react.useLayoutEffect)(() => {
      applyVanishDom(decision, sentinelRef.current);
    }, [decision]);
    (0, import_react.useEffect)(() => {
      if (current !== void 0 && typeof refreshSubagents === "function") {
        try {
          refreshSubagents(current);
        } catch {
        }
      }
      return startVanishObserver(() => ({ decision: latest.current.decision, sentinel: sentinelRef.current }));
    }, [current]);
    return (0, import_react.createElement)("span", {
      [SENTINEL_ATTR]: "1",
      style: { display: "none" },
      ref: sentinelRef
    });
  };
}
var inject = ["slots", "settingsScope", "sessions"];
function apply(ctx) {
  const disposers = [];
  disposers.push(startDreamIconManager());
  disposers.push(startDelegateStateSync());
  disposers.push(startDreamSkipManager());
  try {
    applySettingsPage(ctx);
  } catch (e) {
    console.warn("[meow-memory] \u8BBE\u7F6E\u9875\u6CE8\u518C\u5931\u8D25\uFF08\u4E0D\u5F71\u54CD\u6298\u53E0\u4E0E\u56FE\u6807\uFF09\uFF1A", e);
  }
  for (const stale of Array.from(document.querySelectorAll("style[data-iwiw-memory-css]"))) {
    stale.remove();
  }
  const style = document.createElement("style");
  style.dataset.iwiwMemoryCss = "true";
  style.textContent = FOLD_CSS;
  document.head.appendChild(style);
  const slots = ctx?.slots;
  if (slots === void 0 || typeof slots.inject !== "function") {
    console.warn("[meow-memory] slots service unavailable; reflection folding disabled");
  } else {
    disposers.push(slots.inject("conversation.composer.dock", () => slots.register(
      {
        name: "conversation.composer.dock",
        id: "dsh-iwiw-memory",
        order: 90
      },
      MemoryFoldDock
    )));
    const sessions = ctx?.sessions;
    const refreshSubagents = typeof sessions?.refreshSubagents === "function" ? (id) => sessions.refreshSubagents(id) : void 0;
    disposers.push(slots.inject("conversation.session.header.actions", () => slots.register(
      {
        name: "conversation.session.header.actions",
        id: "dsh-iwiw-memory",
        order: 200
      },
      makeDelegateVanishDock(refreshSubagents)
    )));
  }
  return () => {
    for (const dispose of disposers) {
      try {
        dispose();
      } catch {
      }
    }
  };
}
    return module.exports;
  }
});
//# sourceMappingURL=client.js.map
