#!/usr/bin/env node
/**
 * release.mjs — 一键发布：build → smoke → bump → commit → publish → push
 *
 * 用法：
 *   npm run release              # patch 版本（如 0.1.0 → 0.1.1）
 *   npm run release -- --minor   # minor 版本
 *   npm run release -- --major   # major 版本
 *   npm run release -- --dry-run # 只 build + smoke + 预览，不改版本/不提交/不发布
 *
 * 行为约定：
 * - 自动把全部工作区改动（含 lib 重建产物与版本号）提交入库，随后 npm version 创建
 *   bump commit + vX.Y.Z tag，发布到官方源后连同 tag 一起 push。
 * - build 或 smoke 失败立即中止，不会发布半成品。
 * - publish 使用官方 registry（本机默认 npmmirror 只读，不能发布）。
 * - 2FA 账号发布依赖 ~/.npmrc 里的 Automation 令牌（bypass OTP），缺失时会报 EOTP。
 */
import { execSync } from "node:child_process";
import { readFileSync } from "node:fs";

const args = process.argv.slice(2);
const dryRun = args.includes("--dry-run");
const bump = args.includes("--major") ? "major" : args.includes("--minor") ? "minor" : "patch";

const sh = (cmd) => execSync(cmd, { stdio: "pipe", shell: true, encoding: "utf8" }).trim();
const step = (msg) => console.log(`\n==> ${msg}`);
const pkgPath = new URL("../package.json", import.meta.url);
const ver = () => JSON.parse(readFileSync(pkgPath, "utf8")).version;

try {
  console.log(`dsh-iwiw-memory v${ver()} → ${bump} release${dryRun ? "（dry-run）" : ""}`);

  step("1/5 build（tsc + esbuild）");
  sh("npm run build");
  console.log("build OK");

  step("2/5 host 冒烟");
  const smoke = sh("node scripts/host-smoke.mjs");
  if (!smoke.includes("SMOKE ALL PASS")) {
    console.error(smoke.split("\n").slice(-20).join("\n"));
    throw new Error("host-smoke 未通过，发布中止");
  }
  console.log("smoke OK");

  if (dryRun) {
    step("dry-run 结束：未修改版本号、未提交、未发布");
    console.log(`将要执行：npm version ${bump}（commit + tag）→ npm publish（官方源）→ push main+tags`);
    process.exit(0);
  }

  step(`3/5 提交工作区改动并 bump 版本（npm version ${bump}）`);
  sh("git add -A");
  if (sh("git status --porcelain")) sh('git commit -m "chore: release prep"');
  sh(`npm version ${bump} -f`);
  const newVer = ver();

  step("4/5 npm publish（官方源）");
  sh("npm publish --registry=https://registry.npmjs.org/");
  console.log(`published dsh-iwiw-memory@${newVer}`);

  step("5/5 push main + tags");
  sh("git push origin main --follow-tags");

  console.log(`\n✅ dsh-iwiw-memory@${newVer} 发布完成：https://www.npmjs.com/package/dsh-iwiw-memory`);
} catch (e) {
  console.error(`\n❌ 发布中止：${String(e.message ?? e).split("\n")[0]}`);
  process.exit(1);
}
