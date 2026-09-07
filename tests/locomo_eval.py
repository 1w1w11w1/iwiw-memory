# -*- coding: utf-8 -*-
"""LoCoMo 评测适配器：将 LoCoMo 对话喂给记忆系统，然后评测 QA 召回。

评测流程：
1. 加载 LoCoMo 对话（多个 session，间隔数天）
2. 逐轮喂给记忆系统（提取 → 入库，模拟真实使用）
3. 对话结束后，逐个 QA：
   - search_memories(question) → 检索相关记忆
   - 将记忆拼入 prompt → LLM 回答
   - 与 ground truth 对比（LLM-as-Judge）
4. 输出：per-category accuracy + overall score

运行：python tests/locomo_eval.py [--conversation N] [--max-sessions M]
"""

from __future__ import annotations

import argparse
import asyncio
import httpx
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import memory_agent.db as memory_db
from memory_agent.extractor import extract_and_save
from memory_agent.retrieval import search_memories
from memory_agent.query_builder import extract_topic_grams
from memory_agent.session_state import SessionState
from memory_agent.llm import complete_text
from memory_agent.config import LLM_MODEL

# ── LoCoMo 数据加载 ──

def load_conversations(path: str) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def get_sessions(conversation: dict) -> list[tuple[int, list[dict]]]:
    """按序号提取所有 session 的 turns。"""
    sessions = []
    conv = conversation
    session_keys = [k for k in conv.keys() if k.startswith("session_")]
    for key in sorted(session_keys, key=lambda k: int(k.split("_")[1])):
        if key.startswith("session_") and not key.endswith("date_time"):
            num = int(key.split("_")[1])
            sessions.append((num, conv[key]))
    return sessions


# ── 记忆管线 ──

def process_turn(msg: str, session_state: SessionState, db_path: str) -> dict:
    """处理一轮用户消息：提取 + 入库。返回提取结果。"""
    from memory_agent.extractor import extract_and_save
    saved = {}
    try:
        import asyncio
        loop = asyncio.new_event_loop()
        saved_list = loop.run_until_complete(extract_and_save(msg))
        loop.close()
        saved = {"saved": saved_list}
    except Exception as exc:
        saved = {"error": str(exc)}
    return saved


async def answer_question_async(
    question: str,
    top_k: int = 5,
    speaker: str = "",
) -> str:
    """用记忆系统回答一个问题。"""
    from memory_agent.retrieval import search_memories
    from memory_agent.llm import complete_text
    
    results = search_memories(question, top_k=top_k)
    
    if not results:
        return "我没有找到相关信息。"
    
    # 组装记忆上下文
    mem_lines = []
    for r in results:
        rdate = r.get("recorded_date") or r.get("metadata", {}).get("recorded_date", "")
        date_tag = f" (recorded: {rdate})" if rdate else ""
        mem_lines.append(f"- {r['slug']}{date_tag}: {r.get('content', '')[:200]}")
    memory_block = "\n".join(mem_lines)
    
    qa_prompt = f"""Answer the question based on the following memories. If the memories don't contain enough information, honestly say "I cannot answer based on my memories."

## Relevant memories
{memory_block}

## Question
{question}

Answer based on the memories (be honest if there's not enough information):"""
    
    from memory_agent.llm import complete_text
    answer = await complete_text(
        system_prompt="You are an assistant answering from long-term memory. Answer based only on the provided memories. If the memories don't contain enough information, honestly say you cannot answer.",
        user_prompt=qa_prompt,
        max_tokens=300,
        temperature=0.1,
        timeout=60.0,
    )
    return answer.strip()


# ── LLM-as-Judge ──

JUDGE_PROMPT = """Judge whether the answer correctly answers the question.

Ground truth: {ground_truth}
Question: {question}
System answer: {answer}

Rules:
- If the answer matches the ground truth on core facts → "correct"
- If the answer contains correct information but is incomplete → "partial"
- If the answer contradicts the ground truth or says it doesn't know → "incorrect"

Return one word: correct / partial / incorrect"""


async def judge_answer_async(question: str, ground_truth: str, answer: str) -> str:
    """LLM-as-Judge 评分。"""
    from memory_agent.llm import complete_text
    prompt = JUDGE_PROMPT.format(ground_truth=ground_truth, question=question, answer=answer)
    result = await complete_text(
        system_prompt="You are a grader. Return only one word: correct/partial/incorrect.",
        user_prompt=prompt,
        max_tokens=10,
        temperature=0.0,
        timeout=30.0,
    )
    return result.strip().lower()


# ── 带重试的 LLM 调用包装（瞬时网络错误重试 2 次）──
async def llm_with_retry(coro_factory, retries=2):
    for attempt in range(retries + 1):
        try:
            return await coro_factory()
        except (httpx.ConnectTimeout, httpx.ConnectError, httpx.ReadTimeout) as e:
            if attempt < retries:
                await asyncio.sleep(3 * (attempt + 1))
                continue
            raise


# ── 主评测流程 ──

def run_evaluation(
    data_path: str,
    conversation_idx: int = 0,
    max_sessions: int = None,
    db_path: str = None,
    keep_db_path: str = None,
) -> dict:
    """对一段 LoCoMo 对话运行完整评测。"""
    
    conversations = load_conversations(data_path)
    sample = conversations[conversation_idx]
    conv = sample["conversation"]
    qa_pairs = sample.get("qa", [])
    sessions = get_sessions(conv)
    
    if max_sessions:
        sessions = sessions[:max_sessions]
    
    # 提取 speaker 名
    speaker_a = conv.get("speaker_a", "SpeakerA")
    speaker_b = conv.get("speaker_b", "SpeakerB")
    
    # 设置隔离库
    tmp = None
    if db_path is None:
        import tempfile
        if keep_db_path:
            db_path = keep_db_path
        else:
            tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
            db_path = str(Path(tmp.name) / "memory.db")
    
    import memory_agent.db as memory_db
    memory_db.close()
    memory_db.MEMORY_DB_PATH = Path(db_path)
    memory_db.connect()
    
    # 初始化会话状态
    session_state = SessionState()
    session_state.build_word_index(memory_db.list_memories())
    
    # Phase 1: 批量提取（每 BATCH_SIZE 轮合并一次 LLM 调用，A/B 实验验证质量不降）
    BATCH_SIZE = 8
    turn_count = 0
    extraction_count = 0
    errors = []
    
    async def run_extraction():
        nonlocal extraction_count, turn_count
        buffer = []
        for sess_num, turns in sessions:
            # session 日期（LoCoMo 的 session_N_date_time）
            sess_date = conv.get(f"session_{sess_num}_date_time", "")
            sess_date = str(sess_date)[:10] if sess_date else ""
            for turn in turns:
                speaker = turn.get("speaker", "")
                text = turn.get("text", "").strip()
                if not text:
                    continue
                turn_count += 1
                buffer.append(text)
                # SessionState 每轮更新（确定性，免费）
                grams = extract_topic_grams(text)
                session_state.update(text, grams)
                if len(buffer) >= BATCH_SIZE:
                    merged = chr(10).join(f"[消息{j+1}]: {m}" for j, m in enumerate(buffer))
                    try:
                        saved = await llm_with_retry(lambda: extract_and_save(merged, session_date=sess_date))
                        if saved:
                            extraction_count += len(saved)
                    except Exception as exc:
                        errors.append(f"batch extraction error: {exc}")
                    buffer = []
        # flush 剩余
        if buffer:
            merged = chr(10).join(f"[消息{j+1}]: {m}" for j, m in enumerate(buffer))
            try:
                saved = await extract_and_save(merged, session_date=sess_date)
                if saved:
                    extraction_count += len(saved)
            except Exception as exc:
                errors.append(f"final extraction error: {exc}")
    
    loop = asyncio.new_event_loop()
    loop.run_until_complete(run_extraction())
    
    # QA 过滤：只评测 evidence 落在已处理 session 内的 QA（公平度量）
    max_sess_num = max(sn for sn, _ in sessions)
    def in_scope(qa):
        for ev in qa.get("evidence", []):
            # dialog id 格式: "D1:3" → session 1
            try:
                sess = int(ev.split(":")[0][1:])
                if sess <= max_sess_num:
                    return True
            except (ValueError, IndexError):
                pass
        return False
    scoped_qa = [qa for qa in qa_pairs if in_scope(qa)]
    print(f"[LoCoMo] QA scope: {len(scoped_qa)}/{len(qa_pairs)} (evidence in sessions 1-{max_sess_num})")
    qa_pairs = scoped_qa
    
    # Phase 2: QA 并发评测（8 路并发 + 智能 judge 跳过）
    sem = asyncio.Semaphore(4)
    judge_calls = [0]
    
    def fast_judge(answer: str, ground_truth: str, category: int):
        """确定性 judge：能确定的直接判，不能确定返回 None（需 LLM）。"""
        ans_lower = answer.lower()
        no_info = any(k in ans_lower for k in ["没有找到", "无法回答", "不知道", "not mentioned", "no information", "cannot answer", "didn't mention", "not specified"])
        if category == 5:  # adversarial
            return "correct" if no_info else "incorrect"
        if no_info:
            return "incorrect"
        gt_lower = ground_truth.lower()
        if gt_lower and gt_lower in ans_lower:
            return "correct"
        return None  # 需要 LLM judge
    
    async def eval_one_qa(qa):
        async with sem:
            question = qa.get("question", "")
            ground_truth = str(qa.get("answer", ""))
            category = qa.get("category", 0)
            try:
                hits = search_memories(question, top_k=5)
                hit_slugs = [h["slug"] for h in hits]
                answer = await llm_with_retry(lambda: answer_question_async(question, speaker=speaker_a))
                judgment = fast_judge(answer, ground_truth, category)
                if judgment is None:
                    judge_calls[0] += 1
                    judgment = await llm_with_retry(lambda: judge_answer_async(question, ground_truth, answer))
            except Exception as exc:
                return {
                    "question": question[:60],
                    "answer": f"[error] {type(exc).__name__}",
                    "ground_truth": ground_truth[:60],
                    "category": category,
                    "judgment": "incorrect",
                    "evidence": qa.get("evidence", []),
                    "hit_slugs": [],
                }
            return {
                "question": question[:60],
                "answer": answer[:100],
                "ground_truth": ground_truth[:60],
                "category": category,
                "judgment": judgment,
                "evidence": qa.get("evidence", []),
                "hit_slugs": hit_slugs,
            }
    
    async def run_qa():
        return await asyncio.gather(*[eval_one_qa(qa) for qa in qa_pairs])
    
    results = loop.run_until_complete(run_qa())
    
    # 统计
    total = len(results)
    correct = sum(1 for r in results if r["judgment"] == "correct")
    partial = sum(1 for r in results if r["judgment"] == "partial")
    incorrect = sum(1 for r in results if r["judgment"] == "incorrect")
    
    # 按类别统计
    by_cat = {}
    for r in results:
        cat = r["category"]
        by_cat.setdefault(cat, {"total": 0, "correct": 0})
        by_cat[cat]["total"] += 1
        if r["judgment"] == "correct":
            by_cat[cat]["correct"] += 1
    
    memory_db.close()
    
    report = {
        "conversation_idx": conversation_idx,
        "total_turns": turn_count,
        "total_qa": total,
        "correct": correct,
        "partial": partial,
        "incorrect": incorrect,
        "accuracy": round(correct / total, 3) if total else 0,
        "accuracy_with_partial": round((correct + partial * 0.5) / total, 3) if total else 0,
        "by_category": by_cat,
        "errors": errors[:5],
        "details": results,
    }
    return report


def main():
    parser = argparse.ArgumentParser(description="LoCoMo evaluation for memory system")
    parser.add_argument("--conversation", type=int, default=0, help="conversation index (0-9)")
    parser.add_argument("--max-sessions", type=int, default=None)
    parser.add_argument("--data", type=str, default=str(ROOT / "data" / "locomo" / "locomo10.json"))
    parser.add_argument("--output", type=str, default=None)
    parser.add_argument("--keep-db", type=str, default=None, help="保留记忆库到指定路径（供归因诊断）")
    parser.add_argument("--max-qa", type=int, default=None, help="限制 QA 数量（调试用）")
    args = parser.parse_args()
    
    data_path = args.data
    if not Path(data_path).exists():
        print(f"数据文件不存在: {data_path}")
        return 1
    
    report = run_evaluation(data_path, args.conversation, args.max_sessions, keep_db_path=args.keep_db)
    
    output = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        Path(args.output).write_text(output, encoding="utf-8")
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
