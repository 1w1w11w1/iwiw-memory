# meow-memory client 源码（迁移参考）

来源：meow-memory@0.24.1 的 lib/client.js.map sourcesContent（TS 源码原文），
从本机安装包提取。上游仓库：https://github.com/Phant0Meow/dsh-meow-memory
许可证：MIT（Copyright Phant0Meow）——本项目（dsh-iwiw-memory，MIT）迁移其
client-fold 注入折叠逻辑时保留原署名，适配点仅限 PLUGIN_NAME 与文本标记。

| 文件 | 大小 | 迁移状态 |
|---|---|---|
| client.ts | 30KB | 部分（apply/slots/CSS 骨架 + 注入折叠 DOM，待换装阶段验证） |
| client-fold.ts | 11KB | ✅ 已迁 src/client-fold.ts（纯计算，宿主断言覆盖） |
| client-delegate-vanish.ts | 7.8KB | 未迁（依赖 delegate 场景，暂无） |
| client-delegate-notice.ts | 14.4KB | 未迁（依赖 delegate） |
| client-dream-events.ts | 3.9KB | 未迁（依赖 dream，暂无） |
| client-dream-icon.ts | 11.2KB | 未迁（依赖 dream） |
| client-dream-skip.ts | 11KB | 未迁（依赖 dream） |
| settings-page.ts | 18KB | 未迁（配置项少，暂无） |
