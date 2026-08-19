# -*- coding: utf-8 -*-
"""悦食BI · 门店经营数据看板 — FastAPI 后端

运行: E:/Anacoda/envs/agentLang/python.exe -m uvicorn main:app --port 5000
"""
import hashlib
import re
import secrets
import time
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

import aiomysql
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

# ---------------- 配置 ----------------
DB_CONFIG = dict(
    host="127.0.0.1", port=3306, user="root",
    password="xzh20050928@", db="Demo",
    charset="utf8mb4", autocommit=True,
)
BASE_DIR = Path(__file__).parent
TOKEN_TTL = 24 * 3600          # token 有效期 24h
DEMO_USER = ("admin", "admin123")   # 首次启动自动写入

# ---------------- 会话（内存存储，重启失效） ----------------
SESSIONS: dict[str, dict] = {}  # token -> {username, expires}

pool: Optional[aiomysql.Pool] = None
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


@asynccontextmanager
async def lifespan(app: FastAPI):
    global pool
    pool = await aiomysql.create_pool(minsize=1, maxsize=5, **DB_CONFIG)
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            # 用户表（不存在则创建）
            await cur.execute("""
                CREATE TABLE IF NOT EXISTS users (
                  user_id INT AUTO_INCREMENT PRIMARY KEY,
                  username VARCHAR(50) NOT NULL COMMENT '用户名',
                  password_hash CHAR(64) NOT NULL COMMENT 'SHA-256口令',
                  nickname VARCHAR(50) COMMENT '昵称',
                  role VARCHAR(20) NOT NULL DEFAULT 'viewer' COMMENT '角色: admin/viewer',
                  status TINYINT NOT NULL DEFAULT 1 COMMENT '状态: 1启用 0禁用',
                  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                  updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                  UNIQUE KEY uk_username (username)
                ) ENGINE=InnoDB COMMENT='看板系统用户表'
            """)
            # 旧版表自动迁移（列不存在时补齐，重复列报错直接忽略）
            for ddl in (
                "ALTER TABLE users ADD COLUMN nickname VARCHAR(50) COMMENT '昵称'",
                "ALTER TABLE users ADD COLUMN role VARCHAR(20) NOT NULL DEFAULT 'viewer' "
                "COMMENT '角色: admin/viewer'",
                "ALTER TABLE users ADD COLUMN status TINYINT NOT NULL DEFAULT 1 "
                "COMMENT '状态: 1启用 0禁用'",
                "ALTER TABLE users ADD COLUMN updated_at DATETIME DEFAULT CURRENT_TIMESTAMP "
                "ON UPDATE CURRENT_TIMESTAMP",
            ):
                try:
                    await cur.execute(ddl)
                except Exception:
                    pass  # 列已存在
            # 种子账号：admin（已存在则更新 nickname/role 为管理员）
            await cur.execute(
                "INSERT INTO users (username, password_hash, nickname, role) "
                "VALUES (%s, %s, %s, %s) "
                "ON DUPLICATE KEY UPDATE nickname=VALUES(nickname), role=VALUES(role)",
                (DEMO_USER[0], sha256(DEMO_USER[1]), "管理员", "admin"))
    yield
    pool.close()
    await pool.wait_closed()


app = FastAPI(title="悦食BI", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def get_conn():
    @asynccontextmanager
    async def _ctx():
        async with pool.acquire() as conn:
            yield conn
    return _ctx()


# ---------------- 鉴权 ----------------
async def require_token(request: Request) -> str:
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(401, "未登录")
    token = auth[7:]
    sess = SESSIONS.get(token)
    if not sess or sess["expires"] < time.time():
        SESSIONS.pop(token, None)
        raise HTTPException(401, "登录已过期，请重新登录")
    sess["expires"] = time.time() + TOKEN_TTL
    return sess["username"]


# ---------------- 请求模型 ----------------
USERNAME_RE = re.compile(r"^[a-zA-Z0-9_\u4e00-\u9fa5]{3,20}$")


class LoginBody(BaseModel):
    username: str
    password: str


class RegisterBody(BaseModel):
    username: str
    password: str
    nickname: str = ""
    confirm: str = ""


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatBody(BaseModel):
    messages: list[ChatMessage]


# ---------------- 页面 ----------------
@app.get("/")
async def login_page(request: Request):
    return templates.TemplateResponse(request, "login.html")


@app.get("/dashboard")
async def dashboard_page(request: Request):
    return templates.TemplateResponse(request, "dashboard.html")


# ---------------- 鉴权 API ----------------
@app.post("/api/register")
async def register(body: RegisterBody):
    username = body.username.strip()
    nickname = body.nickname.strip() or username
    if not USERNAME_RE.match(username):
        return JSONResponse(
            {"detail": "用户名需为 3-20 位字母、数字、下划线或中文"}, status_code=400)
    if not (6 <= len(body.password) <= 64):
        return JSONResponse({"detail": "密码长度需为 6-64 位"}, status_code=400)
    if body.confirm and body.confirm != body.password:
        return JSONResponse({"detail": "两次输入的密码不一致"}, status_code=400)

    async with get_conn() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT user_id FROM users WHERE username=%s", (username,))
            if await cur.fetchone():
                return JSONResponse({"detail": "用户名已被注册"}, status_code=409)
            await cur.execute(
                "INSERT INTO users (username, password_hash, nickname, role) "
                "VALUES (%s, %s, %s, 'viewer')",
                (username, sha256(body.password), nickname))
            await cur.execute(
                "SELECT nickname, role FROM users WHERE username=%s", (username,))
            nickname, role = await cur.fetchone()

    # 注册成功直接登录
    token = secrets.token_hex(16)
    SESSIONS[token] = {"username": username, "expires": time.time() + TOKEN_TTL}
    return {"token": token, "username": username, "nickname": nickname, "role": role}


@app.post("/api/login")
async def login(body: LoginBody):
    async with get_conn() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT password_hash, status, nickname FROM users WHERE username=%s",
                (body.username,))
            row = await cur.fetchone()
    if not row or row[0] != sha256(body.password):
        return JSONResponse({"detail": "用户名或密码错误"}, status_code=401)
    if not row[1]:
        return JSONResponse({"detail": "账号已被禁用，请联系管理员"}, status_code=403)
    token = secrets.token_hex(16)
    SESSIONS[token] = {"username": body.username, "expires": time.time() + TOKEN_TTL}
    return {"token": token, "username": body.username, "nickname": row[2] or body.username}


@app.post("/api/logout")
async def logout(request: Request):
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        SESSIONS.pop(auth[7:], None)
    return {"ok": True}


@app.get("/api/me")
async def me(username: str = Depends(require_token)):
    async with get_conn() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT nickname, role FROM users WHERE username=%s", (username,))
            row = await cur.fetchone()
    nickname, role = (row or (username, "viewer"))
    return {"username": username, "nickname": nickname or username, "role": role}


# ---------------- 看板数据 API ----------------
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def parse_range(start: Optional[str], end: Optional[str]) -> tuple[Optional[str], Optional[str]]:
    if start and not DATE_RE.match(start):
        raise HTTPException(400, "start 日期格式应为 YYYY-MM-DD")
    if end and not DATE_RE.match(end):
        raise HTTPException(400, "end 日期格式应为 YYYY-MM-DD")
    return start or None, end or None


async def fetch_one(sql: str, args: tuple) -> tuple:
    async with get_conn() as conn:
        async with conn.cursor() as cur:
            await cur.execute(sql, args)
            return await cur.fetchone()


async def fetch_all(sql: str, args: tuple) -> list:
    async with get_conn() as conn:
        async with conn.cursor() as cur:
            await cur.execute(sql, args)
            return await cur.fetchall()


@app.get("/api/dashboard")
async def dashboard(start: Optional[str] = None, end: Optional[str] = None,
                    _: str = Depends(require_token)):
    start, end = parse_range(start, end)

    min_date, max_date = await fetch_one(
        "SELECT MIN(date), MAX(date) FROM sales", ())
    min_date, max_date = str(min_date), str(max_date)
    start = start or min_date
    end = end or max_date
    if start < min_date:
        start = min_date
    if end > max_date:
        end = max_date
    if start > end:
        raise HTTPException(400, "start 不能晚于 end")

    # 环比对照区间（等长前一区间）
    d_start, d_end = date.fromisoformat(start), date.fromisoformat(end)
    span = (d_end - d_start).days + 1
    prev_end = d_start - timedelta(days=1)
    prev_start = prev_end - timedelta(days=span - 1)
    has_prev = prev_start >= date.fromisoformat(min_date)
    prev_range = (str(prev_start), str(prev_end)) if has_prev else None

    def kpi_row(row):
        revenue, orders, items = float(row[0] or 0), int(row[1] or 0), int(row[2] or 0)
        aov = round(revenue / orders, 2) if orders else 0
        return {"revenue": round(revenue, 2), "orders": orders,
                "items": items, "aov": aov}

    cur_kpi = kpi_row(await fetch_one(
        "SELECT SUM(amount), COUNT(DISTINCT order_id), SUM(qty) FROM sales "
        "WHERE date BETWEEN %s AND %s", (start, end)))

    compare = {}
    if prev_range:
        prev_kpi = kpi_row(await fetch_one(
            "SELECT SUM(amount), COUNT(DISTINCT order_id), SUM(qty) FROM sales "
            "WHERE date BETWEEN %s AND %s", prev_range))
        for key in ("revenue", "orders", "aov", "items"):
            base, now = prev_kpi[key], cur_kpi[key]
            compare[key] = round((now - base) / base * 100, 1) if base else None

    daily_rows = await fetch_all(
        "SELECT DATE_FORMAT(date,'%%Y-%%m-%%d'), SUM(amount), COUNT(DISTINCT order_id) "
        "FROM sales WHERE date BETWEEN %s AND %s GROUP BY date ORDER BY date",
        (start, end))
    daily = []
    for d, rev, orders in daily_rows:
        rev, orders = float(rev or 0), int(orders or 0)
        daily.append({"date": d, "revenue": round(rev, 2), "orders": orders,
                      "aov": round(rev / orders, 2) if orders else 0})

    top_rows = await fetch_all(
        "SELECT product_name, product_category, SUM(qty), SUM(amount) "
        "FROM v_sales_detail WHERE date BETWEEN %s AND %s "
        "GROUP BY product_name, product_category ORDER BY SUM(amount) DESC LIMIT 10",
        (start, end))
    total_rev = cur_kpi["revenue"] or 1
    top_products = [
        {"rank": i + 1, "name": r[0], "category": r[1], "qty": int(r[2] or 0),
         "revenue": round(float(r[3] or 0), 2),
         "share": round(float(r[3] or 0) / total_rev * 100, 1)}
        for i, r in enumerate(top_rows)]

    store_rows = await fetch_all(
        "SELECT store_id, store_name, store_category, district, SUM(amount), "
        "COUNT(DISTINCT order_id) FROM v_sales_detail "
        "WHERE date BETWEEN %s AND %s GROUP BY store_id, store_name, store_category, district "
        "ORDER BY SUM(amount) DESC", (start, end))
    stores = [
        {"store_id": r[0], "name": r[1], "category": r[2], "district": r[3],
         "revenue": round(float(r[4] or 0), 2), "orders": int(r[5] or 0)}
        for r in store_rows]

    payment_rows = await fetch_all(
        "SELECT payment, COUNT(*) FROM sales WHERE date BETWEEN %s AND %s "
        "GROUP BY payment ORDER BY COUNT(*) DESC", (start, end))
    payments = [{"name": r[0], "value": int(r[1])} for r in payment_rows]

    category_rows = await fetch_all(
        "SELECT product_category, SUM(amount) FROM v_sales_detail "
        "WHERE date BETWEEN %s AND %s GROUP BY product_category "
        "ORDER BY SUM(amount) DESC", (start, end))
    categories = [{"name": r[0], "value": round(float(r[1] or 0), 2)} for r in category_rows]

    return {
        "meta": {"min_date": min_date, "max_date": max_date, "start": start, "end": end},
        "kpi": {"revenue": cur_kpi["revenue"], "orders": cur_kpi["orders"],
                "aov": cur_kpi["aov"], "items": cur_kpi["items"], "compare": compare},
        "daily": daily, "top_products": top_products, "stores": stores,
        "payments": payments, "categories": categories,
    }


# ---------------- AI 对话（预留模块） ----------------
async def call_ai(messages: list[ChatMessage]) -> str:
    """AI 钩子：后续接入大模型（如智谱/DeepSeek）只需实现此函数。

    可选进阶：
    - 解析用户意图 -> 调用 /api/dashboard 聚合数据 -> 组装回答
    - 使用 sse-starlette 做流式输出
    - 结合 schema 做自然语言转 SQL（只读账号）
    """
    # TODO: 接入真实大模型
    return ("AI 模块正在开发中，敬请期待。当前您可以问我预留的示例问题，"
            "后续将支持自然语言查数、经营洞察与自动日报。")


@app.post("/api/ai/chat")
async def ai_chat(body: ChatBody, _: str = Depends(require_token)):
    if not body.messages:
        raise HTTPException(400, "messages 不能为空")
    reply = await call_ai(body.messages)
    return {"reply": reply}
