# Audiobook Studio V3 架构

## 边界

工作台接收外部 Agent 生成的单一 `structured_script.json`，只负责本地生产，不负责
小说理解、角色识别或远程模型调用。

```text
外部 Agent / Skill
       │
       │ structured_script.json
       ▼
StructuredScriptImportService
       │
       ▼
ProjectRepository
       │
       ▼
Project / Structured Script
       │
       ▼
Voice Bindings
       │
       ▼
SynthesisService → ProductionRuntime / queue / TTS
       │
       ▼
Listen / Repair
       │
       ▼
Export / Delivery
```

当前产品模型是「一个 Project = 一本书」。Chapter 是书内脚本结构，不是独立项目；
项目选择、打开和维护统一由项目工作台的 Catalog / Bookshelf 提供入口。

## 主要目录

```text
app.py                         Gradio 页面组合和生产事件编排
lib/
  types.py                     Script / Chapter / Segment / ProjectMeta
  script_loader.py             V3 JSON 解析、兼容别名和结构校验
  queue.py                     段级合成、断点续跑和按 engine identity 隔离的缓存
  tts_profile.py               双引擎 profile、冻结 identity 与 cache identity
  tts_engine.py                IndexTTS 2 / 2.5 Backend Adapter
  directed_synthesis.py        片段内、片段前后停顿处理
  audio_pipeline.py            章节拼接、字幕和导出准备
repositories/
  project_repo.py              项目槽位、原子创建、快照、绑定和状态
  binding_repo.py              音色绑定持久化
  config_repo.py               数据目录配置
services/
  structured_script_import.py  JSON 检查、预览和创建唯一链路
  project.py                   项目、声音绑定和音色库服务
  synthesis.py                 合成任务状态和后台任务
  supplement.py                角色补录
  export.py                    交付导出
  environment_diagnostics.py   本地环境诊断
ui/
  pages/                       页面布局
  wiring/                      Gradio 事件接线
  create_project_handlers.py   JSON 导入预览和创建的 UI 适配
```

## 数据协议

一个项目对应一本书。`structured_script.json` 的 `chapters[]` 和每章的
`segments[]` 是全书生产顺序；角色来自 `voices`，声音绑定单独保存在
`voice_bindings.json`，因此重新绑定不会改写原始剧本。

新项目使用纯 v3 布局，项目根只包含四个一级目录：

```text
01_原始资料/书稿/       原始结构化剧本副本
01_原始资料/项目音色/   项目内参考音频
02_生成音频/            分段、章节、合并和补录音频
03_导出成品/            正式导出和补录交付物
99_系统数据/
  配置/                 project.json、structured_script.json、voice_bindings.json 等
  章节数据/             按章节拆分的剧本 JSON
  质检/（历史兼容目录，不属于当前用户工作流）、任务/、缓存/、日志/、临时/
```

v1/v2 项目仍通过 `lib.project_paths` 兼容读取；存储升级必须由用户显式发起，
打开项目不会自动迁移或批量清理用户数据。

角色、章节和声音资产在工作台中使用下拉/单选控件完成选择，旧版内联入口的功能等价
路径已经收敛到「项目工作台 → 角色与声音 → 生产与试听修复」。

`lib.script_loader` 是 JSON 结构的唯一解析入口；`StructuredScriptImportService` 不
维护第二套 Schema。结构校验由 `validate_script` 和
`check_script_consistency` 分工完成：前者负责阻止结构错误，后者负责错误和可继续创建
的 warning。

## Studio 生命周期

```text
start.bat
   ↓
launcher.py
   ↓
Studio instance identity
   ↓
app.py / Gradio
   ↓
ApplicationLifecycleService
   ↓
ProductionRuntime graceful teardown
```

退出来源包括网页「退出 Studio」、`launcher.py --stop`、`stop.bat`、SIGINT、SIGTERM、
SIGBREAK 和 Gradio/application shutdown；所有应用停机请求最终由
`ApplicationLifecycleService` single-flight 编排。网页退出会先请求现有 lifecycle，再让
Gradio server 返回；浏览器标签页关闭不承担 Studio 生命周期。

`7862` 端口占用只用于拒绝启动，不用于发现或终止目标进程。外部停止入口通过 instance
identity 校验 PID、仓库路径和 app 路径，再执行 graceful shutdown 与必要的安全兜底。

## 原子性和安全

项目创建先在 `<projects>/.tmp_<name>_<uuid>/` 中建立完整标记文件，然后用原子替换
变成正式目录；任一步失败都会清理临时目录。创建前和仓库层都会检查目标槽位，避免
覆盖合法项目、Legacy 项目或异常残留。程序不会对用户数据目录执行自动迁移或批量清理。
