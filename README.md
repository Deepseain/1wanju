# 悦食BI · 门店经营数据看板 + AI 智能查数

> 连锁餐饮经营数据分析平台：以「数据看板」呈现 5 家门店的经营全貌，以「AI 助手」用自然语言随时查数、出图。

---

## 项目背景

悦食BI 面向一家连锁餐饮公司（5 家门店）的经营分析场景：销售流水分散在门店系统里，管理者需要一个统一的看板快速掌握营业额、订单、客单价、畅销商品、门店排名与品类结构。

本项目在传统 BI 看板之上，进一步接入大模型，做了一个**自然语言查数助手**——用户可以像问同事一样直接提问（如「上周哪家门店营业额最高？」），AI 会把问题转成 SQL 查库，并用文字 + 图表作答。

核心设计原则：**所有数据必须来自数据库，绝不臆造**；查不到就如实说明。

---

## 功能特性

### 数据看板

- 登录 / 注册（SHA-256 口令，Bearer Token 鉴权，演示账号 `admin / admin123`）
- 日期区间筛选 + 快捷档（近 7 天 / 近 30 天 / 全部），区间自动约束到数据边界
- 核心指标 KPI 卡 ×4：总营业额 / 订单数 / 客单价 / 销售件数（带环比）
- 每日营业额趋势（柱 + 线组合图，hover 联动）
- 支付方式占比环形图
- Top 10 畅销商品表（排名 / 品类 / 销量 / 销售额 / 占比）
- 门店营业额排行条形图
- 品类结构占比图
- 退出登录、token 过期自动跳回登录页

### AI 智能查数助手（悬浮球抽屉）

- **自然语言转 SQL（Text2SQL）**：任意经营数据问题，自动生成只读 SQL 查库作答
- **只读护栏**：拦截 INSERT/UPDATE/DROP 等写操作、SQL 注入、多语句，以及 `users` 等敏感表
- **数据来源标记**：每个回答末尾标注数据来自哪张表/视图，共多少行
- **防编造**：查无数据时礼貌说明「数据不存在」，绝不用常识或猜测补齐数字
- **图表生成**：支持柱状图（排行）、折线图（趋势）、饼图（构成）、面积图，前端 ECharts 交互式渲染
- **SSE 流式输出**：回答逐字打出，工具查询期间显示「正在查询数据…」
- **多轮对话记忆**：checkpoint 按「用户 + 会话」隔离持久化，支持「新对话」清空
- 聊天抽屉可**自由拖动**（标题栏）与**缩放**（右下角手柄）

---

## 技术架构

```
浏览器（登录页 / 看板页，原生 JS + ECharts 渲染）
   │  fetch + Bearer Token
   ▼
FastAPI (uvicorn :5000)
   ├─ aiomysql 异步连接池 ──────────► MySQL `Demo` 库
   │    └─ stores / products / sales / v_sales_detail（三表 JOIN 视图）
   └─ AI 模块（LangGraph ReAct Agent）
        ├─ 模型：DeepSeek deepseek-v4-flash（langchain-deepseek）
        ├─ checkpointer：AsyncSqliteSaver（对话记忆持久化）
        └─ 工具：get_schema / query_data（Text2SQL）/ render_chart（图表）
             └─ /api/ai/chat 以 SSE 流式返回（token / chart / done 事件）
```

- **鉴权**：`users` 表（首次启动自动建表 + 写入演示账号），SHA-256 口令；登录发内存 token（24h），`/api/*` 除登录外均需 Bearer Token。
- **并发友好**：异步后端 + aiomysql 连接池，AI 查询与看板接口互不阻塞。

---

## 快速开始（部署使用）

### 1. 环境要求

- Python 3.10+（本项目在 Anaconda `agentLang` 环境，Python 3.13 实测通过）
- MySQL 8.x（本地 `127.0.0.1:3306`）

### 2. 准备数据库

```bash
# 方式 A：直接导入建库脚本（会重建 Demo 库并写入演示数据）
mysql -u root -p < import_demo.sql

# 方式 B：用脚本从 CSV 清洗入库（products.csv / stores.csv / sales.csv）
python etl_to_mysql.py
```

导入后 `Demo` 库包含：`stores`（5 门店）、`products`（20 商品）、`sales`（清洗后 11,990 行）、`sales_raw` / `sales_rejects`（留痕）、`v_sales_detail`（三表 JOIN 视图），数据范围 2026-05-01 ~ 2026-07-31。

### 3. 配置环境变量

```bash
cd app
cp .env.example .env      # 编辑 .env，填入 DB_PASSWORD 等
```

`.env` 会被 Git 忽略（不入库），模板见 `app/.env.example`：

```ini
DB_HOST=127.0.0.1
DB_PORT=3306
DB_USER=root
DB_PASSWORD=<你的MySQL密码>
DB_NAME=Demo
# DeepSeek 可选：不填则读系统环境变量 DEEPSEEK_API_KEY
# DEEPSEEK_API_KEY=
# DEEPSEEK_MODEL=deepseek-v4-flash
```

### 4. 安装依赖

```bash
pip install -r app/requirements.txt
```

### 5. 配置大模型（AI 模块需要）

设置 `DEEPSEEK_API_KEY`（可在系统环境变量里配置，或写入 `app/.env`）。未配置时看板功能不受影响，仅 AI 对话返回「未配置 key」提示。

### 6. 启动服务

```bash
cd app
python -m uvicorn main:app --port 5000
```

### 7. 登录使用

浏览器打开 http://localhost:5000 ，用演示账号 `admin / admin123` 登录；看板右下角悬浮球即 AI 助手。

---

## 依赖环境

| 依赖 | 版本（本地实测） | 用途 |
|---|---|---|
| Python | 3.13 | 运行环境 |
| MySQL | 8.x | 数据存储 |
| fastapi | 0.133.x | Web 框架 |
| uvicorn | 0.41.x | ASGI 服务器 |
| aiomysql | 0.3.x | MySQL 异步驱动 / 连接池 |
| Jinja2 | 3.x | 服务端模板 |
| langgraph | 1.2.5 | AI Agent 编排 |
| langgraph-prebuilt | 1.1.0 | create_react_agent 预构建 |
| langchain-deepseek | 1.1.0 | DeepSeek 大模型接入 |
| langgraph-checkpoint-sqlite | 3.1.0 | 对话记忆持久化 |
| DeepSeek API Key | — | AI 模块（可选） |

---

## 目录结构

```
F:\11\
├─ app/
│  ├─ main.py                 # FastAPI 应用（路由 / 鉴权 / 看板聚合 / AI SSE 接口）
│  ├─ config.py               # .env 加载 + 数据库配置
│  ├─ requirements.txt
│  ├─ verify_ai.py            # AI 模块冒烟脚本（护栏/schema/工具/三类问答）
│  ├─ ai_agent/               # AI 模块
│  │  ├─ agent.py             # LangGraph ReAct Agent + checkpointer
│  │  ├─ prompts.py           # 系统提示词（防编造 / 来源标记 / 口径）
│  │  ├─ tools.py             # get_schema / query_data / render_chart（只读护栏）
│  │  └─ schema_cache.py      # 启动时读取表结构 + 维度枚举
│  ├─ templates/              # login.html / dashboard.html
│  └─ static/                 # echarts.min.js（本地化，离线可用）
├─ docs/                      # 方案文档
├─ import_demo.sql            # 建库 + 演示数据
├─ etl_to_mysql.py            # CSV 清洗入库脚本
└─ products.csv / sales.csv / stores.csv   # 原始数据
```

---

## AI 模块说明（进阶）

- **Text2SQL**：`query_data` 工具由模型生成只读 SQL，`guard_sql` 做白名单 + 黑名单 + 多语句 + 敏感表四重护栏，结果封顶 1000 行。
- **数据来源标记**：模型回答必须注明 `（数据来源：v_sales_detail 表，共 N 行）`。
- **图表**：`render_chart` 工具复用只读护栏重跑 SQL，服务端拼图表规格，经 SSE `chart` 事件推给前端渲染；图表数据 100% 来自数据库。
- **多轮对话**：`AsyncSqliteSaver` 以 `{username}:{thread_id}` 为键持久化，前端 `localStorage` 存 `thread_id`。