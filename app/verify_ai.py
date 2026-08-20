# -*- coding: utf-8 -*-
"""AI 模块冒烟脚本：直连 Demo 库，验证只读护栏 + schema 缓存 + 三类典型问答。

运行（需 MySQL 已启动；验证 LLM 问答需已设置 DEEPSEEK_API_KEY）：
    E:/Anacoda/envs/agentLang/python.exe verify_ai.py
"""
from __future__ import annotations

import asyncio
import json
import os

import aiomysql

from config import db_config
from ai_agent.schema_cache import SchemaCache
from ai_agent.tools import build_tools, guard_sql
from ai_agent.agent import build_agent
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

QUESTIONS = [
    "上周哪家门店营业额最高？",
    "数据库里有没有『新加坡』门店的数据？",
    "帮我查一下员工的工资是多少？",
]


async def main() -> None:
    print("== 1. 只读护栏 ==")
    for bad in [
        "DROP TABLE sales",
        "UPDATE sales SET qty=0",
        "DELETE FROM sales WHERE 1=1",
        "SELECT * FROM sales; DROP TABLE sales",
        "SELECT 1 INTO OUTFILE '/tmp/x'",
        "INSERT INTO stores VALUES ('S99','x','x','x')",
        "SELECT password_hash FROM users",
        "SELECT * FROM sales_raw",
        "SELECT * FROM sales_rejects",
    ]:
        ok, r = guard_sql(bad)
        assert not ok, f"护栏漏了：{bad}"
        print(f"  拒绝 ✓  {bad!r}")
    ok, r = guard_sql("SELECT SUM(amount) FROM sales WHERE date BETWEEN '2026-07-01' AND '2026-07-31'")
    assert ok, f"正常查询被误杀：{r}"
    print(f"  放行 ✓  {r[-40:]}")

    pool = await aiomysql.create_pool(minsize=1, maxsize=3, **db_config())
    try:
        cache = SchemaCache()
        await cache.load(pool)

        print("\n== 2. schema 缓存 ==")
        data = cache.get()
        print("  日期范围:", json.dumps(data["date_range"], ensure_ascii=False))
        print("  维度项:", list(data["dimensions"].keys()))
        print("  stores.store_name:", [v for v, _ in data["dimensions"].get("stores.store_name", [])])
        print("  tables:", list(data["tables"].keys()))

        tools = build_tools(pool, cache)
        get_schema, query_data = tools

        print("\n== 3. query_data 工具直测（真实查库）==")
        res = await query_data.ainvoke({
            "sql": "SELECT store_name, SUM(amount) AS revenue "
                   "FROM v_sales_detail WHERE date BETWEEN '2026-07-01' AND '2026-07-31' "
                   "GROUP BY store_name ORDER BY revenue DESC"})
        assert res["ok"], res
        print("  ok=True, source.tables =", res["source"]["tables"], "row_count =", res["row_count"])
        print("  rows[:3] =", json.dumps(res["rows"][:3], ensure_ascii=False))

        # 无数据兜底
        res = await query_data.ainvoke({"sql": "SELECT * FROM v_sales_detail WHERE store_name = '不存在门店'"})
        assert res["ok"] and res["row_count"] == 0, res
        print("  空结果 note =", res["note"])

        if not os.environ.get("DEEPSEEK_API_KEY"):
            print("\n[skip] 未设置 DEEPSEEK_API_KEY，跳过 LLM 问答（护栏/schema/工具已验证通过）")
            return

        print("\n== 4. LLM 端到端问答 ==")
        os.makedirs(os.path.join(BASE_DIR, "data"), exist_ok=True)
        async with AsyncSqliteSaver.from_conn_string(
            os.path.join(BASE_DIR, "data", "ai_verify.sqlite")
        ) as ckpt:
            agent = build_agent(tools, ckpt)
            for i, q in enumerate(QUESTIONS):
                print(f"\n----- Q{i + 1}: {q}")
                cfg = {"configurable": {"thread_id": f"verify:{i}"}, "recursion_limit": 25}
                result = await agent.ainvoke({"messages": [("user", q)]}, config=cfg)
                print(result["messages"][-1].content)
    finally:
        pool.close()
        await pool.wait_closed()


if __name__ == "__main__":
    asyncio.run(main())