# V4 工作流整合说明（V4 Workflow Integration Guide）

> 版本：4.1.0（分支 `refactor/v4-integrated-workflow`）
> 目标：将 V4 底层能力整合进原五步工作流，用户不再需要区分 V3 / V4 流程。

---

## 1. 用户视角：只看到一套流程

正式用户导航只有：

```text
🏠 工作台
① 新建项目
② 项目管理
③ 角色与声音
④ 生产与质检
⑤ 交付
⚙ 设置
```

- 新建项目**默认创建 V4 格式**（source-first、schema v4、runtime.db、原子 staging）。
- 项目管理同时列出 V3 与 V4 项目（带格式标记），V4 项目打开后走原五步页面。
- V3 项目按旧逻辑打开，可通过「复制并升级到 V4」生成新 V4 项目（原项目不变）。
- 项目格式差异由服务层内部处理，页面不感知 V3 / V4 分支。

## 2. 统一服务层（页面与 V4 工作台共用）

| 服务 | 职责 |
| --- | --- |
| `services/v4_project_service.py` | 混合扫描、格式识别、统一打开、状态、V3→V4 迁移入口 |
| `services/v4_voice_service.py` | 音色绑定 + 音频校验 + SHA 指纹 + 自动重生成计划触发局部失效 |
| `services/v4_synthesis_service.py` | 计划生成、后台统一队列、暂停/继续/取消、中断恢复、状态查询 |
| `services/v4_quality_service.py` | 章节/片段音频、重新生成（失效目标缓存置回 pending） |
| `services/speaker_normalization.py` | 角色名规整（过滤情绪/动作/语气/叙述后缀与泛指称呼） |
| `services/audio_validation.py` | 音频存在/可读/格式/时长校验（用户可读错误） |
| `services/synthesis_executor.py` | 增加 `should_pause` 协作暂停钩子（原有缓存/OOM 拆分/中断恢复保留） |

## 3. 暂停 / 继续 / 取消语义（写入 runtime.db）

- **暂停**：协作暂停——当前 TTS 推理完成后在任务边界挂起，不杀进程；暂停期间仍响应取消。
- **继续**：跳过已完成任务（`claim_next_task` 只返回 pending，天然不重复合成）。
- **取消**：已完成任务的音频与缓存（`synthesis_tasks` + `cache_entries`）持久化保留；
  pending 任务立即标记 cancelled，正在合成的任务完成后退出。
- **中断恢复**：进程异常退出后，下次运行 `recover_interrupted_tasks()` 把 running 复位为
  pending 继续。
- 运行状态（`running/paused/cancelling/done/error`）写入 `runtime.db` 的 `run_state` 表，
  页面刷新 / 重启后可读。

## 4. 局部失效与缓存

- cache key 由文本 + voice fingerprint 参与计算（`SynthesisPlanner`）。
- 绑定/更换音色后，`V4VoiceService` 自动重新生成计划并 `sync_runtime`，仅相关任务变 stale，
  其它缓存复用。
- `AudioCacheRepository.invalidate` 支持 segment 级重新生成时只失效目标缓存。

## 5. 已修复问题

| 问题 | 修复 |
| --- | --- |
| 未选项目/章节报 WindowsPath / NoneType | handler 判空返回空态，不抛异常 |
| 测试稿三章解析成四章（题名页成前言） | 纯题名页并入第一章旁白（lossless），章节数正确 |
| 无效音频路径 | `audio_validation` 明确校验并转用户可读消息 |
| 角色噪音（她自言自语/顾川急/轻声说/笑着问） | 规则正则收紧 + `speaker_normalization` 规整 + 单元测试 |

## 6. 开发模式（调试独立 V4 工作台）

```bash
set AUDIOBOOK_STUDIO_DEV_MODE=1
python app.py
```

- 环境变量置 1 时，「✨ v4 工作流」导航入口重新显示。
- `ui/pages/v4_workspace_page.py` 及 `ui/v4_workspace_handlers.py` 全部保留。
