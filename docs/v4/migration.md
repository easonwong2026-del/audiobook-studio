# v3 到 v4 迁移策略

迁移遵循“复制、校验、切换”，不就地改写 v3 项目。

1. 识别 v3 文件并生成迁移报告。
2. 从可用原始文本或旧脚本重建 v4 source；无法证明完整性的项目标记人工确认。
3. 生成带原文坐标的 script 和 speakers。
4. 把可映射的声音绑定转为 production 配置；队列历史只作为审计记录，不冒充新任务。
5. 校验 SHA、片段覆盖、speaker 引用和音频资产。
6. 在独立目录发布 v4 项目，保留 v3 原目录和回滚指针。

Phase 1 不提供迁移执行器，也不改变 v3 项目扫描或默认 UI。迁移工具、双读兼容和 UI 切换属于 Phase 5；确认所有支持项目均可回滚后，Phase 6 才删除旧实现。

## 回滚

- Phase 1 回滚只需停止读取 `audiobook-project-v4`；v3 文件和默认 UI 未改，无数据回写。
- Phase 5 迁移必须输出到新目录，写入源 v3 路径和备份位置；失败删除临时 v4 目录即可。
- 迁移后的 v4 不得覆盖 v3。回滚时重新选择原 v3 项目，不反向覆盖。
- 任何 legacy 删除都必须晚于一轮迁移验证、备份恢复演练和独立 Phase 6 PR。

## Windows 手工验收

1. 在 NTFS 临时数据目录分别导入 UTF-8、BOM、GB18030 TXT、DOCX、EPUB。
2. 检查 `source.txt` 为 UTF-8，`source.meta.json` 字符数和 SHA 与程序重算一致。
3. 遍历所有 segment，确认 `source_start/source_end` 可还原文字，gap 仅为空白。
4. 创建包含中文、英文引号、超长段落和未闭合引号的项目，确认 unresolved 不阻止发布。
5. 创建同名项目，确认原项目不被覆盖；模拟数据库创建失败，确认没有伪完整目标目录。
6. 打开 `runtime.db`，确认 migration 版本为 2；插入 running 测试任务并执行恢复，确认变回 pending/interrupted。
7. 启动现有 v3 UI，创建、打开、合成一个 v3 smoke project，确认默认流程未切换。
8. 不在本阶段启动真实 DeepSeek/OpenAI 或 IndexTTS2。
