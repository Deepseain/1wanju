# -*- coding: utf-8 -*-
"""从 .env 或环境变量读取配置（极简加载器，无第三方依赖）。"""
from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).parent


def load_dotenv(path: Path | None = None) -> None:
    """加载 .env：每行 KEY=VALUE，# 注释、空行忽略；不覆盖已存在的环境变量。

    「不覆盖」保证系统环境变量（如 DEEPSEEK_API_KEY）优先于 .env。
    值支持两边的引号；用 partition('=') 保留值中的等号。"""
    p = path or (BASE_DIR / ".env")
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key, val = key.strip(), val.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = val


load_dotenv()


def db_config() -> dict:
    """构造 MySQL 连接参数（密码来自环境变量 DB_PASSWORD，不硬编码）。"""
    password = os.environ.get("DB_PASSWORD", "")
    if not password:
        raise RuntimeError(
            "缺少 DB_PASSWORD 环境变量：请复制 app/.env.example 为 app/.env "
            "并填写数据库密码。")
    return dict(
        host=os.environ.get("DB_HOST", "127.0.0.1"),
        port=int(os.environ.get("DB_PORT", "3306")),
        user=os.environ.get("DB_USER", "root"),
        password=password,
        db=os.environ.get("DB_NAME", "Demo"),
        charset="utf8mb4",
        autocommit=True,
    )