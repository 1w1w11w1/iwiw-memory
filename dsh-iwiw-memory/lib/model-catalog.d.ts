/**
 * model-catalog.ts — 把 DSH 内置模型目录的响应压成候选列表。
 *
 * 候选**不**由插件维护：渲染端调 ctx.remote.session.modelCatalog()
 * （与 DSH 自己的模型选择器同源）拿分组目录，这里只做形状归一。
 * 纯函数、无 React、无 IO —— 被 tsconfig 收录，可从 lib 直接断言。
 */
/** 一条候选：展示带 provider 前缀，落库只存 id。 */
export interface ModelOption {
    /** 存进设置的纯模型 id（内核要能直接发给 API）。 */
    id: string;
    /** 下拉里显示的「来源/模型」，如 workbuddy-global/deepseek-v4.1-flash。 */
    label: string;
    /** provider 分组 id（来源）。 */
    provider: string;
}
/**
 * 从 modelCatalog() 的响应里取出候选列表。
 *
 * 展示用 `provider/model`（用户要看得出模型来自哪个来源），
 * 但**落库只存 id** —— provider 前缀是给人看的，不是内核参数。
 *
 * 只认 `{ ok: true, value: { groups: [{ id, models: [{ id }] }] } }` 形状；
 * 任何一层不符合（旧宿主、失败响应、空目录）都返回空数组，
 * 调用方据此把字段退化成自由文本输入 —— 候选缺失不等于设置不可用。
 */
export declare function catalogModelOptions(response: unknown): ModelOption[];
