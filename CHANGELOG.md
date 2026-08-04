# 更新日志

## 当前开发线：V3.3.3 JSON 工作台

- 以 V3.3.3 基线继续维护本地有声书生产能力。
- 新建项目统一导入外部 Agent 生成的 `structured_script.json`。
- 增加离线 JSON 检查、作品预览、角色/旁白/统计摘要和一致性 warning/error 展示。
- 项目名称按显式项目字段、`meta.title`、文件名顺序自动填写；手工输入不会被覆盖。
- 创建前检查 available、valid、legacy、incomplete、temporary 和 corrupted 槽位，不覆盖已有目录。
- 创建继续复用 `ProjectRepository.create_project()` 的临时目录与原子替换。
- 设置页收口为数据目录、TTS/FFmpeg、本地诊断和异常项目管理。
- 保留角色绑定、IndexTTS2 合成、暂停恢复、断点续跑、试听质检、补录、章节拼接和多格式导出。
- 删除工作台内嵌的原稿分析、Provider、Prompt、模型配置、密钥管理、流式分析和导演/推荐入口。

本轮不创建正式版本、Tag 或 Release；技术基线仍为 V3.3.3。
