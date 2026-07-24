# 增量 PRD：角色单独补录 / 补合成导出（补录合成）

> 对应功能：有声书合成工作台新增「补录合成」导航分区。
> 阶段：标准 SOP 的产品经理（许清楚）增量 PRD —— 仅需求分析与调研，不含代码。

---

## 1. 产品目标

让**已打开项目**的用户，**无需重传整本 `structured_script.json`**，即可为某个**已绑定音色的角色**，快速输入 / 上传少量台词，用已绑定音色**独立合成并导出为独立音频片段（mp3 / wav）**，立即试听或交付使用 —— 同时覆盖「**缺音重合成**」与「**新增内容补充**」两类高频场景。

---

## 2. 用户故事

1. 作为正在制作有声书的用户，我想在已打开项目里选角色 X、粘贴 3 句补录台词，一键合成并导出 mp3，而不必重传整本书。
2. 作为用户，当某几段缺音（合成失败 / 漏合）时，我想把缺失段的原文粘贴进来，用原角色音色快速补合出独立音频，便于单独检查或交付。
3. 作为用户，我想上传一份小 JSON（含角色 + 多句台词 + 可选情感），让工作台自动合成为一段音频并导出，省去逐句粘贴。
4. 作为用户，我想在导出前试听每句合成结果、确认音色与情感符合预期，再决定是否导出。

---

## 3. 需求池（P0 / P1 / P2）

### P0（必须有）
- **P0-1** 复用当前已打开项目作为音色来源（`ss.project` 非空）；未开项目给明确提示。
- **P0-2** 角色下拉：仅列出当前项目**已绑定音色**的角色（`ss.bindings` 中非空项），默认选中第一个。
- **P0-3** 输入方式 A：文本输入框 + 选中角色 → 按行拆分（每行 = 一段）生成待合成句列表。
- **P0-4** 输入方式 B：上传小 JSON（`role` + `lines[{text, emotion?, emo_alpha?, speech_rate?, pinyin_hints?}]`）→ 解析为待合成句列表；`role` 必须命中项目 `voices`。
- **P0-5** 合成：对每句调用 `tts_engine.init_engine()` 后逐句 `synthesize_segment(text, speaker_audio=ss.bindings[role], emotion, emo_alpha, speech_rate, output_path, num_beams)`。
- **P0-6** 拼接导出：将若干句 wav 按序拼接（句间插入 `SEG_SILENCE_SEC` 静音）+ LUFS 归一 → 导出独立文件（默认 wav，可选 mp3），**不写入整本项目拼接**。
- **P0-7** 导出按钮 + `gr.File` 下载，路径落在数据目录内（项目 `output/`），保证 Gradio 可直接服务。
- **P0-8** 状态提示：逐句「合成中 / ✅ / ❌」反馈；缺音色、空输入、未开项目给明确错误文案。

### P1（应该有）
- **P1-1** 试听播放器：合成后整段试听 + 逐句试听（P0 至少保证整段试听）。
- **P1-2** 情感 / 语速全局覆盖：小 `emotion` 下拉 + `speech_rate` 滑块 + `num_beams` 质量档（复用合成参数语义）。
- **P1-3** 小 JSON 格式校验与诊断：复用 `script_loader` 的别名 / 容错与诊断信息（角色未定义、缺字段给最小示例）。
- **P1-4** 可选「换音色」：从音色库选音频覆盖本次绑定（复用 `_lib_voices` + `override_voice` 模式），不回写 `ss.bindings`。
- **P1-5** 文本拆分增强：除按行外，可选「按标点（。！？；）拆分」长段落。
- **P1-6** 导出格式 mp3 / m4b / wav 可选 + 比特率可选。

### P2（可选，本轮未做）
- **P2-1** 保存到项目：补录结果可选地回写项目 `segments/`（参数感知缓存键）以填补整本书缺失段。
- **P2-2** 与整本合成缓存隔离：补录产物默认不写 `segments/`、`project.json` 的 `segments_status`。
- **P2-3** 本项目补录历史列表（可回溯下载）。
- **P2-4** 支持多角色小 JSON（`voices` + `chapters` 子集），一次补录多个角色。

---

## 4. UI 设计稿（ASCII）

**Tab 放置**：新增独立导航项「补录合成」，放在「合成」与「试听与质检」之间（增量、纯追加，不改动现有 6 个 Group 的事件接线）。

```
左侧导航: [概览][项目][音色资产][合成][补录合成][试听与质检][导出]
                                          └─ ★新增

主工作区（补录合成 Tab）：
┌──────────────────────────────────────────────────────────────┐
│ 📌 补录合成（需先打开项目并绑定角色音色）                       │
├──────────────────────────────────────────────────────────────┤
│ 选择角色：[ 下拉：仅列出已绑定音色的角色 ]   ⚠未开项目时灰显    │
│ 输入方式： (●) 粘贴文本    ( ) 上传小JSON                       │
│ ┌─ 粘贴文本 ─────────────────────────────────────────────────┐│
│ │ [ 文本框：每行一句台词（可多行）                          ] ││
│ └────────────────────────────────────────────────────────────┘│
│ ┌─ 或上传小JSON ─────────────────────────────────────────────┐│
│ │ [文件上传 .json]  [解析/校验] → 解析预览：角色/句数/异常诊断 ││
│ └────────────────────────────────────────────────────────────┘│
│ 合成参数：[情感 ▼] [情绪强度 slider] [语速 slider] [质量 1/2/3 ▼]│
│ [▶ 开始补录合成]                            （⏸ 停止 → P2）     │
│ 状态：合成中… 2/3   ✅句1   ✅句2   ❌句3(原因)                  │
├──────────────────────────────────────────────────────────────┤
│ 试听： [▶ 整段试听 audio]      [▶ 逐句试听 audio]（P1）         │
│ 导出： [格式 ▼ wav/mp3/m4b] [比特率 ▼]  [📦 导出独立音频]        │
│ [下载文件 File]   路径：…/output/supplement_林黛玉_xxx.wav      │
└──────────────────────────────────────────────────────────────┘
```

---

## 5. 小 JSON 格式提案

**最小 schema（单角色，覆盖 P0-4）：**
```json
{
  "role": "林黛玉",
  "lines": [
    {"text": "你放心，我自有道理。", "emotion": "sad", "emo_alpha": 1.0, "speech_rate": 1.0},
    {"text": "这可奇了。", "emotion": "neutral"}
  ]
}
```

字段说明：
- `role`（string，必填）：必须命中当前项目 `voices`；否则报错并提示可用角色列表。
- `lines`（array，必填，≥1）：每条待合成句。
  - `text`（string，必填）
  - `emotion`（string，可选，默认 `neutral`）：枚举 `neutral / angry / happy / sad / excited / whisper / sarcastic`
  - `emo_alpha`（float 0–1，可选，默认 1.0）
  - `speech_rate`（float 0.7–1.5，可选，默认 1.0）
  - `pinyin_hints`（object，可选）

**与 `script_loader` 兼容 / 复用思路**：把小 JSON 包成完整 `Script` dict（`meta` / `voices`{role:{}} / `chapters`[segments]），直接复用现有 `from_dict` + `validate_script` 校验诊断，无需另写校验器：
```python
wrapped = {
  "meta": {"title": "补录", "author": ""},
  "voices": {role: {}},
  "chapters": [{"id": 0, "title": "补录", "segments": [
      {"id": f"sup-{i}", "role": role,
       "emotion": ln.get("emotion", "neutral"),
       "text": ln["text"],
       "emo_alpha": ln.get("emo_alpha", 1.0),
       "speech_rate": ln.get("speech_rate", 1.0),
       "pinyin_hints": ln.get("pinyin_hints", {})}
      for i, ln in enumerate(lines)]}]
}
script = script_loader.from_dict(wrapped)
errors = script_loader.validate_script(script)   # 复用角色未定义 / 缺字段诊断 + 最小示例
```
自动获得：`voices` / `chapters` 缺失诊断、角色未定义诊断、`_MIN_EXAMPLE` 最小示例提示、别名容错（`characters`/`roles`/`cast`/`speakers → voices`，`sections`/`episodes`/`scenes → chapters`，仅规范 key 缺失时回退）。

---

## 6. 待确认问题（已在本功能内给出默认决策）

1. **落盘位置**：默认项目 `output/`（经 `_safe_path_for_file_component` 保证 Gradio 可服务）；不强制用户自定目录。
2. **缺音重合成是否回写整本**：默认仅导出独立片段，不回写 `segments` / `project.json`（符合「单独导出」）；P2-1 回写留作可选。
3. **整本缓存隔离**：补录产物默认不写 `segments/` 与 `project.json`（保持整本状态独立）。
4. **文本拆分**：P0 按行拆分；按标点拆分留 P1 可选（默认 `。！？；`）。
5. **如何指定「原缺失段」**：场景 A 用户粘贴原文即可（本轮不做 `seg_id` 精准回写）；若日后支持，小 JSON 可加 `seg_id` 字段。
6. **角色下拉范围**：仅列已绑定音色角色。
7. **引擎并发冲突**：`tts_engine` 是全局单例，补录合成与整本合成 / 试听重合成必须串行互斥（已在 `tts_engine` 内部用 `RLock` 实现，调用方无需再加锁）。
8. **小 JSON 的 role 不在项目 voices**：报错提示可用角色，不自动新建角色。

---

## 7. 技术可行性备注（关键代码位置）

- **参考音频路径**：`voice_bindings.json` 的 `bindings[role]=path`；读取 `ProjectService.open_project` → `voice_bindings["bindings"][role]`；会话态 `ss.bindings`（`app.py` `open_project` 写入）。
- **单段合成**：`lib/tts_engine.py::synthesize_segment(text, speaker_audio, emotion, emo_alpha, speech_rate, output_path, max_tokens, pinyin_hints, num_beams)`；`init_engine()`（全局单例 `_tts`）。现成范例 `app.py::regenerate_segment`：`init_engine` → 逐句 `synthesize_segment` → 写缓存键路径 → `empty_cache`。
- **拼接 + 导出复用**：`lib/audio_pipeline.py` 新增 `export_supplement(paths, out_path, format, bitrate, target_lufs, insert_silence_sec, ...)`，复用「归一 → LUFS → ffmpeg」三段，不依赖整本 script。
- **Gradio 下载路径白名单**：`app.py::_safe_path_for_file_component` 确保导出文件落在 `allowed_paths` 内。
- **小 JSON 校验复用**：`lib/script_loader.py::from_dict` + `validate_script`（别名容错 + 诊断）。
- **UI / Tab 接线范式**：侧边栏 nav 按钮 + Group 显隐（`app.py`）；打开项目刷新角色下拉 `build_role_choices`；会话态 `services/session.py::SessionState`。
- **配置 / 目录**：`config.get_data_dir()`、`config.get_preview_dir()`；项目目录 `ProjectService.get_project_dir(name)`。

## 8. 约束符合性
本功能严格**增量**：合成复用 `tts_engine.synthesize_segment`、拼接 / LUFS / 转码复用 `audio_pipeline` + `postprocess`、校验复用 `script_loader`，仅新增 `audio_pipeline.export_supplement` 薄封装 + UI 新增独立 Tab。音色选择权仍归用户（`ss.bindings[role]` 为唯一真相），不破坏现有 6 个 Group 行为。
