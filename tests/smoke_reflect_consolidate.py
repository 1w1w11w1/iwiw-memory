"""reflect + consolidate 内核侧 tracer bullet（本地冒烟，不入库提交）。"""
import asyncio
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import memory_agent.db as memory_db

tmp = Path(tempfile.mkdtemp()) / "smoke.db"
memory_db.MEMORY_DB_PATH = tmp
memory_db._connection = None

from memory_agent.db import upsert_memory, get_memory, list_pending_actions
from memory_agent import maintenance
from memory_agent.config import REFLECT_TURNS

# ── ⑤ reflect：配置可加载、chat 模块导入无损（轮次逻辑由 E2E 覆盖）──
assert REFLECT_TURNS >= 1, "REFLECT_TURNS 默认值异常"
import memory_agent.chat  # noqa: F401  语法/导入完整性
print(f"reflect: REFLECT_TURNS={REFLECT_TURNS}，chat 导入 OK")

# ── consolidate：canned LLM 输出做确定性验证 ──
# 空库短路（先于建数据验证）
r_empty = asyncio.run(maintenance.run_consolidate(since_hours=24))
assert r_empty["reviewed"] == 0, "空库应短路返回"

upsert_memory(slug="d-fact", description="某个项目决策", content="dsh 换装链的拍板过程", mem_type="fact")
upsert_memory(slug="d-desc", description="这个是那个东西反正就是", content="用户每周三晚上打球", mem_type="fact")
upsert_memory(slug="d-old", description="假期出行计划", content="十一去成都玩五天", mem_type="fact")
upsert_memory(slug="d-prof", description="用户身份", content="用户是开发者", mem_type="profile")

CANNED = '''[
 {"action": "retype", "slug": "d-fact", "mem_type": "project", "reason": "项目决策应归 project"},
 {"action": "update_desc", "slug": "d-desc", "description": "用户每周三晚打球的运动习惯", "reason": "原描述含混"},
 {"action": "archive", "slug": "d-old", "reason": "假期已结束的短期计划"},
 {"action": "archive", "slug": "d-prof", "reason": "试探：profile 不应被归档"},
 {"action": "merge", "slug": "d-old", "target_slug": "d-desc", "reason": "试探性合并建议"},
 {"action": "keep", "slug": "无关slug", "reason": "未知条目应被忽略"}
]'''


async def fake_complete_text(**kwargs):
    return CANNED


maintenance.complete_text = fake_complete_text
r = asyncio.run(maintenance.run_consolidate(since_hours=24))

assert r["error"] is None, r["error"]
assert r["reviewed"] == 4, f"reviewed={r['reviewed']}"
# retype 自动执行且留痕生效
assert {"slug": "d-fact", "action": "retype", "mem_type": "project"} == {
    k: r["auto_fixed"][0][k] for k in ("slug", "action", "mem_type")
}, r["auto_fixed"]
assert get_memory("d-fact")["mem_type"] == "project"
# update_desc 自动执行
assert any(a["slug"] == "d-desc" and a["action"] == "update_desc" for a in r["auto_fixed"])
assert get_memory("d-desc")["description"] == "用户每周三晚打球的运动习惯"
# profile 归档提议被拒：无 pending 指向 d-prof
assert all(p["slug"] != "d-prof" for p in r["pending"]), r["pending"]
# archive 落 pending 待审批（不自动执行）
assert len(r["pending"]) == 1 and r["pending"][0]["slug"] == "d-old"
assert get_memory("d-old")["priority"] == "active", "archive 不应自动执行"
# merge 仅建议、未知 slug 忽略
assert len(r["suggestions"]) == 1 and r["suggestions"][0]["slug"] == "d-old"

# 审批后归档生效
from memory_agent.db import approve_pending_action_result
assert approve_pending_action_result(r["pending"][0]["id"]).ok
assert get_memory("d-old")["priority"] == "archived"
# 版本/审计留痕存在（mutation 契约）
hist = list_pending_actions("executed")
assert len(hist) == 1

print("consolidate: retype/update_desc 留痕自愈 + profile 保护 + archive 走审批 + merge 仅建议 — ALL PASS")
