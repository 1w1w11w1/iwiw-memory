# -*- coding: utf-8 -*-
"""chat 体验 E2E 评估 —— 以真实 chat 管道为准的体验回归套件。

设计：
- 驱动：subprocess 管道驱动真实 python -m memory_agent.chat（与真实用户体验一致）
- 隔离：每个场景独立临时记忆库（MEMORY_AGENT_DB_PATH 覆盖）
- 判定两级：
  * 硬判定 —— 只用 stdout 确定性锚点（[提取]/[联想注入]/记忆总数/命令输出/耗时），
    不赌 LLM 自由文本
  * 软审阅 —— 每场景输出完整对话，供人工审阅回复质量
- LLM 真实调用（.env 配置）；场景总量控制以控制成本

运行：python tests/e2e_chat_eval.py
输出：JSON 报告 + 人读摘要；退出码 0 = 全部硬判定通过
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

CHAT_TIMEOUT = 300


def run_chat(turns, db_path, extra_env=None):
    """驱动一次真实 chat 会话，返回 stdout/stderr/耗时。"""
    env = dict(os.environ, PYTHONIOENCODING='utf-8', MEMORY_AGENT_DB_PATH=db_path, MEMORY_AGENT_ECHO_STATE='1')
    if extra_env:
        env.update(extra_env)
    inp = chr(10).join([*turns, ''])
    t0 = time.perf_counter()
    p = subprocess.run(
        [sys.executable, '-m', 'memory_agent.chat'],
        input=inp, capture_output=True, text=True,
        timeout=CHAT_TIMEOUT, cwd=str(ROOT), env=env,
        encoding='utf-8', errors='replace',
    )
    wall = time.perf_counter() - t0
    return {'stdout': p.stdout, 'stderr': p.stderr, 'exit': p.returncode, 'wall': wall}


def db_count(db_path):
    import sqlite3
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute('SELECT COUNT(*) FROM memories').fetchone()[0]
    finally:
        conn.close()


def injected_per_turn(stdout):
    """从 [联想注入] 锚点行提取每轮注入的 slug 列表。"""
    out = []
    for line in stdout.splitlines():
        if '[联想注入]' in line:
            body = line.split('[联想注入]', 1)[1].strip()
            out.append(re.findall(r'[A-Za-z0-9_-]+', body))
    return out


def stats_counts(stdout):
    return [int(l.split('记忆总数:')[1].strip()) for l in stdout.splitlines() if '记忆总数' in l]


# ── 场景 ──

def sc_trigger_coverage():
    """E2E-01 工具写入闭环：自然事实句由模型自主调用记忆工具写入（选择性记忆，至少一条），
    且随后的相关提问能让写入的记忆回流注入层（[standing 注入] 或 [联想注入]，A2 一致性）。"""
    facts = [
        '我对芒果过敏',
        '我把烟戒了',
        '我妈让我周末回家吃饭',
    ]
    turns = []
    for f in facts:
        turns.append(f)
        turns.append('/stats')
    # 隔离库内只有本次写入的记忆：相关提问出现任何注入行 = 写入已回流
    turns.append('那我平时能吃芒果吗？')
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        db = str(Path(tmp) / 'memory.db')
        out = run_chat(turns, db)['stdout']
        counts = stats_counts(out)
        checks = []
        for i, f in enumerate(facts):
            got = counts[i] if i < len(counts) else -1
            checks.append({'fact': f, 'count_after': got, 'written': got > 0})
        tool_used = '[记忆工具]' in out
        written = bool(counts) and counts[-1] >= 1
        # 注入 echo 与 "你> " 提示符同行交错（CLI 时序），必须子串匹配而非行首匹配
        inj_lines = [l for l in out.splitlines() if '[standing 注入]' in l or '[联想注入]' in l]
        injected_back = len(inj_lines) >= 1
        return {
            'name': 'trigger_coverage',
            'ok': tool_used and written and injected_back,
            'tool_used': tool_used,
            'written': written,
            'injected_back': injected_back,
            'inj_lines': inj_lines[:3],
            'checks': checks,
            'stdout_tail': out[-1200:],
        }


def sc_inject_relevance():
    """E2E-02 联想注入相关性：库内已有记忆，相关问句应注入对应记忆。"""
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        db = str(Path(tmp) / 'memory.db')
        # 先建 schema（chat 子进程的 connect 只在库里建表；播种前须初始化）
        import memory_agent.db as memory_db
        memory_db.close()
        memory_db.MEMORY_DB_PATH = Path(db)
        memory_db.connect()
        memory_db.close()
        import sqlite3
        from datetime import datetime
        conn = sqlite3.connect(db)
        now = datetime.now().isoformat(timespec='seconds')
        conn.execute(
            'INSERT INTO memories (id, slug, description, content, mem_type, priority, recorded_date, content_hash, created_at, updated_at, metadata) VALUES (?,?,?,?,?,?,?,?,?,?,?)',
            ('seed-1', 'user-piano-lesson', '学钢琴', '用户每周三晚上七点上钢琴课，老师姓陈。', 'fact', 'active', now, 'x', now, now, '{}'),
        )
        conn.commit()
        conn.close()
        out = run_chat(['我那门兴趣课的老师说我该多练练视唱练耳，你觉得呢？'], db)['stdout']
        inj = injected_per_turn(out)
        hit = any('user-piano-lesson' in slugs for slugs in inj)
        return {'name': 'inject_relevance', 'ok': hit, 'injected': inj, 'stdout_tail': out[-1000:]}


def sc_commands():
    """E2E-05 管理命令冒烟：help/stats/pending 生命周期正常。"""
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        db = str(Path(tmp) / 'memory.db')
        out = run_chat(['/help', '/stats', '/pending', '/quit'], db)['stdout']
        checks = {
            'help_ok': 'CLI 工作台' in out,
            'stats_ok': '记忆总数' in out,
            'pending_ok': ('待确认动作' in out) or ('无待确认' in out),
        }
        return {'name': 'commands', 'ok': all(checks.values()), 'checks': checks, 'stdout_tail': out[-800:]}


def sc_llm_failure():
    """E2E-06 LLM 失败韧性：坏 key 下进程不崩溃、给出失败提示并正常退出。"""
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        db = str(Path(tmp) / 'memory.db')
        r = run_chat(
            ['你好', '/quit'],
            db,
            extra_env={'MEMORY_AGENT_LLM_API_KEY': 'sk-invalid-key-for-e2e'},
        )
        out = r['stdout']
        ok = ('回复失败' in out or 'LLM 配置错误' in out) and r['exit'] == 0
        return {'name': 'llm_failure', 'ok': ok, 'exit': r['exit'], 'stdout_tail': out[-600:]}


SCENARIOS = [
    sc_trigger_coverage,
    sc_inject_relevance,
    sc_commands,
    sc_llm_failure,
]


def main():
    only = sys.argv[1:]  # 可选：命令行传入场景名过滤
    print('=' * 60)
    print(' chat 体验 E2E 评估')
    print('=' * 60, flush=True)
    results = []
    for sc in SCENARIOS:
        if only and sc.__name__ not in only and sc.__name__.replace('sc_', '') not in only:
            continue
        print('>>> 运行 ' + sc.__name__ + ' ...', flush=True)
        try:
            r = sc()
        except Exception as exc:
            r = {'name': sc.__name__, 'ok': False, 'error': type(exc).__name__ + ': ' + str(exc)}
        results.append(r)
        print('    ' + ('PASS' if r.get('ok') else 'FAIL') + ' ' + r['name'], flush=True)

    print()
    print('=' * 60)
    print(' 评分摘要')
    print('=' * 60)
    passed = sum(1 for r in results if r.get('ok'))
    for r in results:
        print('  ' + ('PASS' if r.get('ok') else 'FAIL') + '  ' + r['name'])
        for k in ('covered', 'total', 'injected', 'extracted', 'memories', 'per_turn_hi', 'per_turn_lo', 'delta_per_turn', 'tool_used', 'written', 'injected_back', 'inj_lines', 'checks', 'exit', 'error'):
            if k in r:
                print('        ' + k + ': ' + str(r[k]))
    print()
    print('E2E 基线: ' + str(passed) + '/' + str(len(results)) + ' 通过')
    failed = [r['name'] for r in results if not r.get('ok')]
    print('失败场景（优化目标）: ' + (', '.join(failed) if failed else '无'))
    return 0 if passed == len(results) else 1


if __name__ == '__main__':
    raise SystemExit(main())
