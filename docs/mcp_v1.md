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

## V1 tools

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

## 后续边界

V1 不实现声音绑定、合成任务控制、QA 记录、导出和存储维护 MCP tools。它们可在
后续阶段复用现有 services/，不需要改变 stdio dispatcher 或让 MCP 调用 Gradio
callback。
