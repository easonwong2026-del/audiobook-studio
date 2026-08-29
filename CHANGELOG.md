# 更新日志

## 3.4.0 — 2026-08-29

V3 工作流稳定收口版本：一个 Project 对应一本书，生产、试听修复、交付和 Studio
生命周期形成完整的本地工作流。

### Workflow

- 工作流正式收敛为「一个项目一本书」；Chapter 只是书内脚本结构。
- 项目创建、选择、打开和维护统一收敛到 Catalog / Bookshelf。
- 统一使用外部 Agent / Skill 生成的 `structured_script.json` 作为项目入口。

### Production

- 完善合成总耗时和启动阶段 elapsed time 展示。
- 活跃生产任务使用单一 observer；idle / terminal 状态停止无意义的高频页面刷新。
- 收敛 Runtime TTS liveness、任务状态和 engine prewarm 路径。
- 保留暂停、恢复、取消、断点续跑、失败重试和多引擎缓存隔离。

### Listen & Repair

- 章节试听与段落试听统一到「试听与修复」工作区。
- 使用单一 current segment 作为单段修复目标。
- 修复默认继承原始 voice / emotion / rate，并支持临时覆盖。
- 保留 revision / rollback；当前生产流程不再使用 QA gate。

### Studio Lifecycle

- 增加安全的 Studio start / status / stop 生命周期。
- 使用 instance identity 防止 stale PID、PID reuse 和 foreign-port 误操作。
- 增加 `stop.bat` 和网页「退出 Studio」入口；active production 退出时给出明确提醒。
- 关闭浏览器标签页不会自动停止 Studio 服务。

### MCP

- MCP advertised surface 收敛为 24 个工具：20 个 Core 和 4 个 Advanced。
- 旧 handler 名称保留兼容调用，但不再主动出现在 `tools/list`。

### Architecture / Cleanup

- 移除 Chapter Project、Chapter Merge 和 Whole-book Assembly 的当前工作流模型。
- 保留 ProjectRepository、结构化脚本、Voice Cast、ProductionRuntime、Listen / Repair
  和 Export / Delivery 的清晰边界。
- Storage Layout v3 成为当前项目布局；旧项目读取和显式存储整理保持兼容。

### Reliability

- 保留结构化脚本离线校验、warning/error 预览和原子项目创建。
- 保留 revision、任务、导出交付清单和跨进程 Runtime 的持久化安全边界。
- 既有用户项目无需迁移；存储布局整理仍由用户显式发起。

## 3.3.3 — 历史开发线

- **Storage Layout v3（存储布局分层，开发中未发布）**：项目根目录收敛为 4 个用户可理解的一级目录（`01_原始资料/ 02_生成音频/ 03_导出成品/ 99_系统数据/`）。系统 JSON 全部进 `99_系统数据/配置/`，章节拆分 JSON 进 `99_系统数据/章节数据/`，质检/任务/缓存/日志/临时各归其位；新项目创建即纯 v3（不建 legacy 空目录/junction）。打开项目**不自动迁移**；旧版项目通过书架「整理存储布局」显式 `plan → token → execute` 三步迁移（迁移前自动完整备份，备份路径记录在结果中；live 任务拒绝迁移；unknown 文件默认保留到 `99_系统数据/迁移保留/`；失败自动回滚）。
- **v3 路径解析统一**：`lib/project_paths` 成为唯一版本判定（`detect_storage_version`）与路径解析入口（`directory_map` / `project_dir` / `project_file` / `resolve_relative` / `make_relative`），业务模块不再散落 `os.path.join(project_dir, "exports")` 式硬编码；v1/v2 项目 backward 读取保持兼容。
- **正式导出 v3 语义**：工作目录 `99_系统数据/临时/export/<task_id>/`，发布到 `03_导出成品/正式导出/<YYYYmmdd_HHMMSS_书名>/`（保留 atomic publish / ownership fencing / delivery manifest / hash）；失败只清理临时工作目录，绝不留半成品到 `03_导出成品`。
- **补录 v3 语义**：补录合成 WAV → `02_生成音频/补录音频/<task_id>/`，补录导出 → `03_导出成品/补录/`；删除旧 `output/` junction 创建。
- 以 V3.3.3 基线继续维护本地有声书生产能力。
- Runtime/TaskRepository 热路径优化：`load_project_task` project-local O(1) 读取；pending claim 信号门控（`runtime_pending.signal` 原子写 + mtime 变化检测 + 每类型 claim 去重）；schema-once（每个 path+进程只执行一次建表/迁移/legacy 导入，探针自愈）；heartbeat 局部化（仅扫 owned 项目 + 30s 全扫兜底）；adaptive idle polling（active 0.1s / idle 1s，`poke()`/`stop()` 立即唤醒）。5 段 warm-engine 基准：`_connect` 8,577 → ~379 inline / ~476 process（约 -95.6%），schema/PRAGMA 重路径约 -99.7%，claim 全库扫描 ~197 → ~3，任务列表/快照/暂停/恢复/取消/导出行为无回归。
- 新建项目统一导入外部 Agent 生成的 `structured_script.json`。
- 增加离线 JSON 检查、作品预览、角色/旁白/统计摘要和一致性 warning/error 展示。
- 项目名称按显式项目字段、`meta.title`、文件名顺序自动填写；手工输入不会被覆盖。
- 创建前检查 available、valid、legacy、incomplete、temporary 和 corrupted 槽位，不覆盖已有目录。
- 创建继续复用 `ProjectRepository.create_project()` 的临时目录与原子替换。
- 设置页收口为数据目录、TTS/FFmpeg、本地诊断和异常项目管理。
- 保留角色绑定、IndexTTS 2 Legacy / IndexTTS 2.5 双引擎合成、暂停恢复、断点续跑、试听质检、补录、章节拼接和多格式导出。
- 删除工作台内嵌的原稿分析、Provider、Prompt、模型配置、密钥管理、流式分析和导演/推荐入口。

本历史开发线不创建正式版本、Tag 或 Release。
