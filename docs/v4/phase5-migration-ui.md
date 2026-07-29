# Phase 5：迁移、完整工作流与默认切换

## 默认行为

“从原始书稿创建”现在默认调用 v4 local source-first 创建服务：导入、规则切分和项目发布不依赖 AI。旧 `structured_script.json` 创建入口保留在“旧版兼容”折叠区，现有 v3 项目管理、声音、生产和交付页面仍可使用。

侧边栏新增“v4 工作流”，按角色确认、角色与声音、TTS 设置/计划、合成队列、试听质检、导出和旧项目迁移组织。v4 handler 只做 Gradio 输入输出适配，领域状态仍由 services/repositories 管理。

## v3 → v4 copy migration

迁移器：

1. 验证 v3 三个核心文件；
2. 复制完整 v3 项目到 `.v3-backups`；
3. 按章节和 segment 顺序重建 source，并明确插入 segment 换行和章节空行；
4. 生成全新 source 坐标、script 和 speaker ID；
5. 标记 `source_origin=reconstructed-from-v3`、`source_fidelity=segment-text`；
6. 仅把显式非默认 emotion/delivery 写为 performance override；
7. 复制存在的 voice binding 到 v4 assets 并计算 fingerprint；
8. 在同父目录临时 staging 中完成所有文件，再原子发布目标；
9. 写 migration marker；相同源再次迁移直接返回已有目标，不重复复制；
10. 永不覆盖、移动或写回 v3 源项目。

## 回滚

- 新建 v4 与 v3 项目使用不同 schema 和页面；切回旧页面即可继续 v3。
- 迁移失败时删除临时 staging，v3 和备份保留。
- 迁移成功后仍保留原 v3 和备份；删除 v4 目标即可回滚。
- Phase 6 之前不删除 v3 reader、服务、页面或文件。

## 导出

v4 export 只读取已装配章节 WAV，按 script 章节顺序统一格式并插入章间停顿。WAV 无需 ffmpeg；MP3/M4B 使用 ffmpeg，缺失时保留中间 WAV 并明确报错。导出不读取 v3 `structured_script.json` 或 JSON 任务状态。

## Windows 验收

1. 新建 TXT/DOCX/EPUB v4 项目，确认无需 AI 即成功。
2. 配置 DeepSeek/OpenAI 后继续角色识别，中止并重启确认只继续未完成 batch。
3. 人工批量指定、创建、锁定和合并角色。
4. 绑定真实参考 WAV，修改单角色 voice 后确认只有相关 task stale。
5. 生成 plan，运行 IndexTTS2，模拟中断、缓存文件删除和 OOM。
6. 试听不同格式输入装配后的章节并导出 WAV/MP3/M4B。
7. 复制迁移一个 v3 项目，核对 fidelity 标记、备份和重复执行。
8. 从旧页面重新打开原 v3 项目，确认未被修改。
