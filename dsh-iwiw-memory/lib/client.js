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
  apply: () => apply,
  inject: () => inject
});
module.exports = __toCommonJS(client_exports);

// src/settings-page.ts
var React = __toESM(require("react"), 1);
var SETTINGS_NS = "dsh-iwiw-memory";
var CSS_ID = "iwiw-memory-settings-css";
var CSS = `
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
    title: "\u56DE\u987E\u4E0E\u5DE9\u56FA",
    fields: [
      { key: "reflectTurns", label: "\u56DE\u987E\u63D0\u793A\u89E6\u53D1\u6B65\u6570", type: "num", hint: "\u8FDE\u7EED N \u6B65\u672A\u5199\u5165\u8BB0\u5FC6\u65F6\u6CE8\u5165\u4E00\u6B21\u6027\u56DE\u987E\u63D0\u793A\uFF1B0=\u5173\u95ED" },
      { key: "consolidateIdleMinutes", label: "\u7A7A\u95F2\u5DE9\u56FA\u9608\u503C\uFF08\u5206\u949F\uFF09", type: "num", hint: "\u7A7A\u95F2\u6EE1\u8BE5\u5206\u949F\u6570\u4E14\u975E\u5CF0\u65F6\uFF089-12/14-18\uFF09\u81EA\u52A8\u89E6\u53D1\u8BB0\u5FC6\u5DE9\u56FA\uFF1B0=\u5173\u95ED" }
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
      "\u672C\u5730\u957F\u671F\u8BB0\u5FC6\u63D2\u4EF6\u7684\u8FD0\u884C\u53C2\u6570\u3002\u6539\u52A8\u4FDD\u5B58\u5728 DSH \u8BBE\u7F6E\u91CC\uFF08\u5B57\u6BB5\u7EA7\uFF0C\u53EF\u5355\u9879\u6062\u590D\u9ED8\u8BA4\uFF09\uFF1BhitTopK / reflectTurns / consolidateIdleMinutes / standingLayers \u70ED\u751F\u6548\uFF0CcoreMaxChars \u5728\u4E0B\u6B21 core \u6BB5\u5237\u65B0\u65F6\u751F\u6548\u3002"
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
  if (typeof document !== "undefined" && document.querySelector(`style[data-plugin-css="${CSS_ID}"]`) === null) {
    const tag = document.createElement("style");
    tag.dataset.plugin = "iwiw-memory-settings";
    tag.dataset.pluginCss = CSS_ID;
    tag.textContent = CSS;
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
var inject = ["slots", "settingsScope"];
function apply(ctx) {
  try {
    applySettingsPage(ctx);
  } catch (e) {
    console.warn("[iwiw-memory] \u8BBE\u7F6E\u9875\u6CE8\u518C\u5931\u8D25\uFF1A", e);
  }
  return () => {
  };
}
    return module.exports;
  }
});
//# sourceMappingURL=client.js.map
