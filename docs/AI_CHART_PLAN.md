# 悦食BI · AI 图表生成 — 实施方案

> 版本 v0.1 | 2026-08-20
> 状态：已确认 → 实施中

---

## 1. 结论（选型）

**不需要 MCP。** 对齐 DS4 的做法：LLM 只输出很小的一张「图表规格」，真正的数据来自数据库查询结果，图表在浏览器端用 ECharts 渲染（悦食BI 页面已加载本地 echarts.min.js）。

参考 DS4 `frontend/src/components/ReportCanvas.tsx` 的 `reportChartOption()`：图表规格 = `{chart_type, x, y}` + 数据行，前端拼 ECharts option 客户端渲染。MCP 在这里没有任何角色（跨进程工具暴露才需要，本场景纯开销）。

## 2. 架构

```
AI 提问 → Text2SQL agent
  ├─ query_data(sql)            —— 查数作答（已有）
  └─ render_chart(sql, chart_type, title, x, y)  —— 新增
       1. 复用 guard_sql 只读护栏
       2. 复用 _execute 重跑 SQL（数据 100% 来自 DB，防图表数据被胡编）
       3. build_chart_spec 把 rows 转成 {chart_type, title, labels, series}
       4. 返回 dict(含 chart 字段)
       ↓ stream_ai 里 on_tool_end 捕获 render_chart 的 chart 字段
       ↓ SSE 新增 "chart" 事件
前端收到 chart 事件 → 在聊天气泡下方渲染交互式 ECharts 图（hover/legend）
```

## 3. 图表类型（先支持 4 种）

| type | 用途 | 对应看板已有图 |
|---|---|---|
| bar | 排行/类别比较（门店、品类） | 门店排行条形图 |
| line | 时间趋势 | 每日营业额趋势 |
| pie | 构成占比（支付、品类） | 支付方式环图 |
| area | 带面积的趋势 | — |

## 4. 关键设计点

- **防编造**：图表数据由 `render_chart` 服务端重跑 SQL 产生，模型不直接传数据；空结果则不发 chart。
- **交互式**：不用后端出 PNG，ECharts 客户端渲染，hover/legend 可用。
- **渲染顺序**：chart 事件先到（工具跑完即发），此时占位「正在查询数据…」还在，chart 追加在文本气泡下方；随后 token 流填满文本——最终顺序 = 回答文本在上、图在下。
- **缩放联动**：抽屉 resize 时对已渲染 chart 调 `.resize()`。

## 5. 改动清单

- `app/ai_agent/tools.py`：抽 `_execute()` 共用执行；新增 `build_chart_spec()` + `render_chart` 工具。
- `app/main.py`：`stream_ai` 增加 `on_tool_end` 捕获，发 SSE `chart` 事件。
- `app/templates/dashboard.html`：`.chart-msg` 样式 + `buildChartOption()` + `appendChart()`；`handleSse` 处理 `chart`；resize 联动。

## 6. 验证

- curl 实测：提问含图表诉求 → SSE 出现 `chart` 事件，规格 labels/series 与 DB 对账一致。
- 浏览器：问「各门店营业额对比，画个图」→ 气泡下方出现条形图，可 hover；拖拽/缩放抽屉后图自适应。