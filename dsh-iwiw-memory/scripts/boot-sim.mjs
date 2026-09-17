/* 启动级仿真：重放 dsh-plugin-desktop 对 profile bundles 的完整校验流
 * （对齐 profile bundle 解析逻辑，解析器用 DSH 自家的 dsh-app-boot）。
 *
 * 路径不写死：从 DSH_HOME（或 ~/.dsh）出发，自动挑选包含 @iwiw/dsh-iwiw-memory
 * 的 profile。本机曾存在 D:/dsh/DSH Desktop/... 与 profiles/desktop，二者都已
 * 不存在；硬编码会直接失败且掩盖真实部署状态。
 */
import { readFileSync, existsSync, readdirSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";
import { pathToFileURL } from "node:url";
import { createRequire } from "node:module";

const PLUGIN = "@iwiw/dsh-iwiw-memory";

/** 定位 DSH_HOME：环境变量优先，其次 ~/.dsh。 */
function resolveDshHome() {
  const fromEnv = process.env.DSH_HOME;
  const home = fromEnv && existsSync(fromEnv) ? fromEnv : join(homedir(), ".dsh");
  if (!existsSync(home)) throw new Error(`cannot locate DSH home (tried ${fromEnv ?? "DSH_HOME unset"} and ${home})`);
  return home;
}

/** 找出实际挂载了本插件的 profile（可能有多个）。 */
function findProfiles(dshHome) {
  const profilesRoot = join(dshHome, "profiles");
  if (!existsSync(profilesRoot)) throw new Error(`no profiles directory at ${profilesRoot}`);
  const found = [];
  for (const name of readdirSync(profilesRoot)) {
    const dir = join(profilesRoot, name);
    const manifestPath = join(dir, "package.json");
    if (!existsSync(manifestPath)) continue;
    let manifest;
    try { manifest = JSON.parse(readFileSync(manifestPath, "utf8")); } catch { continue; }
    const bundles = manifest.dsh?.profile?.bundles ?? [];
    if (bundles.includes(PLUGIN)) found.push({ name, dir, manifest, bundles });
  }
  return found;
}

const dshHome = resolveDshHome();
const profiles = findProfiles(dshHome);
if (profiles.length === 0) throw new Error(`no profile mounts ${PLUGIN} under ${join(dshHome, "profiles")}`);
console.log(`DSH home: ${dshHome}`);
console.log(`profiles mounting ${PLUGIN}: ${profiles.map((p) => p.name).join(", ")}`);

// 解析器：优先用 profile 自己解析到的 dsh-app-boot（bare specifier 在本仓库解析不到）。
function loadAppBoot(profileDir) {
  const req = createRequire(join(profileDir, "noop.js"));
  for (const spec of ["@deepseek-ai/dsh-app-boot", "@deepseek-ai/dsh-app-boot/lib/index.js"]) {
    try {
      const resolved = req.resolve(spec);
      return import(pathToFileURL(resolved).href);
    } catch {
      // 试下一个候选
    }
  }
  // 兜底：从全局安装树解析（新版本布局）
  const globalReq = createRequire(
    process.env.NODE_PATH ? join(process.env.NODE_PATH, "noop.js") : join(process.execPath, "..", "noop.js"),
  );
  const resolved = globalReq.resolve("@deepseek-ai/dsh-app-boot");
  return import(pathToFileURL(resolved).href);
}

/** 对齐 resolveOverlayPackage 的 overlay 语义：profile 层优先，安装锚点兜底。 */
function resolvePackageDir(profileDir, packageName) {
  const inProfile = join(profileDir, "node_modules", packageName);
  if (existsSync(join(inProfile, "package.json"))) return inProfile;
  const req = createRequire(join(profileDir, "noop.js"));
  try {
    return join(req.resolve(`${packageName}/package.json`), "..");
  } catch {
    throw new Error(`cannot resolve profile bundle ${packageName} from ${profileDir}`);
  }
}

let checked = 0;
for (const profile of profiles) {
  const appBoot = await loadAppBoot(profile.dir);
  const loadOverlayPatches = appBoot.loadOverlayPatches;
  if (typeof loadOverlayPatches !== "function") {
    throw new Error(`dsh-app-boot resolved for ${profile.name} exports no loadOverlayPatches`);
  }

  for (const packageName of profile.bundles) {
    const packageDir = resolvePackageDir(profile.dir, packageName);
    const pkg = JSON.parse(readFileSync(join(packageDir, "package.json"), "utf8"));
    const declared = pkg.dsh?.bundle?.patch;
    if (typeof declared !== "string" || declared.length === 0) {
      throw new Error(`bundle ${packageName} declares no dsh.bundle in its package.json`);
    }
    const patchPath = join(packageDir, declared);
    const patches = loadOverlayPatches("smoke", patchPath);
    checked += 1;
    // 对 iwiw 自身：insert 行必须装载自己的包名（name 是 import() 说明符）
    if (packageName === PLUGIN) {
      const selfInsert = patches.flatMap((p) => p.insert ?? []).find((r) => r.name === packageName);
      if (!selfInsert) throw new Error(`iwiw insert 行缺失或 name 不是 ${packageName}`);
      console.log(`[${profile.name}] bundle ${packageName}: patch OK, insert id=${selfInsert.id} config=${JSON.stringify(selfInsert.config)}`);
    }
  }

  // profile patch 层：id 覆盖目标必须存在
  const profilePatchPath = join(profile.dir, "cordis.patch.yml");
  if (!existsSync(profilePatchPath)) throw new Error(`${profile.name}: profile cordis.patch.yml missing`);
  const profilePatch = readFileSync(profilePatchPath, "utf8");
  if (!profilePatch.includes(PLUGIN)) throw new Error(`${profile.name}: profile patch 缺 iwiw id 覆盖`);

  // 依赖清单一致性：dependencies 与 bundles 都指向 iwiw
  if (!profile.manifest.dependencies?.[PLUGIN]) throw new Error(`${profile.name}: dependencies 缺 iwiw`);
}

console.log(`BOOT SIMULATION: ALL PASS (${profiles.length} profile(s), ${checked} bundle(s))`);
