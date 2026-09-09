/**
 * 构建脚本：host 端（src 除 client*）走 tsc；client 端（client.ts 及其依赖）
 * 走 esbuild bundle（client 依赖的 DSH client 类型包不在 npm 公开发布，
 * esbuild 不做类型检查，react 由 DSH 渲染进程提供）。
 *
 * client 产物必须符合 DSH 渲染端 __ModuleLoader__ 约定：文件被拼进
 * /plugins/??a.js,b.js 合并包后，每个模块都要在求值时调用
 * window.__ModuleLoader__.load({ id, factory }) 自注册，否则整个合并包
 * 会报 "loaded without registering ... via __ModuleLoader__.load"。
 * 因此用 cjs 格式 + banner/footer 把 bundle 包成 factory（react 保持 external，
 * 经工厂注入的 require 获取）。
 * 运行：npm run build
 */
import * as esbuild from "esbuild";
import { cpSync, mkdirSync } from "node:fs";

mkdirSync("lib", { recursive: true });

const CLIENT_ID = "@iwiw/dsh-iwiw-memory";

// client bundle（渲染进程，__ModuleLoader__ cjs factory）
await esbuild.build({
  entryPoints: ["src/client.ts"],
  bundle: true,
  outfile: "lib/client.js",
  format: "cjs",
  platform: "browser",
  target: "es2022",
  external: ["react"],
  sourcemap: true,
  legalComments: "inline",
  banner: {
    js:
      "window.__ModuleLoader__.load({\n" +
      `  id: ${JSON.stringify(CLIENT_ID)},\n` +
      "  factory: (require) => {\n" +
      // 本包是 type:module，esbuild 不会自动注入 cjs 垫片，需显式声明
      "    var module = { exports: {} };\n" +
      "    var exports = module.exports;\n",
  },
  footer: {
    js: "    return module.exports;\n  }\n});",
  },
});

// prompts 目录与 patch 原样拷贝（若有）
try { cpSync("src/prompts", "lib/prompts", { recursive: true }); } catch {}

console.log("build done: lib/client.js (ModuleLoader factory wrapper)");
