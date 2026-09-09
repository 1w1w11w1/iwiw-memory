"""万字级长对话 · 对话质量与注意力质量评估。

模拟一个约 60 轮（万字级）的真实对话，逐轮驱动 chat 的联想注入管线
（_always_load_text + _related_text），量化：

- 联想命中率：回指轮（用户以联想式问法回指早前话题）期望记忆被注入的比例
- 注入预算：每轮注入字符量（应稳定、不超 MEMORY_RECALL_MAX_CHARS）
- 长程联想：前 1/3 轮埋点的话题在后 1/3 轮仍能被联想（注意力不随会话变长而稀释）
- 注入稳定性：注入量随轮次波动（理想：有话题时注入、闲聊时收敛）

不依赖 LLM 回复，纯注入管线评估（对话质量 = 注入到上下文的信息是否相关且可控）。
运行：python tests/long_conversation_eval.py
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import memory_agent.db as memory_db
from memory_agent.chat import _always_load_text, _related_text, CHAT_RECALL_TOP_K
from memory_agent.config import MEMORY_RECALL_MAX_CHARS
from tests._conversation_data import build_conversation


# ── 播种记忆（模拟写入产物：常驻层 profile + 检索层 fact 混合）──
SEED_MEMORIES = [
    ("health-asthma", "profile", "哮喘病史", "用户有哮喘，避免剧烈运动，随身携带吸入剂。"),
    ("work-python", "profile", "职业", "用户是软件工程师，主用 Python 做后端。"),
    ("hobby-piano", "fact", "学钢琴", "用户每周三晚上七点上钢琴课，老师姓陈，学了一年。"),
    ("plan-marathon", "fact", "跑步计划", "用户计划三个月后参加半程马拉松，目前每周跑三次五公里。"),
    ("relation-brother", "fact", "弟弟", "用户弟弟在上海同济读建筑，明年毕业。"),
    ("food-spicy", "fact", "饮食习惯", "用户爱吃辣，但肠胃敏感，吃多会胃疼。"),
    ("study-german", "fact", "学德语", "用户最近开始学德语，目标明年去柏林出差能日常交流。"),
    ("ref-feedback", "fact", "交流偏好", "用户不喜欢长篇大论，希望直接给结论。"),
]

class IsolatedMemoryDb:
    def __enter__(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.path = Path(self._tmpdir.name) / "memory.db"
        self.original_path = memory_db.MEMORY_DB_PATH
        memory_db.close()
        memory_db.MEMORY_DB_PATH = self.path
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        memory_db.close()
        memory_db.MEMORY_DB_PATH = self.original_path
        self._tmpdir.cleanup()


def main() -> int:
    conversation = build_conversation()
    total_chars = sum(len(t["content"]) for t in conversation)
    print(f"对话规模: {len(conversation)} 轮, {total_chars} 字符")

    with IsolatedMemoryDb():
        for slug, mem_type, desc, content in SEED_MEMORIES:
            memory_db.upsert_memory(slug=slug, description=desc, content=content, mem_type=mem_type)

        always_load = _always_load_text()
        print(f"常驻注入(standing): {len(always_load)} 字符")

        history: list[dict[str, str]] = []
        state_injected: dict[str, int] = {}   # slug -> turn（窗口去重）
        topic_freq: dict[str, int] = {}       # 会话主题轨迹
        compacted = ""                        # DSC 检查点文本
        compact_triggered = 0
        from memory_agent.config import CHAT_COMPACT_TAIL_TURNS
        from memory_agent.session_state import SessionState
        session = SessionState()
        session.build_word_index(memory_db.list_memories())
        recall_hits: list[dict] = []
        inject_sizes: list[int] = []
        # 每个主题最后一次讨论的对话索引（讨论轮带 topic 标注）
        last_discussed: dict[str, int] = {}
        for _i, _t in enumerate(conversation):
            if _t.get("topic"):
                last_discussed[_t["topic"]] = _i
        from memory_agent.config import STANDING_LAYERS
        core_slugs = {
            m["slug"] for layer in STANDING_LAYERS
            for m in memory_db.list_memories(priority="active", mem_type=layer)
        }
        from memory_agent.query_builder import extract_topic_grams, filter_topic_words
        from memory_agent.chat import CHAT_CONTEXT_TURNS

        for i, turn in enumerate(conversation):
            if turn["role"] == "assistant":
                history.append({"role": "assistant", "content": turn["content"]})
                continue
            user = turn["content"]
            # 与 chat 主循环一致的窗口去重 + 主题轨迹
            turn_no = i + 1
            window = 4  # 与 chat 一致：注入只进 system，窗口仅防高频重复
            recent = {s for s, r in state_injected.items() if r >= turn_no - window}
            grams = extract_topic_grams(user)
            for kw in grams:
                topic_freq[kw] = topic_freq.get(kw, 0) + 1
            topic_words = filter_topic_words(topic_freq, limit=30)
            session.update(user, grams)
            # DSC 压缩：超窗口压成检查点 + 保留尾部
            if len(history) > CHAT_CONTEXT_TURNS * 2:
                cp = session.render_checkpoint()
                if cp:
                    compacted = cp
                history = history[-(CHAT_COMPACT_TAIL_TURNS * 2):]
                compact_triggered += 1
            related, injected = _related_text(
                user, history,
                exclude_slugs=core_slugs | recent,
                topic_words=topic_words,
                session_state=session,
            )
            for slug in injected:
                state_injected[slug] = turn_no
            if injected:
                session.note_recalled(injected)
            inject_sizes.append(len(related))
            expect = turn.get("expect")
            if expect:
                last = last_discussed.get(expect[0], 0)
                window_out = (i - last) > CHAT_CONTEXT_TURNS
                if window_out:
                    recall_hits.append({
                        "turn": i,
                        "expected": expect,
                        "hit": any(s in injected for s in expect),
                        "injected": sorted(injected),
                        "size": len(related),
                    })
            history.append({"role": "user", "content": user})

        # ── 指标 ──
        total_recall = len(recall_hits)
        hit_count = sum(1 for r in recall_hits if r["hit"])
        # 长程：后 1/3 轮（回指集中区）的命中
        late = [r for r in recall_hits if r["turn"] >= len(conversation) * 2 // 3]
        late_hit = sum(1 for r in late if r["hit"])
        sizes = inject_sizes
        over_budget = sum(1 for s in sizes if s > MEMORY_RECALL_MAX_CHARS)
        # 注入稳定性：有话题的轮 vs 全部轮
        topic_turns = [r["size"] for r in recall_hits]

        report = {
            "conversation_turns": len(conversation),
            "compact_triggered": compact_triggered,
            "compact_checkpoint_nonempty": bool(compacted),
            "conversation_chars": total_chars,
            "recall_total": total_recall,
            "recall_hits": hit_count,
            "recall_hit_rate": round(hit_count / total_recall, 3) if total_recall else 0,
            "long_range_total": len(late),
            "long_range_hits": late_hit,
            "long_range_hit_rate": round(late_hit / len(late), 3) if late else 0,
            "inject_avg_chars": round(sum(sizes) / len(sizes), 1) if sizes else 0,
            "inject_max_chars": max(sizes) if sizes else 0,
            "over_budget_turns": over_budget,
            "budget": MEMORY_RECALL_MAX_CHARS,
            "core_inject_chars": len(always_load),
            "recall_details": recall_hits,
        }
        print(json.dumps(report, ensure_ascii=False, indent=2))

        ok = (hit_count == total_recall) and (over_budget == 0)
        return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
