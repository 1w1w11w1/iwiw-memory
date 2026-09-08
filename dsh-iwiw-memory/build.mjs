/**
 * 构建脚本：host 端（src 除 client*）走 tsc；client 端（client.ts 及其依赖）
 * 走 esbuild bundle（与 meow-memory 同模式——client 依赖的 DSH client 类型包
 * 不在 npm 公开发布，esbuild 不做类型检查，react 由 DSH 渲染进程提供）。
 * 运行：npm run build
 */
import * as esbuild from "esbuild";
import { cpSync, mkdirSync } from "node:fs";

mkdirSync("lib", { recursive: true });

// client bundle（渲染进程，ESM）
await esbuild.build({
  entryPoints: ["src/client.ts"],
  bundle: true,
  outfile: "lib/client.js",
  format: "esm",
  platform: "browser",
  target: "es2022",
  external: ["react"],
  sourcemap: true,
  legalComments: "inline",
});

// prompts 目录与 patch 原样拷贝（若有）
try { cpSync("src/prompts", "lib/prompts", { recursive: true }); } catch {}

console.log("build done: lib/client.js");
