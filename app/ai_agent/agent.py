# -*- coding: utf-8 -*-
"""构建 LangGraph ReAct agent（Text2SQL）。"""
from __future__ import annotations

import os

from langchain_deepseek import ChatDeepSeek
from langgraph.prebuilt import create_react_agent

from .prompts import SYSTEM_PROMPT

DEEPSEEK_MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-flash")
RECURSION_LIMIT = 25  # 防工具调用死循环


def build_agent(tools, checkpointer):
    model_kwargs: dict = {}
    api_base = os.environ.get("DEEPSEEK_API_BASE")
    if api_base:
        model_kwargs["api_base"] = api_base
    model = ChatDeepSeek(
        model=DEEPSEEK_MODEL,
        temperature=0,          # 确定性输出，降低 SQL 生成幻觉
        max_retries=2,          # DeepSeek 偶发 503，自动重试
        **model_kwargs,
    )
    return create_react_agent(
        model=model,
        tools=tools,
        prompt=SYSTEM_PROMPT,
        checkpointer=checkpointer,
    )