# Audiobook Studio MCP V1

MCP V1 是独立于 Gradio 的本地 stdio 入口：

~~~text
External Agent
      ↓ JSON-RPC / stdio
mcp_server
      ↓ thin adapters
services
      ↓
repositories
      ↓
lib / project storage
~~~

mcp_server 不 import app.py、Gradio 或任何模型/LLM provider。它只把 MCP
参数转换为现有 Service 调用，并把结果包装为 JSON-RPC tools/call 响应。

## 启动

在仓库根目录运行：

~~~bash
python -m mcp_server.server
~~~

服务器默认从 stdin 读取一行一个 JSON-RPC 请求，并把响应写到 stdout；日志只写
stderr。当前没有网络监听，也不要求 Gradio 已启动。

## Phase 1 tools

| Tool | 作用 |
| --- | --- |
| server_info | 返回 API、structured_script 和能力版本 |
| validate_structured_script | 直接校验内存 JSON，返回 valid/can_create/summary/errors/warnings/script_summary |
| create_project | 复用校验、项目槽位检查、canonical storage 和原子创建 |
| list_projects | 返回项目名、书名、章节/片段统计、进度、占用和修改时间 |
| get_project | 返回 meta、角色、声音绑定、合成、存储和完整性摘要，不嵌入完整剧本 |

validate_structured_script 和 create_project 接受：

~~~json
{
  "project_name": "三体",
  "script": {
    "version": "3.0",
    "meta": {},
    "voices": {},
    "chapters": []
  }
}
~~~

校验错误统一包含 code、severity、path、message 和 fix_hint；必要时还会附带
role、id、expected 等定位信息。warning 不阻止创建，error 会使 can_create=false
并阻止 create_project。

## Phase 2 tools

Phase 2 增加全局音色资产目录和项目角色/演员表生命周期：

| Tool | 作用 |
| --- | --- |
| list_voice_assets / get_voice_asset | 返回稳定 voice_asset_id 和脱敏元数据 |
| set/get/add/update/validate_character_roster | 管理 Character Roster |
| set/get/bind/validate/finalize_voice_cast | 管理 Voice Cast、锁定和重绑定 |
| get_voice_binding_status | 返回绑定、锁定和 synthesis_ready 状态 |
| check_chapter_roles | 检查章节中的已知、新增和未绑定角色 |

## Phase 3 production jobs

Phase 3 使用一个同时服务于网页和 Agent 的 `ProductionJobService`，并由独立
`production_runtime` 进程独占 TTS 模型和 GPU。Web 不通过 MCP 调用自己，MCP
也不调用 Gradio callback；两者都只是 SQLite 命令/查询客户端：

~~~text
MCP ─────┐
         ↓
 ProductionJobService → project-local SQLite ← production_runtime
         ↑                                      ↓
Web ─────┘                         SynthesisService / lib / TTS
~~~

新增 tools：

| Tool | 作用 |
| --- | --- |
| plan_production | 检查项目、范围、Structured Script、Voice Cast 和段落状态，不创建任务 |
| start_production | 异步创建任务，返回稳定 task_id；支持 chapter scope、all scope、options 和 idempotency_key |
| get_production_task | 返回持久化状态、进度、当前段和失败段，不解析日志判断状态 |
| list_production_tasks | 按 project_name、status、source 倒序过滤任务 |
| pause_production / resume_production | 在段边界协作暂停/恢复 |
| cancel_production | 返回 cancelling，worker 到段边界后写 cancelled |
| retry_failed_segments | 只重试实际失败或缺失的段落 |

任务状态为：`pending`、`running`、`pausing`、`paused`、`cancelling`、
`cancelled`、`done`、`error`、`interrupted`。`pause`/`cancel` 先持久化意图，
worker 到段边界后确认最终状态。客户端读取永远不会把任务判成
`interrupted`；只有新的 runtime 成功取得 OS 单实例锁后，才会修复上一 owner
遗留的 active attempt。`resume_production` 会创建 child attempt，保留恢复历史。

任务存放在项目 `01_项目配置/production_tasks.sqlite3`。同一项目同时只允许一个
active production task。重复的 `project + task_type + idempotency_key` 且 payload
完全一致时返回原任务；同 key 不同 payload 返回 `IDEMPOTENCY_CONFLICT`，不会
创建第二个 GPU job。
`source` 只用于审计和 Web 展示，支持 `mcp`、`web`、`system`、`recovery`，不会
改变业务路径。

生产状态不返回本机绝对路径；任务 API 只返回 task、scope、状态、进度、错误摘要和
稳定的段/项目标识。

## Phase 4 quality、repair、workflow 与 delivery

Phase 4 的 UI 与 MCP 共用项目内 Revision/QA/Review、修复任务、工作流与正式交付
模型。技术 QA 和人工 Review 分开记录，修复只在新 revision 通过技术检查后切换
active pointer；正式导出固定一份 revision snapshot，并生成 Delivery Manifest。

| Tool | 作用 |
| --- | --- |
| get_workflow_state | 派生当前阶段、blockers 和 next_actions |
| get_quality_report / list_review_segments / get_segment_review | 查询技术 QA、人工 Review 与 active revision |
| mark_segment_review / run_technical_qa | 写入人工 Review、运行技术检查 |
| regenerate_segments / get_repair_task / list_repairs | 创建和查询 revision-safe 修复 |
| plan_export / start_export / get_export_task / list_exports | 正式导出的 readiness、执行和历史 |
| get_delivery_manifest | 获取交付物相对路径、校验和与 revision snapshot 摘要 |

`plan_export` 会检查缺段、生产失败、QA policy、metadata、active production/repair/export、
regenerating revision、项目完成度和 FFmpeg。它同时返回确定性的
`delivery_input_snapshot` / `delivery_input_hash`，覆盖 structured script、段落顺序、
active revision、音频 checksum、cache identity、voice fingerprint、Voice Cast 和正式
metadata。历史 Manifest 没有匹配的 freshness hash 时只作为历史记录，不会让 workflow
进入 `delivered`。

`start_export` 只做 readiness/snapshot 和 SQLite durable task 创建，立即返回
`export_id` 与 `pending`（或 `running`）状态。整书 WAV、后处理、FFmpeg、字幕和 manifest
由同一 `production_runtime` 管理的 CPU/IO worker 执行；`get_export_task` / `list_exports`
轮询 SQLite lifecycle。执行前后会再次比较 delivery input hash 和 revision snapshot，
变化时以 `DELIVERY_INPUT_CHANGED` 失败。输出写入 task 专属临时目录，校验后 atomic
publish；runtime 中断会把任务标记为 `interrupted`，不会留下 `ready=true` 的新 Manifest。
同一 idempotency key 与完整 payload 一致时 replay，不一致返回 `IDEMPOTENCY_CONFLICT`。
字幕使用同一 active revision snapshot，并在任何段落缺失时整体失败，不会静默生成部分
字幕。所有公开返回只包含项目相对路径，不暴露本机绝对路径。

Technical QA 在多段/全书 MCP 调用中先 analyze，再通过一次 batch mutation 保存
`quality_state.json`；单段 API 保持兼容。正式整书后处理使用 bounded streaming
buffers，FFmpeg 可用时走 loudnorm two-pass，WAV-only 环境使用 bounded fallback，
不会把整本书一次性读入 NumPy float64。

## 后续边界

当前不实现 AI 听感 QA、云端 worker、分布式任务、多 GPU、权限系统或
Streamable HTTP。API_VERSION 继续保持 `1`，Phase 1–4 共用同一 MCP V1 stdio
协议。
