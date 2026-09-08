/**
 * iwiw 记忆 — 注入折叠：纯计算逻辑（与 DOM 无关，可在宿主冒烟中单测）。
 *
 * 迁移自 meow-memory（MIT License，Copyright Phant0Meow）client-fold.ts，
 * 按 dsh-iwiw-memory 的快照消息 shape 适配（PLUGIN_NAME 与 source.plugin 对齐）。
 *
 * 识别：会话快照 chat 节点里 kind='context' 且 source 为
 * { kind: 'plugin', plugin: 'dsh-iwiw-memory' } 的节点 = 插件注入的快照消息。
 * fail-closed：快照形状变化时降级为不折叠，绝不抛错——computeInjectionGroups
 * 在每次渲染都跑，一炸就是一整个会话视图。
 */
/** 插件 source 识别（与 host 端 index.ts 快照消息保持一致）。 */
export const PLUGIN_NAME = 'dsh-iwiw-memory';
/** 反思/dream 轮识别标记（与 host 端 reflect/dream 保持一致；当前未启用，预留）。 */
export const REFLECT_MARKER = '[iwiw-memory-reflect]';
export const DREAM_MARKER = '[iwiw-memory-dream]';
/** 从 content blocks 提取纯文本。 */
export function blocksToText(blocks) {
    return blocks
        .map((block) => block.text ?? '')
        .join('\n')
        .trim();
}
function contextText(node) {
    return blocksToText(node.data.content ?? []);
}
/**
 * 从会话快照计算全部可折叠的注入组。
 * 新格式识别 source.memory.kind (hit/reinjection) 机器元数据，解耦于自然语言文本。
 */
export function computeInjectionGroups(snapshot) {
    if (snapshot?.chat === undefined)
        return []; // fail-closed：快照无 chat 时不折叠，绝不抛错
    const groups = [];
    for (const key of snapshot.chat.order) {
        const node = snapshot.chat.nodes.get(key);
        if (node === undefined)
            continue;
        if (node.kind !== 'context')
            continue;
        const source = node.data.source;
        if (source?.kind !== 'plugin' || source.plugin !== PLUGIN_NAME)
            continue;
        const memKind = source.memory?.kind;
        if (memKind === 'reinjection') {
            groups.push({ id: key, kind: 'first', injectedText: contextText(node) });
            continue;
        }
        if (memKind === 'hit') {
            groups.push({ id: key, kind: 'hit', injectedText: contextText(node) });
            continue;
        }
        if (source.form === 'snapshot') {
            // 未带 memory.kind 的旧快照：按文本内容判定
            const injectedText = contextText(node);
            const isHit = injectedText.includes('相关记忆（命中）');
            groups.push({ id: key, kind: isHit ? 'hit' : 'first', injectedText });
        }
    }
    return groups;
}
