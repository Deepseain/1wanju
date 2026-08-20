# 悦食BI · AI 对话查数模块 — 实施方案

> 版本 v0.1 | 2026-08-20
> 状态：✅ 已确认并实施完成（Text2SQL + DeepSeek，端到端验证通过）

---

## 1. 目标

用户在 AI 抽屉里用自然语言问数据问题，AI 生成 SQL 查本地 MySQL `Demo` 库并作答，满足三条硬约束：

1. **数据来源只能是数据库**——回答所引用的每个数字都必须来自真实查询结果；
2. **返回数据标记来源**——答案末尾注明数据来自哪张表/视图；
3. **禁止胡编**——查不到就礼貌说「数据不存在」，绝不臆造。

框架：LangChain / LangGraph + checkpointer 存对话消息。

---

## 2. 现状侦察结论

- 后端 `app/main.py`：`call_ai()` 是占位钩子，`/api/ai/chat` 请求/响应结构已定义（`{messages:[{role,content}]}` → `{reply}`）。
- 前端 `app/templates/dashboard.html`：AI 悬浮球 + 抽屉 + 建议问题 + `sendAi()` 全部就绪，`appendMsg` 渲染 `{reply}`。当前副标题「AI 模块开发中 · 当前为占位回复」需改为已上线提示。
- 数据表：`stores`(5 行)、`products`(20 行)、`sales`(清洗后 11,990 行)、`v_sales_detail`(三表 JOIN 视图)、`sales_raw`/`sales_rejects`(留痕)、`users`(系统账号)。日期范围 2026-05-01 ~ 2026-07-31。
- 运行环境：`E:\Anacoda\envs\agentLang`（Python 3.13）。已安装 `langgraph 1.2.5`、`langchain-deepseek 1.1.0`、`langgraph-checkpoint-sqlite 3.1.0`、`aiomysql 0.3.2` 等——**无需新增依赖**。

参考项目 AIStoreSystem（`E:\Program_Files\AIStoreSystem\ai-assistant`）：FastAPI + LangGraph ReAct + FastMCP 独立服务 + MemorySaver + deepseek-v4-flash + SSE 流式。

---

## 3. 方案选型：Text2SQL（推荐） vs MCP 工具

| 维度 | A. Text2SQL（进程内工具） | B. MCP 工具（参考 AIStoreSystem） |
|---|---|---|
| 适用场景 | **BI 查数**：任意 ad-hoc 分析问题 | 固定业务操作（查订单/加购/领券） |
| 灵活性 | 高——「6 月徐汇区客单价环比」这类组合问题可直接答 | 低——受限于预定义查询原语，只能答预设问题形态 |
| 架构复杂度 | 低——工具与数据库同进程，**无需多起一个服务/端口** | 高——额外 MCP Server 进程 + streamable-http + 每请求建连接 |
| 数据源 | 直接 SQL 查本地 MySQL | MCP 工具再转调服务（本项目无独立业务服务，需自造） |
| 幻觉风险 | SQL 生成可能错，靠只读护栏 + schema 注入 + 错误重试控制 | 低——查询逻辑固定 |

**结论：选 A（Text2SQL）。** 本项目数据源就是本地 MySQL，且用户意图是「任意自然语言查数」，Text2SQL 天然匹配；MCP 的额外进程/网络跳转在这里是纯开销，没有同进程外要协调的服务。MCP 方案保留为将来「若要把查数能力暴露给外部 Agent/多服务」时的演进路径。

---

## 4. 架构设计

```
浏览器 AI 抽屉（已就绪，微调）
   │  POST /api/ai/chat  { message, thread_id }
   ▼
FastAPI (app/main.py) —— call_ai() 改为接入 agent（最小改动）
   │  ainvoke(graph, config={"configurable": {"thread_id": "<user>:<tid>"}})
   ▼
LangGraph ReAct Agent (create_react_agent)  [app/ai_agent/agent.py]
   ├─ model   : ChatDeepSeek(deepseek-v4-flash, temperature=0)
   ├─ checkpointer : AsyncSqliteSaver（对话消息持久化，thread 隔离）
   └─ tools（进程内，lanchain `@tool`）：
        ├─ query_data(sql)  —— Text2SQL 执行器（只读护栏 + 数据来源标记）
        └─ get_schema()     —— 表结构 + 维度枚举值（启动时缓存，供模型写准 SQL）
              │  只读校验 + aiomysql 池
              ▼
        MySQL Demo 库（只读）
```

### 4.1 agent 构建（`app/ai_agent/agent.py`）
- `create_react_agent(model=ChatDeepSeek(...), tools=[query_data, get_schema], prompt=SYSTEM_PROMPT, checkpointer=saver)`
- 模型：`deepseek-v4-flash`，`temperature=0`（确定性，降低 SQL 幻觉）；`DEEPSEEK_API_KEY` 环境变量，缺失则启动失败（fast fail，与参考项目一致）。
- checkpointer：`AsyncSqliteSaver`，文件 `app/data/ai_checkpoints.sqlite`，`thread_id = "{username}:{thread_id}"`（username 取自鉴权 token，thread_id 前端 localStorage 持久，二者拼一起保证用户隔离 + 可多会话）。

### 4.2 工具设计（`app/ai_agent/tools.py`）

**`query_data(sql: str)` → 结构化结果**
```json
{ "ok": true,
  "columns": ["store_name", "revenue"],
  "rows": [["Super Souper", 98000.0], ...],
  "row_count": 5,
  "source": { "tables": ["v_sales_detail"], "sql": "SELECT ..." },
  "note": null }
```
- 空结果：`ok=true, rows=[], row_count=0, note="查询结果为空"`。
- 只读护栏拦截：`ok=false, error="仅允许只读查询"`。
- SQL 执行报错：`ok=false, error=<报错信息>`（让模型看到错误自行改写）。
- 返回前把 `Decimal/date/datetime` 序列化为 JSON 安全类型。

**`get_schema()` → 数据字典**
- 启动时（lifespan）读 `information_schema` 缓存：每张表/视图的列名、类型、注释 + 关键维度列（store_name / product_name / product_category / district / payment / category）的去重枚举值（带条数，封顶）。
- 模型拿到准确的门店名/商品名，避免拼错 `S01/S02`、门店名、品类字面量。

### 4.3 只读 SQL 护栏（防误写/注入）
- 语句白名单：仅放行 `SELECT / SHOW / DESCRIBE / DESC / EXPLAIN / WITH ... SELECT`。
- 黑名单拦截：`INSERT/UPDATE/DELETE/DROP/ALTER/TRUNCATE/CREATE/GRANT/INTO OUTFILE/FOR UPDATE` 等一律拒绝。
- 去尾注释/分号；禁止多语句（`;` 后另有内容即拒）。
- 结果行数封顶（默认 1000，`LIMIT` 补足），防止一次拉爆内存。
- **纵深防御（推荐，可选）**：建只读 MySQL 账号 `bi_reader`（`GRANT SELECT ON Demo.*`），AI 查询走独立连接池；即使应用层漏判，DB 层也写不进去。

### 4.4 数据来源标记 + 防臆造（`app/ai_agent/prompts.py`）
SYSTEM_PROMPT 固化三条规则：
1. 所有数字/事实**必须来自 `query_data` 返回**，严禁编造，禁止用常识猜补（如「大概 30 家店」）。
2. 回答引用数据时，末尾附注来源：`（数据来源：v_sales_detail 表，共 N 行）`。
3. `row_count==0` 或 `ok==false` 时，礼貌说明「抱歉，数据库中没有查到相关数据 / 查询失败」，不臆造。
4. 写入 SYSTEM_PROMPT 的 schema 概览（表关系：`sales.store_id→stores`、`sales.product_id→products`，聚合口径：营业额=SUM(amount)、订单数=COUNT(DISTINCT order_id)、客单价=营业额/订单数）。

### 4.5 对话历史（checkpoint）
- checkpointer 作为**历史唯一真源**：每次请求只把「最新一条用户消息」作为 graph 输入，历史由 `thread_id` 恢复，前端不再回传全量 messages。
- 前端微调：localStorage 存 `ai_thread_id`，请求体从 `{messages:[...]}` 改为 `{message, thread_id}`；新增「新对话」按钮清空 thread_id。
- 兼容：thread_id 缺失时退化为 `"{username}:__default__"`（每用户单会话）。

---

## 5. 文件改动清单

**新增 `app/ai_agent/`**
```
app/ai_agent/
├─ __init__.py
├─ agent.py          # 构建 ReAct agent + AsyncSqliteSaver + thread 组装
├─ prompts.py        # SYSTEM_PROMPT（schema、防臆造、来源标记、口径）
├─ tools.py          # query_data / get_schema（只读护栏、序列化、来源标记）
└─ schema_cache.py   # lifespan 时读 information_schema + 维度枚举值并缓存
```

**改动**
- `app/main.py`：`call_ai()` 由占位改为调用 agent；lifespan 里初始化 schema 缓存 + checkpointer；`/api/ai/chat` 请求模型加 `thread_id` 字段（可选）。
- `app/templates/dashboard.html`：抽屉副标题改「AI 已上线」；`sendAi()` 改发 `{message, thread_id}`；thread_id 持久化 + 「新对话」按钮；回复渲染保留（`{reply}` 不变）。
- `app/requirements.txt`：追加已装依赖（可选，仅文档化）：`langgraph>=1.2.5`、`langchain-deepseek>=1.1.0`、`langgraph-checkpoint-sqlite>=3.1.0`。
- 新增 `app/verify_ai.py`：冒烟脚本（对齐现有 verify_api.py 风格）。

---

## 6. 实施步骤（确认后执行）

| # | 内容 |
|---|---|
| S1 | `app/ai_agent/schema_cache.py`：读 information_schema + 维度枚举，写 `get_schema` 数据源 |
| S2 | `app/ai_agent/tools.py`：`query_data`（只读护栏 + 序列化 + 来源标记）、`get_schema` 工具 |
| S3 | `app/ai_agent/prompts.py` + `agent.py`：SYSTEM_PROMPT + ReAct agent + AsyncSqliteSaver |
| S4 | `app/main.py`：`call_ai()` 接入 agent，lifespan 初始化 |
| S5 | `dashboard.html`：抽屉副标题 + thread_id 收发 + 新对话按钮 |
| S6 | 验证：`verify_ai.py` 冒烟 + 手动 curl + 三类问题回归 |
| S7 | 自审（Critical/Warning/Suggestion）交付 |

---

## 7. 测试与验证

**三类必测问题**（真库回归）：
1. 命中：`上周哪家门店营业额最高？` → 返回门店 + 金额 + 来源标记。
2. 无数据：`数据库里有没有新加坡门店的数据？` → 礼貌「数据不存在」，不臆造。
3. 越界：`帮我查一下员工工资`（库内无该表）→ 说明查不到/无法回答，不编造。
4. 护栏：直接注入 `query_data("DROP TABLE sales")` → 被拒。

**验证方式**：`verify_ai.py` 脚本直调 `call_ai()`，再 curl 走一遍 `/api/ai/chat` 端到端。

---

## 8. 风险与坑

- **Clash 代理劫持 localhost**：DeepSeek 走外网没问题；工具查本地 MySQL 用 aiomysql（TCP 直连，不走 httpx 代理），无此问题。但若将来用 httpx 调本机服务需 `trust_env=False`（参考项目踩过）。
- **非 ASCII 用户名**：本机用户「阿豪」，临时文件/测试输出避免写用户名相关路径；冒烟脚本输出到项目内。
- **DeepSeek 官方偶发 503**：体温重试/错峰（已知）。备选切 DashScope `qwen3.7-max`（langchain-openai 兼容）。
- **版本细微差**：环境 fastapi=0.133.1 / uvicorn=0.41.0 略低于 requirements 声明（0.139/0.45），实测可用则不动。
- **checkpointer 换后端不迁移**：若以后 MySQL→别的库，历史不迁移（已知，接受）。

---

## 9. 待确认问题

1. **方案确认**：Text2SQL（推荐）还是 MCP 工具？（默认按 A 实施）
2. **模型/凭证**：用 `deepseek-v4-flash` + `DEEPSEEK_API_KEY`？还是 DashScope qwen？（需确认 key 已在环境变量）
3. **checkpointer**：`SqliteSaver`（持久化，推荐）还是 `MemorySaver`（重启丢历史）／MySQL checkpointer（社区包）？
4. **只读账号**：要不要额外建 `bi_reader` 只读 MySQL 账号做纵深防御，还是仅应用层护栏？
5. **流式**：本版先同步返回 `{reply}`（最小改动），SSE 流式留后续？（参考项目是流式的）