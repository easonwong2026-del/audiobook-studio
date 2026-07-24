# 有声书合成工作台（Audiobook Studio）— 架构与代码质量评审报告

> 评审人：software-architect（独立评审）
> 评审对象：`C:\Users\rakliang\WorkBuddy\2026-06-29-18-28-53\audiobook-studio\`
> 评审依据：实际阅读 `app.py`、`lib/*.py`、`launcher.py`、设计文档 `ARCHITECTURE.md` / `DESIGN.md` 及 `更新日志.txt`，并对照真实工程 JSON（如 `我在末世无限重生3`）核对数据模型落地情况。
> 方法：所有结论均引用具体文件与行号；区分「已确认 BUG / 设计承诺缺失 / 潜在隐患」三类。

---

# 块一：代码 / 架构评审报告

## 1.1 整体架构评价（两个 Bounded Context、段级缓存、持久化、Gradio 单体）

### 优点

1. **职责边界清晰（lib 层）**：`project_manager.py`（项目生命周期）、`tts_engine.py`（TTS 封装 + VRAM）、`script_loader.py`（解析 + 校验）、`queue.py`（合成队列 + 断点续跑）、`audio_pipeline.py`（拼接导出）五模块职责单一、耦合低，是本项目结构上的最大亮点。
2. **两个 Bounded Context 的契约设计合理**：Context A（WorkBuddy 文本分析）产出 `structured_script.json`，Context B（本工作台）只做执行。以「JSON 剧本」作为两个上下文之间的稳定接口，解耦了 AI 分析与语音合成，方向正确。
3. **段级文件缓存 + 断点续跑**：`segments/{seg_id}.wav` 文件即缓存、`project.json` 维护段状态表、`get_remaining()` 只跑 pending+failed、`_repair_meta()` 在打开项目时自动对齐段 ID —— 这套机制让「合成长达数小时、关窗可恢复」的目标在**概念层面成立**，对长任务场景非常务实。
4. **音色库 + 角色绑定**的模型直观，复用在 Tab1（绑定）与 Tab3（换音色）两处，概念统一。

### 缺点

1. **Gradio 单体反模式**：`app.py`（437 行）同时承担 UI 声明、事件接线、业务流程编排、全局可变状态（`S` 字典）、错误提示。回调直接穿透到 `pm/tts_engine/queue/audio_pipeline`。**没有 service 层、没有可注入的边界、没有任何单元测试**。可测试性与可演进性都很差。
2. **核心 TTS 能力大面积「未落地或坏掉」**：语速（speech_rate）、多音字（pinyin_hints）、OOM 自动拆段、响度归一化（LUFS -16）、ID3/章节标签、均衡（equalization）、每角色「试音」按钮 —— 这些在 `ARCHITECTURE.md/DESIGN.md` 明确承诺或为 `更新日志.txt` 声称已实现的能力，经核对要么**代码根本没接**，要么**接了但有致命 BUG**。详见 1.2/1.3。
3. **双数据表示、死代码与文档漂移**：
   - `lib/types.py` 的 `Segment/Chapter/VoiceInfo/Script` dataclass 只在 `queue.py` 经 `script_loader.load_script` 使用；而 `app.py` 全程用原始 `dict`（如 `app.py:84,179,199,233,247`），两套表示并存。
   - `script_loader.validate_script`（校验函数）**全工程零调用**；`app.py:137 test_voice` **未接线**（无 `.click` 事件），属于死代码。
   - `ARCHITECTURE.md` 底部仍写「准备开始编码」，但代码已存在；`ARCHITECTURE.md:249` 引用 `requirements.txt` 而该文件不存在（Glob 确认）；文档中 `create_project(root, name, script_path) -> dict` 与真实签名 `create_project(name, script_path) -> str` 不符。
4. **无测试、无依赖清单、依赖检查不完整**：`launcher.py` 只校验/安装 `gradio`+`pydub`，但 `audio_pipeline.py` 需要 `scipy/numpy`，导出需要 `ffmpeg`，均无校验；无 `requirements.txt`，可复现性差。

---

## 1.2 问题清单（含已确认 BUG / 设计承诺缺失 / 潜在隐患）

> 严重程度定义：
> - **Critical** = 功能直接损坏 / 产生错误数据
> - **High** = 明确 BUG 或设计承诺未实现
> - **Medium** = 稳健性 / 性能问题
> - **Low** = 代码整洁 / 体验细节

| 编号 | 类别 | 文件:行 | 严重程度 | 问题描述 | 影响 | 修复建议 |
|---|---|---|---|---|---|---|
| B1 | 已确认 BUG | `lib/tts_engine.py:79-81` | **Critical** | OOM 自动拆段分支位置参数错位且未拼接。`synthesize_segment(text, speaker_audio, emotion, emo_alpha, output_path, max_tokens, ...)`，但递归调用写成 `synthesize_segment(text[:mid], speaker_audio, emotion, path_a, max_tokens)`——`path_a`(字符串)被当作 `emo_alpha`，`max_tokens`(整数)被当作 `output_path`；且 `return output_path`（原始路径）之前**从未把 `_a/_b` 两段拼接写回原路径**。 | OOM 恢复机制完全失效：大段触发 OOM 时，内层调用几乎必然因 `emo_alpha=str`/`output_path=int` 抛 `TypeError` 或直接标记 failed；即便不报错的路径，原 `output_path` 文件也不会被写出，段状态却可能被标 `done` 但文件缺失。 | 用关键字参数调用并补全拼接：`synthesize_segment(text[:mid], speaker_audio, emotion, emo_alpha, path_a, max_tokens, pinyin_hints)`；两段合成后 `audio_pipeline`/pydub 拼接 `_a+_b → output_path` 再 `return output_path`。 |
| B2 | 已确认 BUG | `lib/queue.py:62-68`（关联 `lib/types.py:13-15`、`lib/script_loader.py:30-32`、`lib/tts_engine.py:35-43,57-65`） | **High** | 批量合成路径**丢弃 `speech_rate` 与 `pinyin_hints`**。`script_loader` 已从 JSON 读入 `Segment.speech_rate/pinyin_hints`，但 `queue.py` 调用 `synthesize_segment` 时未传；更关键的是 `tts_engine.synthesize_segment` **根本没有 `speech_rate` 形参**，且 `pinyin_hints` 形参虽接收却**未转发给 `_tts.infer`**。 | 真实工程 JSON（如 `我在末世无限重生3`）每个段都带 `speech_rate`（0.7~1.3），但全部以默认 1.0 合成；多音字提示完全失效。叙事节奏意图丢失。 | 给 `synthesize_segment` 增加 `speech_rate` 形参并透传到底层（按 `IndexTTS2` API，可能经 `speed`/`generation_kwargs`）；`pinyin_hints` 转发给 `_tts.infer`；`queue.py` 传 `seg.speech_rate, seg.pinyin_hints`。 |
| B3 | 已确认 BUG | `app.py:242`（签名）, `app.py:261-262`（调用遗漏） | **High** | 手动「重合成」接收了 `speech_rate`(来自 `e_rate` 滑块) 却没有透传给引擎。 | `更新日志.txt:10-11` 声称语速滑块可重合成，实际无效——改语速重合成声响不变。 | 在 `app.py:262` 调用中补 `speech_rate=speech_rate`。 |
| B4 | 已确认 BUG | `app.py:188-194`（调用）, `lib/audio_pipeline.py:15`（形参已具备） | **High** | `do_export(fmt, output_dir)` **未把比特率 `e_br` 透传**给 `audio_pipeline.export_book`；后者形参 `bitrate="192k"` 默认生效。 | 导出页「比特率」下拉（`128k/192k/320k`）被完全忽略，所有导出静默落到 192k，用户选择无效。 | `audio_pipeline.export_book(..., bitrate=e_br)`。 |
| B5 | 已确认 BUG | `app.py:418` | **Medium** | `create_project` 事件把同一组件 `p_sel` 作为输出出现**两次**：`[p_name,p_script,p_create_msg,p_sel,p_sel]`。函数返回第 4/5 个值分别是 `gr.update(choices=pm.scan_projects())` 与空 `gr.update()`。 | Gradio 对重复 output 采用「后者覆盖前者」，空 `gr.update()` 可能把刚刷新的 `choices` 冲掉，导致新建项目后下拉框不更新（需手动点 🔄）。行为依赖 Gradio 版本，属脆弱写法。 | 去掉重复输出，只返回一次 `gr.update(choices=...)`；或第二个槽位返回 `None`。 |
| B6 | 已确认 BUG / 性能 | `app.py:153,171` + `lib/project_manager.py:103-115,144-149` | **Medium** | `do_synthesis` 每合成一段都 `pm.open_project(proj)` 取进度；而每次 `update_segment_status`（每段一次，见 `queue.py:72/84`）都走 `_load_meta`（`project.json`+`structured_script.json`+`voice_bindings.json` 三次读，且 `_repair_meta` 又读一遍 `structured_script.json`）→ 全量重写整个 `project.json`。 | 1247 段 → ~1247 次整文件重写（写入量 O(n²)）+ ~数千次文件读。大状态表/慢盘下明显拖慢。**纠正主理人表述**：open_project 的 `_repair_meta` 仅在段 ID 不一致时重写（一致即早退），真正的「每段落全量写」来自 `update_segment_status`，而非 open_project 本身。 | 内存中维护 meta，按章节或每 N 段批量 `_save_meta` 一次；`open_project` 热路径避免重复读 `structured_script.json`（缓存或拆分纯读接口）。 |
| B7 | 已确认 BUG（功能） | `lib/queue.py:60`（关联 `ARCHITECTURE.md:87`） | **Medium** | 段缓存命中判定仅看 `segments/{seg_id}.wav` 是否存在；而 `ARCHITECTURE.md:87` 承诺缓存键 = `(text+role+emotion)`。 | 批量合成中若修改某段 `emotion/emo_alpha` 后重跑，因 `seg_id.wav` 已存在而**不会重合成**，情感编辑在批量模式下无法生效（只能手动删文件或走 Tab3 重合成）。 | 缓存键纳入 `emo_alpha/emotion/speech_rate/pinyin_hints` 的哈希；或在参数变更时失效旧文件。 |
| B8 | 已确认 BUG（音频质量） | `lib/audio_pipeline.py:30-38,46` | **Medium** | 导出把所有段 `np.concatenate` 成**一段**连续音频，**段间/章间无静音间隔、无章节边界、无停顿**；`chapters/` 目录被 `create_project` 建出却从未写入。 | 成品是「一句接一句无停顿」的连续块，体验差；没有按章拆分，违背「逐章下载」承诺。 | 段间插入短静音（如 200-400ms），章首另加较长停顿；按需输出每章文件。 |
| B9 | 已确认 BUG | `lib/tts_engine.py:101`（关联 `app.py:137-143`） | **Medium** | `test_voice` 内部 `synthesize_segment(text, speaker_audio, emotion, out, max_tokens)` 与 B1 同源的位置参数错位（`out→emo_alpha`、`max_tokens→output_path`）。此外 `app.py:test_voice` 从未接线到任何 UI 事件，是**死代码**。 | 该函数即便被调用也会报错；现状是「试音」能力既坏又未接线。 | 改用关键字参数；修好后接入 Tab1 每角色试音按钮（见 D4）。 |
| B10 | 已确认 BUG（体验） | `app.py:400`（声明）, `app.py:426`（保存只刷 `v_lib`） | **Low** | 导出页「换音色」下拉 `e_voice` 在 build 时一次性填充 `choices=_lib_voices()`；`save_to_lib` 只更新 `v_lib`，不更新 `e_voice`。 | 保存到音色库后，导出页换音色下拉不显示新音色，需重启应用。 | `save_to_lib` 同时 `gr.update(choices=_lib_voices())` 刷新 `e_voice`。 |
| B11 | 潜在隐患（整洁性） | `app.py`（全程 dict）, `lib/types.py`（dataclass） | **Low** | `app.py` 用原始 `dict` 操作剧本，`queue.py` 用 `script_loader` 的 dataclass，两套表示并存；`types.py` 中 `Segment/Chapter/VoiceInfo/Script` 在 app 侧形同虚设。 | 维护成本高，字段变更易遗漏；类型安全未真正利用。 | 统一以 `script_loader.load_script` 产出对象贯穿全流程，或统一用 dict + 显式 schema。 |
| B12 | 已确认 BUG（健壮性） | `lib/script_loader.py:43`（`validate_script` 零调用，见 grep 确认） | **Medium** | `validate_script` 定义但**全工程从未调用**；`app.py` 直接 `json.load` 而不校验。 | 非法剧本（缺 `role/text`、角色未在 `voices` 定义）不会在导入时报错，而是**合成中途 `KeyError` 崩溃**（如 `queue.py:46/61`、`app.py:184/212`）。 | 在 `create_project` / `open_project` 调用 `validate_script`，提前返回清晰错误。 |

| 编号 | 类别 | 文件:行 | 严重程度 | 问题描述 | 影响 | 修复建议 |
|---|---|---|---|---|---|---|
| D1 | 设计承诺缺失 | `lib/audio_pipeline.py`（整体） vs `ARCHITECTURE.md:301` | **High** | **LUFS -16 响度归一化未实现**。代码仅有朴素拼接 + ffmpeg 转码，无任何响度处理（无 `pyloudnorm`/`ffmpeg loudnorm`）。 | 成品响度未统一，多角色/多批次拼接后音量忽大忽小，不专业。 | 转码时加 `loudnorm=I=-16:TP=-1.5:LRA=11` 或后处理归一化。 |
| D2 | 设计承诺缺失 | `lib/audio_pipeline.py` vs `ARCHITECTURE.md:301` | **High** | **ID3 / 章节元数据标签未写入**。无 `eyeD3`、无 ffmpeg `-metadata`、无 m4b 章节原子写入。 | mp3 无标题/作者/封面标签；m4b 实为「.aac 套 .m4b 壳」，无章节跳转。违背「有声书」核心体验。 | mp3 写 ID3（标题/作者/封面），m4b 用 `ffmpeg` 章节元数据或 `mp4box`/Apple 章节。 |
| D3 | 设计承诺缺失 | `lib/audio_pipeline.py` vs `ARCHITECTURE.md:28,301` | **Medium** | **均衡（equalization）未实现**。 | 不同参考音色的频响差异未被补偿，成品一致性差。 | 可选：统一高通/低通或轻量 EQ 链（如 `pydub`/`sox`）。 |
| D4 | 设计承诺缺失 | `DESIGN.md:156-160`、`ARCHITECTURE.md:157-161` vs `app.py:336-367`（Tab1 仅 Markdown 表格） | **High** | **每角色「🎧 试音」三句测试句按钮缺失**。`DESIGN` 明确承诺 Tab1 每行有试音按钮；实际 Tab1 角色表只是 Markdown 文本，无任何试音按钮。`app.py:137 test_voice` 死代码未接线。`更新日志.txt:32` 更写明「试听音色按钮移除」。 | 用户无法在跑数小时长任务前快速验证音色/情感是否满意，违反 ADR-005 的「早期发现问题」初衷。 | 在 Tab1 用 `gr.Accordion`/动态 `gr.Row` 为每个角色生成试音按钮，调用修复后的 `tts_engine.test_voice`（用三句测试句）。 |
| D5 | 设计承诺缺失（UX 偏差） | `app.py:374`（Tab2 纯文本日志）, `app.py:394-401`（Tab3 下拉）vs `ARCHITECTURE.md:88,166-185,194-199` | **Medium** | **日志行内嵌 ⏯🔄 试听/重合成按钮的承诺未达成**。设计说 Tab2 日志每行末尾直接嵌按钮、Tab3 章节列表每行嵌 ⏯🔄；实际 Tab2 是**不可交互**的 `Textbox`，Tab3 用**独立下拉 `e_seg_sel` + 重合成按钮**而非行内按钮。 | 功能上试听/重合成**能用**（经 Tab3 下拉），但交互模型弱于设计，无法在日志里就地操作。属 UX 偏离，非功能断点。 | 若坚持设计，可换 `gr.Dataframe`/自定义组件实现行内操作；或明确「下拉式」为既定方案并在文档中收敛承诺。 |

| 编号 | 类别 | 文件:行 | 严重程度 | 问题描述 | 影响 | 修复建议 |
|---|---|---|---|---|---|---|
| R1 | 潜在隐患（架构） | `app.py:145-173`, `lib/queue.py:15-93` | **High（架构级）** | **Gradio 单体 + 全局可变 `S` + 同步阻塞合成**。`do_synthesis` 是 generator，但循环内 `synthesize_segment` 是同步阻塞推理；取消仅为段间协作式 (`S["cancel"]`)，无法中断单段；无真正异步/队列化、未用多 GPU、多浏览器标签共享同一 `S`。 | 单进程串行、无法并发、无法横向扩展；长任务期间 UI 仅靠 yield 维持，扩展性差。 | 后续演进为 FastAPI 后端 + 任务队列（Celery/RQ）+ 前端（见 Phase 方案）。 |
| R2 | 潜在隐患（依赖） | `launcher.py:11-17`, `lib/audio_pipeline.py:9-10,56` | **Medium** | 依赖检查不完整：`launcher` 仅装 `gradio/pydub`，但 `audio_pipeline` 需要 `scipy/numpy`，导出需要 `ffmpeg`；`ffmpeg` 缺失时 `export_book` 静默回退到 WAV（仅 `logger.warning`），用户不易察觉。 | 新环境首次导出即 `ImportError`；mp3/m4b 失败被吞成 WAV。 | launcher/requirements 补齐 `numpy scipy pydub` 并检测 `ffmpeg`；导出失败时向 UI 显式报错。 |
| R3 | 潜在隐患（可复现） | `ARCHITECTURE.md:249`（引用） | **Low** | `requirements.txt` 被文档引用但**不存在**（Glob 确认）。 | 环境复现靠手猜依赖，CI/协作困难。 | 补 `requirements.txt` 并纳入 `launcher` 安装流程。 |
| R4 | 潜在隐患（数据完整性） | `lib/project_manager.py:191-204` | **Medium** | `project.json` 用 `json.dump` **直接覆盖写**，非原子写；合成中崩溃若发生在写一半，可能损坏状态表。 | 低概率但后果严重：状态表损坏 → 需手动修复。 | 写临时文件再 `os.replace` 原子替换；或加版本/备份。 |
| R5 | 潜在隐患（文档漂移） | `app.py:17-46` vs `DESIGN.md:2.2/2.6` | **Low** | 主题色板与 `DESIGN.md` 不一致：DESIGN 指定 `#0D0D0F/#161618`，app 实际用 `#0A0A0D/#121216`（中性色也偏移）。 | 视觉与规范不符，团队协作时易困惑。 | 收敛到 DESIGN 指定值，或更新 DESIGN 说明实际取值。 |
| R6 | 潜在隐患（文档漂移） | `ARCHITECTURE.md:281` vs `lib/project_manager.py:28` | **Low** | 文档 `create_project(root, name, script_path) -> dict` 与真实 `create_project(name, script_path) -> str` 不符。 | 接口约定误导维护者。 | 同步文档或补全 `root` 参数与返回结构。 |

---

## 1.3 分类汇总（客观判断）

- **已确认 BUG（会直接导致错误行为）**：B1（Critical，OOM 拆段错位）、B2（语速/多音字丢失）、B3（重合成语速丢失）、B4（比特率丢失）、B5（下拉重复输出）、B6（每段落全量重写）、B7（缓存键过粗致情感编辑失效）、B8（拼接无间隔）、B9（test_voice 错位且死）、B12（校验函数从不调用）。
- **设计承诺缺失（文档/规范承诺但未实现）**：D1（LUFS 归一化）、D2（ID3/章节标签）、D3（均衡）、D4（每角色试音按钮）、D5（日志行内 ⏯🔄 按钮）。
- **潜在隐患（稳健性/架构/依赖）**：R1（单体 + 同步阻塞 + 无并发/多 GPU）、R2（依赖与 ffmpeg 检查不全）、R3（无 requirements）、R4（非原子写）、R5/R6（文档漂移）。

> **重要事实核对**：`更新日志.txt:37` 声称「speech_rate 参数通过 `**generation_kwargs` 透传 IndexTTS2 底层」——经代码核对为**不实陈述**：`tts_engine.synthesize_segment` 根本没有 `speech_rate` 形参，也未向 `_tts.infer` 透传任何语速参数；`queue.py` 与 `regenerate_segment` 也均未传递。即该能力**并未实现**，但被更新日志「宣称已完成」。建议把更新日志与代码状态对齐，避免误导验收。

---

# 块二：升级方案（可落地）

## 2.1 现状与瓶颈总结

- **能用的**：项目 CRUD、音色绑定与音色库、段级文件缓存与断点续跑、逐段合成并能单段重合成、基础 mp3/m4b/wav 导出（仅拼接+转码）。
- **坏掉的/缺失的（直接拖慢交付质量）**：OOM 自动拆段（核心恢复机制失效，B1）、语速/多音字（B2/B3）、比特率透传（B4）、响度归一化（D1）、标签与章节（D2）、均衡（D3）、每角色试音（D4）、行内试听交互（D5）、非法剧本校验（B12）。
- **架构瓶颈**：`app.py` Gradio 单体把 UI/编排/状态/业务揉在一起，无 service 层、无测试；合成是单进程同步阻塞，无法并发也无法多 GPU；`project.json` 每段落全量重写（B6）、非原子写（R4）。

**结论**：当前系统是一个「能跑通主链路 demo，但核心 TTS 质量能力与健壮恢复能力尚未真正落地」的原型。短期应优先修补 Critical/High 的功能性缺陷（这些大多可在现有架构内增量修复），中期再对架构做解耦与异步化演进。

---

## 2.2 升级方向建议

1. **UI 演进**：从 Gradio 单体演进为 **FastAPI 后端 + 现代前端（React/Vue 或 Gradio 仅作内部调试）**，把业务编排与状态管理移出 UI 层，形成可测试的 service 层。
2. **合成任务异步化 / 队列化**：引入任务队列（RQ/Celery/asyncio worker），合成任务后台跑，前端轮询/WebSocket 推进度；支持超时/取消/重试与并发段合成；为多 GPU 预留 worker 池。
3. **响度归一化与元数据补全**：补齐 LUFS -16、ID3/章节标签、可选均衡，使成品达到「可发布有声书」水准（D1/D2/D3）。
4. **多音字 / 语速真正落地**：打通 `Segment → queue/regenerate → tts_engine → IndexTTS2` 的全链路参数透传，并修正缓存键以纳入这些参数（B2/B3/B7）。
5. **批量并发与多 GPU**：在异步化基础上，按 GPU 数量并行合成不同段/章，显著缩短长书合成时长。
6. **项目级音频预览 / 试听体验**：重建「每角色试音」+「日志行内 / 章节树行内 试听-重合成」的交互（D4/D5），提升早期纠错效率。
7. **测试体系**：补 `pytest` 单测（tts_engine 参数透传、audio_pipeline 拼接/标签、project_manager 状态机、script_loader 校验），并加 CI。

---

## 2.3 分阶段路线图

> 工作量：S≈1-2 天，M≈3-5 天，L≈1-2 周。括号内为是否需重构。

### Phase 0 — 质量基线（M，纯增量，无需重构）
- **目标**：对齐文档与代码，补依赖与测试骨架，消除「假宣称」。
- **关键改动**：补 `requirements.txt` 与 `launcher` 依赖/ffmpeg 检测（R2/R3）；`git` 忽略项梳理；加最小 `pytest` 骨架与 3 个核心单测（参数透传、拼接、状态机）。
- **验收**：`pip install -r requirements.txt` 后 `pytest` 绿；更新日志与代码状态对齐（移除 speech_rate 不实声明）。

### Phase 1 — 修 Critical/High 功能性缺陷（M，纯增量，现有架构内）
- **目标**：让已承诺的核心能力真正可用。
- **关键改动**：
  - B1 OOM 拆段参数修正 + 两段拼接回写（Critical）。
  - B2/B3 全链路透传 `speech_rate`/`pinyin_hints`（改 `tts_engine` 签名 + `_tts.infer` 转发 + `queue.py`/`regenerate_segment` 传参）。
  - B4 比特率透传；B5 下拉重复输出修复；B9 `test_voice` 修正。
  - B12 在 `create_project/open_project` 接入 `validate_script`。
- **验收**：长段 OOM 可自动拆段成功；改语速/多音字后重合成声响变化；比特率生效；非法 JSON 在导入即报错。

### Phase 2 — 音频后处理补全（M，纯增量）
- **目标**：成品达到可发布质量。
- **关键改动**：D1 LUFS -16（ffmpeg `loudnorm` 或 `pyloudnorm`）；D2 写 ID3（mp3）/章节（m4b）；D3 可选轻量均衡；B8 段/章间静音与按章拆分。
- **验收**：导出 mp3 带标题/作者/封面标签，响度统一；m4b 可在播放器按章跳转；章节间有自然停顿。

### Phase 3 — 交互体验修正（S-M，纯增量）
- **目标**：兑现「早期试听、就地操作」承诺。
- **关键改动**：D4 每角色试音按钮（基于修好的 `test_voice`）；D5 行内试听/重合成（或明确采用「Tab3 下拉」方案并收敛文档）；B10 `e_voice` 保存后刷新；B7 缓存键纳入情感/语速参数。
- **验收**：Tab1 可一键试音三句；改情感重跑批量能生效；导出页换音色下拉实时刷新。

### Phase 4 — 架构解耦：Service 层 + 异步队列（L，需较大重构）
- **目标**：可测试、可并发、可扩展。
- **关键改动**：抽 `services/` 层（ProjectService、SynthesisService、ExportService），把 `S` 全局态移入请求/会话态；合成改 asyncio/RQ worker，前端（暂保留 Gradio 或引入轻前端）轮询进度；取消可中断单段（协作点细化）；多 GPU worker 池。
- **验收**：单测覆盖 service 层；可并发合成多段；关页不丢进度；单测覆盖率 ≥ 60%。

### Phase 5 — 生产化与多 GPU 规模化（L，需重构）
- **目标**：长书小时级交付。
- **关键改动**：R4 原子写 + 状态表增量更新（消除 B6 的 O(n²) 写放大）；多 GPU 分章并行；断点续跑增强（失败段优先、幂等重跑）；监控/日志/资源看板；可选面向用户的 React 前端替换 Gradio。
- **验收**：千段级工程可在合理时长内稳定跑完；状态表写入为常数级；崩溃可无损恢复。

---

## 2.4 增量修复 vs 较大重构 的边界（给主理人的决策参考）

- **可在现有 Gradio 架构内直接增量修复（无需重构，优先做）**：B1、B2、B3、B4、B5、B7、B8、B9、B10、B12、D1、D2、D3、D4、D5、R2、R3。这些只改 `lib/` 内部逻辑与 `app.py` 少量事件接线，风险低、收益高，建议 Phase 0~3 一气呵成。
- **需要较大重构（建议 Phase 4~5 再动）**：R1（单体→后端+队列）、`app.py` 全局态与 service 层抽取、B6/R4 的状态表写入模型（与重构一并解决更划算）、多 GPU 并发。这些改动牵一发动全身，应在功能性缺陷先清零、测试骨架就位后再启动，避免「边修边重构」放大风险。

---

*评审结束。所有引用均来自对被评审仓库的实际阅读；未运行代码（无 GPU/依赖环境），功能性 BUG（B1/B2/B3/B4/B9）已通过逐行核对调用链与函数签名确认，置信度高。*
