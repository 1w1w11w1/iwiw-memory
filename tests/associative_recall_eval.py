"""联想（associative recall）质量离线评估。

验证三类联想场景 + 一类反例：
- direct      用户直接提到关键词 → 应命中
- semantic    语义近义表述（不提关键词）→ 应命中（同义词表 + jieba 词面）
- topic_assoc 当前消息无关键词，靠对话主题联想 → 应命中（联想核心能力）
- unrelated   无关话题 → 不应命中

不依赖 LLM，纯检索 + 注入管线评估。
运行：python tests/associative_recall_eval.py
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import memory_agent.db as memory_db
from memory_agent.retrieval import search_memories
from memory_agent.query_builder import build_queries

SEED_MEMORIES = [
    ("health-asthma", "profile", "哮喘病史", "用户有哮喘，避免剧烈运动，随身带药。"),
    ("work-python", "profile", "职业", "用户是软件工程师，主用 Python。"),
    ("hobby-piano", "fact", "学钢琴", "用户每周三晚上七点上钢琴课，老师姓陈。"),
    ("plan-marathon", "fact", "跑步计划", "用户计划三个月后参加半程马拉松，每周跑三次。"),
    ("relation-brother", "profile", "弟弟", "用户弟弟在上海读大学，学建筑。"),
    ("food-spicy", "profile", "饮食习惯", "用户爱吃辣，但肠胃不好，吃辣后容易胃疼。"),
    ("study-german", "fact", "学德语", "用户最近开始学德语，目标是明年去柏林出差能日常交流。"),
    ("ref-feedback", "profile", "交流偏好", "用户不喜欢长篇大论的回答，希望直接给结论。"),
]


class IsolatedMemoryDb:
    """隔离临时库（检索已全部确定性，无向量依赖）。"""

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


def seed() -> None:
    for slug, mem_type, desc, content in SEED_MEMORIES:
        memory_db.upsert_memory(
            slug=slug, description=desc, content=content,
            mem_type=mem_type,
        )


def recall(
    message: str,
    context: list[str] | None = None,
    top_k: int = 5,
    session_state=None,
) -> list[str]:
    """模拟 chat 联想注入：返回注入候选的 slug 列表。"""
    results = search_memories(
        message, context_messages=context, top_k=top_k, session_state=session_state
    )
    return [r["slug"] for r in results]


# (case_name, message, context, expect_slugs, expect_hit)
CASES: list[tuple[str, str, list[str], list[str], bool]] = [
    # direct：直接关键词
    ("direct_piano", "钢琴课是每周几？", [], ["hobby-piano"], True),
    ("direct_asthma", "我的哮喘平时要注意什么", [], ["health-asthma"], True),
    # semantic：语义近义（不提关键词）
    ("semantic_job", "我平时写代码用什么语言", [], ["work-python"], True),
    ("semantic_brother", "我弟现在在哪读书", [], ["relation-brother"], True),
    ("semantic_spicy", "吃辣的东西对我有什么影响", [], ["food-spicy"], True),
    # topic_assoc：当前消息无关键词，靠对话主题联想
    ("topic_followup", "那然后呢？", ["跑步计划怎么样了", "用户说想参加半程马拉松"], ["plan-marathon"], True),
    ("topic_followup2", "挺好的，继续说说", ["学德语进展", "用户想明年去柏林出差"], ["study-german"], True),
    ("topic_short", "嗯嗯", ["最近练琴怎么样", "钢琴老师姓陈"], ["hobby-piano"], True),
    # unrelated：无关话题不应命中
    ("unrelated_weather", "今天天气怎么样", [], [], False),
    ("unrelated_movie", "推荐一部电影", [], [], False),
]


# topic 用例：先通过 SessionState 记录"会话中讨论过的话题"，测 L2 回指联想
TOPIC_SESSION = {
    "topic_followup": ["跑步计划怎么样了", "半程马拉松", "每周跑三次"],
    "topic_followup2": ["学德语进展", "柏林出差", "德语"],
    "topic_short": ["最近练琴怎么样", "钢琴老师姓陈", "每周三晚上七点"],
}


def _build_session(history_words: list[str], memories) -> "object":
    from memory_agent.session_state import SessionState
    ss = SessionState()
    ss.build_word_index(memories)
    for words in history_words:
        ss.update(" ".join(words), [w for w in words if len(w) >= 2])
    return ss


def run_all() -> list[dict]:
    results: list[dict] = []
    with IsolatedMemoryDb():
        seed()
        mems = memory_db.list_memories()
        for name, message, context, expect, want_hit in CASES:
            ss = None
            if name in TOPIC_SESSION:
                ss = _build_session(TOPIC_SESSION[name], mems)
            got = recall(message, context, session_state=ss)
            # hit 统一表示"预期被满足"：有预期 slug → 是否命中；无预期 → 是否无注入
            hit = any(s in got for s in expect) if expect else len(got) == 0
            ok = hit
            results.append({
                "case": name,
                "ok": ok,
                "message": message[:30],
                "context_hint": (context[-1] if context else "")[:24],
                "expected": expect,
                "got": got,
            })
    return results


def main() -> int:
    results = run_all()
    ok = sum(1 for r in results if r["ok"])
    print(json.dumps(results, ensure_ascii=False, indent=2))
    print(f"\n联想评估: {ok}/{len(results)} 通过")
    by_group: dict[str, list[dict]] = {}
    for r in results:
        by_group.setdefault(r["case"].split("_")[0], []).append(r)
    for group, items in by_group.items():
        gok = sum(1 for i in items if i["ok"])
        print(f"  {group:12s} {gok}/{len(items)}")
    return 0 if ok == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
