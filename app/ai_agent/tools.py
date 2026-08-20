# -*- coding: utf-8 -*-
"""Text2SQL 工具：query_data（只读护栏 + 执行 + 来源标记）与 get_schema（数据字典）。"""
from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal

from langchain_core.tools import tool

from .schema_cache import BUSINESS_TABLES

MAX_ROWS = 1000

# AI 助手无权读取的表（users 含口令哈希；留痕表是脏数据）
DENIED_TABLES = ["users", "sales_raw", "sales_rejects"]

_FORBIDDEN_WORDS = (
    "INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "TRUNCATE",
    "CREATE", "RENAME", "GRANT", "REVOKE", "LOAD", "REPLACE", "MERGE",
)

_FORBIDDEN_CLAUSES = ("INTO OUTFILE", "FOR UPDATE", "LOCK IN SHARE MODE")


def _strip_strings(s: str) -> str:
    """把引号内的字面量替换成空格，避免把字符串内容误判为关键字/分号。"""
    out, i, n = [], 0, len(s)
    in_str = None
    while i < n:
        c = s[i]
        if in_str:
            if c == in_str:
                in_str = None
            elif c == "\\" and i + 1 < n:
                i += 1
            out.append(" ")
        else:
            if c in ("'", '"', "`"):
                in_str = c
                out.append(" ")
            else:
                out.append(c)
        i += 1
    return "".join(out)


def guard_sql(sql: str):
    """校验 SQL 只读。返回 (ok: bool, 结果)。ok=True 时结果为加固后的 SQL，否则为错误说明。"""
    s = sql.strip().rstrip(";").strip()
    # 去注释（块注释/行注释）
    clean = re.sub(r"/\*.*?\*/", " ", s, flags=re.S)
    clean = re.sub(r"--[^\n]*", " ", clean)
    clean = re.sub(r"#[^\n]*", " ", clean)
    bare = _strip_strings(clean)
    bare_upper = bare.upper()

    m = re.match(r"^\s*([A-Za-z]+)", bare)
    if not m:
        return False, "无法识别该 SQL"
    kw = m.group(1).upper()
    if kw not in ("SELECT", "WITH", "SHOW", "DESCRIBE", "DESC", "EXPLAIN"):
        return False, f"仅允许只读查询(SELECT/SHOW/DESCRIBE)，收到关键字 {kw}"

    for word in _FORBIDDEN_WORDS:
        if re.search(r"\b" + word + r"\b", bare_upper):
            return False, f"检测到被禁止的写操作关键字 {word}，仅允许只读查询"
    for clause in _FORBIDDEN_CLAUSES:
        if clause in bare_upper:
            return False, f"检测到被禁止的子句 {clause.strip()}"
    for t in DENIED_TABLES:
        if re.search(r"\b" + t.upper() + r"\b", bare_upper):
            return False, f"无权查询表 {t}（AI 助手仅可读取经营数据）"
    if ";" in bare:
        return False, "不支持多语句，仅允许单条查询"

    # 只读 SELECT/WITH 未显式 LIMIT 时补上，防止一次拉爆
    if kw in ("SELECT", "WITH") and "LIMIT" not in bare_upper and "UNION" not in bare_upper:
        s = s + " LIMIT " + str(MAX_ROWS)
    return True, s


def _extract_tables(sql: str) -> list[str]:
    """从 SQL 里提取被引用的业务表/视图名，作为数据来源标记。"""
    upper = sql.upper()
    return [t for t in BUSINESS_TABLES if re.search(r"\b" + t.upper() + r"\b", upper)]


def _jsonable(v):
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    return v


CHART_TYPES = ("bar", "line", "pie", "area")


async def _execute(pool, guarded: str):
    """执行已过护栏的只读 SQL，返回 (columns, rows, truncated)。执行错误向外抛。"""
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(guarded)
            cols = [d[0] for d in cur.description] if cur.description else []
            raw = await cur.fetchmany(MAX_ROWS + 1)
    truncated = len(raw) > MAX_ROWS
    rows = [[_jsonable(v) for v in row] for row in raw[:MAX_ROWS]]
    return cols, rows, truncated


def build_chart_spec(chart_type: str, title: str, x: str, y, columns, rows) -> dict:
    """把 SQL 结果行转成前端可渲染的图表规格 {chart_type, title, labels, series}。"""
    spec = {"chart_type": chart_type, "title": title, "labels": [], "series": []}
    if not columns or not rows:
        return spec
    x_idx = next((i for i, c in enumerate(columns) if str(c).lower() == (x or "").lower()), None)
    labels = ([str(row[x_idx]) for row in rows]
              if x_idx is not None else [str(i + 1) for i in range(len(rows))])
    spec["labels"] = labels
    for metric in (y or []):
        idx = next((i for i, c in enumerate(columns) if str(c).lower() == str(metric).lower()), None)
        if idx is None:
            continue
        values = []
        for row in rows:
            v = row[idx]
            try:
                values.append(float(v))
            except (TypeError, ValueError):
                values.append(None)
        spec["series"].append({"name": str(metric), "values": values})
    return spec


def build_tools(pool, schema_cache):
    """构造进程内工具列表（闭包持有连接池与 schema 缓存）。"""

    @tool
    async def get_schema() -> dict:
        """返回数据库的表结构、字段名/类型/注释、关键维度可用值枚举和销售日期范围。

        写 SQL 之前先调用本工具，核对表名、字段名、以及门店名/商品名/品类/区域/
        支付方式等文字字段的精确取值，避免拼错导致查不到或查错。
        """
        return schema_cache.get()

    @tool
    async def query_data(sql: str) -> dict:
        """在只读约束下执行一条 SQL 查询并返回结果与数据来源。

        只能执行 SELECT/SHOW/DESCRIBE 等只读语句；任何写操作都会被拒绝。
        先调 get_schema 核对表结构与取值，再写精确的 SELECT 传入本工具。
        返回结构：{ok, columns, rows, row_count, source:{tables, sql}, note}。
        - row_count==0 或 note=="查询结果为空" 表示数据库里没有匹配数据；
        - ok==false 表示语句被拒或执行报错，需按 error 说明修正后重试。
        回答用户时必须以 source.tables 作为数据来源标注。
        """
        ok, guarded = guard_sql(sql)
        if not ok:
            return {"ok": False, "error": guarded}
        try:
            cols, rows, truncated = await _execute(pool, guarded)
        except Exception as e:  # noqa: BLE001 —— 把错误回给模型，让它修正重试
            return {"ok": False, "error": f"SQL 执行失败: {type(e).__name__}: {e}"}

        tables = _extract_tables(_strip_strings(guarded))
        if not rows:
            note = "查询结果为空"
        elif truncated:
            note = f"结果已截断，仅返回前 {MAX_ROWS} 行"
        else:
            note = None
        return {
            "ok": True,
            "columns": cols,
            "rows": rows,
            "row_count": len(rows),
            "source": {"tables": tables, "sql": guarded},
            "note": note,
        }

    @tool
    async def render_chart(sql: str, chart_type: str, title: str, x: str, y: list[str]) -> dict:
        """根据一条只读 SQL 的查询结果生成一张交互式图表（回答含图表诉求时调用）。

        图表作为文字回答的补充；每个数字都由本工具真实查询产生，禁止凭空捏造。
        sql: 只读 SELECT（约束同 query_data）
        chart_type: 'bar'(排行/比较) / 'line'(趋势) / 'pie'(构成占比) / 'area'(面积趋势)
        title: 图标题，写结论或问题，不写「数据分析图」这类空标题
        x: 横轴/类目字段名，必须与 SELECT 输出列名或别名一致（如 store_name、date）
        y: 指标字段名列表，必须与 SELECT 输出列名或别名一致（如 ['revenue']）
        """
        ok, guarded = guard_sql(sql)
        if not ok:
            return {"ok": False, "error": guarded}
        if chart_type not in CHART_TYPES:
            return {"ok": False, "error": f"chart_type 仅支持 {list(CHART_TYPES)}"}
        try:
            cols, rows, truncated = await _execute(pool, guarded)
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": f"SQL 执行失败: {type(e).__name__}: {e}"}

        tables = _extract_tables(_strip_strings(guarded))
        if not rows:
            note = "查询结果为空"
        elif truncated:
            note = f"结果已截断，仅返回前 {MAX_ROWS} 行"
        else:
            note = None
        return {
            "ok": True,
            "chart": build_chart_spec(chart_type, title, x, y, cols, rows),
            "columns": cols,
            "rows": rows,
            "row_count": len(rows),
            "source": {"tables": tables, "sql": guarded},
            "note": note,
        }

    return [get_schema, query_data, render_chart]