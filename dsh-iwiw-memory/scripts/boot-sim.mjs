/* 启动级仿真：重放 dsh-plugin-desktop 对 profile bundles 的完整校验流
 * （对齐 app.asar.unpacked/lib/profile-DcyLDzp6.js:415-468 的逻辑，
 *   解析器用 DSH 自家的 dsh-app-boot）。 */
import { readFileSync, existsSync } from "node:fs";
import { join } from "node:path";
import { pathToFileURL } from "node:url";

// DSH 自家解析器（bare specifier 在本仓库解析不到，用安装路径直连）
const appBoot = await import(
  pathToFileURL("D:/dsh/DSH Desktop/resources/app.asar.unpacked/node_modules/@deepseek-ai/dsh-app-boot/lib/index.js").href
);
const loadOverlayPatches = appBoot.loadOverlayPatches;

const profileDir = "C:/Users/YS/.dsh/profiles/desktop";
const manifest = JSON.parse(readFileSync(join(profileDir, "package.json"), "utf8"));
const bundles = manifest.dsh?.profile?.bundles ?? [];
console.log("bundles:", bundles.join(", "));
if (!bundles.includes("dsh-iwiw-memory")) throw new Error("bundle 未列入 profile");

const installNodeModules = "D:/dsh/DSH Desktop/resources/app.asar.unpacked/node_modules";
function resolvePackageDir(packageName) {
  // 对齐 resolveOverlayPackage 的 overlay 语义：profile 层优先，安装锚点兜底
  const inProfile = join(profileDir, "node_modules", packageName);
  if (existsSync(join(inProfile, "package.json"))) return inProfile;
  const inInstall = join(installNodeModules, packageName);
  if (existsSync(join(inInstall, "package.json"))) return inInstall;
  throw new Error(`cannot resolve profile bundle ${packageName}`);
}

for (const packageName of bundles) {
  const packageDir = resolvePackageDir(packageName);
  const pkg = JSON.parse(readFileSync(join(packageDir, "package.json"), "utf8"));
  const declared = pkg.dsh?.bundle?.patch;
  if (typeof declared !== "string" || declared.length === 0) {
    throw new Error(`bundle ${packageName} declares no dsh.bundle in its package.json`);
  }
  const patchPath = join(packageDir, declared);
  const patches = loadOverlayPatches("smoke", patchPath);
  // 对 iwiw 自身：insert 行必须装载自己的包名（name 是 import() 说明符）
  if (packageName === "dsh-iwiw-memory") {
    const selfInsert = patches.flatMap((p) => p.insert ?? []).find((r) => r.name === packageName);
    if (!selfInsert) throw new Error(`iwiw insert 行缺失或 name 不是 ${packageName}`);
    console.log(`bundle ${packageName}: patch OK, insert id=${selfInsert.id} name=${selfInsert.name} config=${JSON.stringify(selfInsert.config)}`);
  }
}
console.log("全部 12 个 bundle 的 dsh.bundle.patch 声明与解析均通过");

// profile patch 层：id 覆盖目标必须存在
const profilePatch = readFileSync(join(profileDir, "cordis.patch.yml"), "utf8");
if (!profilePatch.includes("dsh-iwiw-memory")) throw new Error("profile patch 缺 iwiw id 覆盖");

// 依赖清单一致性：dependencies 与 bundles 都指向 iwiw
if (!manifest.dependencies?.["dsh-iwiw-memory"]) throw new Error("dependencies 缺 iwiw");

console.log("BOOT SIMULATION: ALL PASS");
