#!/usr/bin/env node
/**
 * release.mjs — 一键发布：build → smoke → changelog → bump → commit → publish → push → GitHub Release
 *
 * 用法：
 *   npm run release              # patch 版本（如 0.1.0 → 0.1.1）
 *   npm run release -- --minor   # minor 版本
 *   npm run release -- --major   # major 版本
 *   npm run release -- --dry-run # 只 build + smoke + 预览 changelog，不改状态不发布
 *
 * changelog：聚合上一个 tag → HEAD 的 conventional commits（feat/fix/docs/revert/chore…）
 * 按类型分组，prepend 到 CHANGELOG.md，并以相同内容创建 GitHub Release。
 *
 * 行为约定：
 * - 自动提交全部工作区改动（含 CHANGELOG、lib 重建产物与版本号），npm version 创建 bump commit + tag。
 * - build 或 smoke 失败立即中止；publish 使用官方 registry（本机默认 npmmirror 只读）。
 * - 2FA 账号发布依赖 ~/.npmrc 里的 Automation 令牌（bypass OTP）。
 * - gh CLI 需要 HTTPS_PROXY（本机直连 api.github.com 被重置，默认走 Clash 7897）。
 */
import { execSync } from "node:child_process";
import { readFileSync, writeFileSync, existsSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const args = process.argv.slice(2);
const dryRun = args.includes("--dry-run");
const bump = args.includes("--major") ? "major" : args.includes("--minor") ? "minor" : "patch";

const GH_PROXY = process.env.HTTPS_PROXY ?? "http://127.0.0.1:7897";
const ghEnv = { ...process.env, HTTPS_PROXY: GH_PROXY, HTTP_PROXY: GH_PROXY };

const sh = (cmd, opts = {}) => execSync(cmd, { stdio: "pipe", shell: true, encoding: "utf8", ...opts }).trim();
const step = (msg) => console.log(`\n==> ${msg}`);
const pkgPath = new URL("../package.json", import.meta.url);
const ver = () => JSON.parse(readFileSync(pkgPath, "utf8")).version;
const repoRoot = sh("git rev-parse --show-toplevel");

// ── changelog：conventional commits → 分类段落 ──
const TYPE_MAP = {
  feat: "新增功能",
  fix: "修复问题",
  revert: "回退",
  docs: "文档",
  chore: "工程优化",
  refactor: "工程优化",
  perf: "工程优化",
  style: "工程优化",
  test: "工程优化",
};
const SKIP = /^(release prep|v\d)/i; // 脚本自生成的机械提交

function collectCommits(sinceTag) {
  const range = sinceTag ? `${sinceTag}..HEAD` : "HEAD";
  const raw = sh(`git log ${range} --format="%s%x09%h"`);
  if (!raw) return [];
  return raw
    .split("\n")
    .map((line) => {
      const [subject, hash] = line.split("\t");
      const clean = subject.replace(/^\uFEFF/, ""); // pwsh 管道 commit -F - 会写入 BOM
      const m = clean.match(/^(\w+)(\([^)]*\))?!?:\s*(.+)$/);
      if (!m) return { type: "other", desc: clean, hash };
      return { type: m[1].toLowerCase(), scope: m[2] ?? "", desc: m[3], hash };
    })
    .filter((c) => !SKIP.test(c.desc));
}

function renderChangelogSection(version, commits) {
  const groups = new Map();
  for (const c of commits) {
    const label = TYPE_MAP[c.type] ?? "其他";
    if (!groups.has(label)) groups.set(label, []);
    groups.get(label).push(`- ${c.desc} (${c.hash})`);
  }
  const today = new Date().toISOString().slice(0, 10);
  const lines = [`## [${version}] - ${today}`, ""];
  for (const [label, items] of groups) {
    lines.push(`### ${label}`, ...items, "");
  }
  return { markdown: lines.join("\n").trimEnd(), body: lines.slice(2).join("\n").trimEnd() };
}

function updateChangelogFile(section) {
  const path = join(repoRoot, "CHANGELOG.md");
  const header = "# Changelog\n\n本文件由 `npm run release` 自动维护（基于 conventional commits）。\n\n";
  const existing = existsSync(path) ? readFileSync(path, "utf8") : header;
  const insertAt = existing.indexOf("## [");
  const next = insertAt === -1
    ? header + section + "\n"
    : existing.slice(0, insertAt) + section + "\n\n" + existing.slice(insertAt);
  writeFileSync(path, next.endsWith("\n") ? next : next + "\n");
}

try {
  console.log(`dsh-iwiw-memory v${ver()} → ${bump} release${dryRun ? "（dry-run）" : ""}`);

  step("1/6 build（tsc + esbuild）");
  sh("npm run build");
  console.log("build OK");

  step("2/6 host 冒烟");
  const smoke = sh("node scripts/host-smoke.mjs");
  if (!smoke.includes("SMOKE ALL PASS")) {
    console.error(smoke.split("\n").slice(-20).join("\n"));
    throw new Error("host-smoke 未通过，发布中止");
  }
  console.log("smoke OK");

  step("3/6 聚合 changelog");
  let lastTag = "";
  try { lastTag = sh("git describe --tags --abbrev=0"); } catch { /* 首个版本无 tag */ }
  const commits = collectCommits(lastTag);
  if (commits.length === 0) throw new Error(`${lastTag || "空仓库"} 之后没有可发布的提交`);
  const preview = ver().replace(/\d+$/, (n) => String(Number(n) + ({ patch: 1, minor: 0, major: 10 }[bump])));
  const { markdown, body } = renderChangelogSection(preview, commits);
  console.log(markdown);

  if (dryRun) {
    step("dry-run 结束：未修改版本号/CHANGELOG，未提交、未发布");
    console.log(`将要执行：写入 CHANGELOG.md → npm version ${bump} → npm publish → push → gh release create`);
    process.exit(0);
  }
  updateChangelogFile(markdown);

  step("4/6 提交并 bump 版本（npm version）");
  sh("git add -A");
  if (sh("git status --porcelain")) sh('git commit -m "chore: release prep"');
  sh(`npm version ${bump} -f`);
  const newVer = ver();

  step("5/6 npm publish（官方源）");
  sh("npm publish --registry=https://registry.npmjs.org/");
  console.log(`published dsh-iwiw-memory@${newVer}`);

  step("6/6 push main + tags，创建 GitHub Release");
  sh("git push origin main --follow-tags");
  const notesFile = join(tmpdir(), `release-notes-${newVer}.md`);
  writeFileSync(notesFile, body, "utf8");
  sh(`gh release create v${newVer} --title "v${newVer}" --notes-file "${notesFile}" --latest`, { env: ghEnv, cwd: repoRoot });
  console.log(`GitHub Release: https://github.com/1w1w11w1/iwiw-memory/releases/tag/v${newVer}`);

  console.log(`\n✅ dsh-iwiw-memory@${newVer} 发布完成（CHANGELOG.md 与 GitHub Release 已更新）`);
} catch (e) {
  console.error(`\n❌ 发布中止：${String(e.message ?? e).split("\n")[0]}`);
  process.exit(1);
}
