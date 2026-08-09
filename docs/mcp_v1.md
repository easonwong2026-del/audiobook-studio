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

Phase 3 使用一个同时服务于网页和 Agent 的 `ProductionJobService`。Web 不通过
MCP 调用自己，MCP 也不调用 Gradio callback：

~~~text
MCP ─────┐
         ↓
 ProductionJobService → SynthesisService → repositories / lib / TTS
         ↑
Web ─────┘
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
`cancelled`、`done`、`error`、`interrupted`。持久记录只保存 JSON-safe 数据；
进程重启后，找不到 runtime registry 的旧 active 任务会转换为 `interrupted`，
`resume_production` 使用项目现有 segment status 和缓存继续未完成部分。

同一项目同时只允许一个 active production task。重复的
`project + task_type + idempotency_key` 返回原任务，不会创建第二个 GPU job。
`source` 只用于审计和 Web 展示，支持 `mcp`、`web`、`system`、`recovery`，不会
改变业务路径。

生产状态不返回本机绝对路径；任务 API 只返回 task、scope、状态、进度、错误摘要和
稳定的段/项目标识。

## 后续边界

Phase 3 仍不实现 AI 听感 QA、导出/M4B、云端 worker、分布式任务、多 GPU、权限系统
或 Streamable HTTP。API_VERSION 继续保持 `1`，Phase 1–3 共用同一 MCP V1 stdio
协议。
