# -*- coding: utf-8 -*-
"""启动时读取 Demo 库表结构 + 维度枚举值，缓存供 get_schema 工具使用。"""
from __future__ import annotations

TABLE_COMMENTS = {
    "stores": "门店维表",
    "products": "商品维表",
    "sales": "销售流水(清洗后)",
    "sales_raw": "销售流水原始留档(未清洗)",
    "sales_rejects": "清洗剔除记录(留痕)",
    "v_sales_detail": "销售明细视图(stores+products+sales 三表JOIN)",
    "users": "系统用户表",
}

# AI 助手仅暴露经营数据表（users 含口令哈希、留痕表是脏数据，都不给模型看）
BUSINESS_TABLES = ("v_sales_detail", "sales", "stores", "products")

# 需要枚举去重值的维度列 (表, 列)
DIMENSION_COLUMNS = [
    ("stores", "store_name"),
    ("stores", "category"),
    ("stores", "district"),
    ("products", "product_name"),
    ("products", "product_category"),
    ("sales", "payment"),
]


class SchemaCache:
    """缓存表结构(列名/类型/注释/行数) + 关键维度去重值 + 日期范围。"""

    def __init__(self):
        self._data: dict = {}

    async def load(self, pool) -> None:
        tables: dict = {}

        # 1) 表/列结构 + 行数
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT TABLE_NAME, COLUMN_NAME, COLUMN_TYPE, COLUMN_COMMENT "
                    "FROM information_schema.COLUMNS "
                    "WHERE TABLE_SCHEMA='Demo' "
                    "ORDER BY TABLE_NAME, ORDINAL_POSITION")
                for tname, cname, ctype, ccomment in await cur.fetchall():
                    meta = tables.setdefault(
                        tname, {"comment": None, "columns": [], "row_count": None})
                    meta["columns"].append({
                        "name": cname,
                        "type": ctype,
                        "comment": (ccomment or "").strip() or None,
                    })
                for tname in list(tables):
                    try:
                        await cur.execute(f"SELECT COUNT(*) FROM `{tname}`")
                        (cnt,) = await cur.fetchone()
                        tables[tname]["row_count"] = int(cnt or 0)
                    except Exception:
                        tables[tname]["row_count"] = None

        for tname, meta in tables.items():
            meta["comment"] = TABLE_COMMENTS.get(tname, "")

        # 2) 维度去重值（按出现频次降序，封顶 100）
        dimensions: dict = {}
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                for tname, col in DIMENSION_COLUMNS:
                    try:
                        await cur.execute(
                            f"SELECT `{col}`, COUNT(*) FROM `{tname}` "
                            f"WHERE `{col}` IS NOT NULL AND `{col}` <> '' "
                            f"GROUP BY `{col}` ORDER BY COUNT(*) DESC LIMIT 100")
                        vals = [(str(v), int(c)) for v, c in await cur.fetchall()]
                        if vals:
                            dimensions[f"{tname}.{col}"] = vals
                    except Exception:
                        pass

        # 3) 销售日期范围
        date_range: dict = {}
        try:
            async with pool.acquire() as conn:
                async with conn.cursor() as cur:
                    await cur.execute("SELECT MIN(date), MAX(date) FROM sales")
                    mn, mx = await cur.fetchone()
                    date_range = {"min": str(mn), "max": str(mx)}
        except Exception:
            date_range = {}

        # 4) 表关系
        relationships = (
            "sales.store_id -> stores.store_id；"
            "sales.product_id -> products.product_id；"
            "v_sales_detail = sales JOIN stores JOIN products 的明细视图。"
        )

        self._data = {
            "tables": tables,
            "dimensions": dimensions,
            "date_range": date_range,
            "relationships": relationships,
        }

    def get(self) -> dict:
        data = dict(self._data)
        # 只暴露经营数据表给模型
        data["tables"] = {
            k: v for k, v in self._data["tables"].items()
            if k in BUSINESS_TABLES
        }
        return data