# 有声书合成工作台 (Audiobook Studio)

本地化的**有声书合成工作台**：把一个结构化剧本 `structured_script.json`（角色 / 章节 / 段落，含情感标注）通过 [IndexTTS2](https://github.com/index-tts/index-tts) 逐段合成、拼接、响度归一、转码，最终导出整本有声书（mp3 / m4b）。基于 Gradio 的图形界面，本地运行、GPU 推理。

> 设计理念：**文本分析在前，机械合成在后**。角色识别、情感标注、多音字处理由 WorkBuddy 对话完成并产出 `structured_script.json`；本工作台只负责「加载 JSON + 绑定音色 → 调 IndexTTS2 → 拼接导出」。音色选择权完全归用户。

---

## 核心能力（7 个导航分区）

| 分区 | 职责 |
|------|------|
| 概览 | 项目书架 / 新建 / 打开项目的总入口 |
| 项目 | 上传 `structured_script.json` 创建项目、项目管理 |
| 音色资产 | 为每个角色绑定 / 录制 / 克隆参考音频（`voice_bindings.json`） |
| 合成 | 整本书逐段队列合成，段落级 VRAM 管理、暂停 / 恢复 |
| **补录合成** ⭐新增 | 打开项目后，给某个**已绑定音色**的角色单独补几句 / 补几段并**独立导出音频**，无需重传整本书（覆盖「缺音重合成」与「新增内容」两类场景） |
| 试听与质检 | 段落级试听、按参数重合成 |
| 导出 | 章节拼接 → 均衡 → LUFS 归一 → 转码 mp3 / m4b |

---

## 目录结构

```
audiobook-studio/
├── app.py                  # Gradio 入口：7 个导航分区 + 全部 handler
├── launcher.py             # start.bat 调用：打印中文提示后启动 app.py
├── start.bat               # Windows 启动器（纯 ASCII，避免中文乱码）
├── requirements.txt        # Python 依赖
├── ARCHITECTURE.md         # 总体架构 + 数据模型（structured_script.json 规范）
├── DESIGN.md / brand-spec.md  # UI 设计稿与品牌规范
├── lib/                    # 核心库（无 Gradio 依赖，可单测）
│   ├── script_loader.py    # 读取 / 校验 structured_script.json（含诊断式报错 + 别名容错）
│   ├── types.py            # Script / VoiceInfo / Chapter / Segment 数据类
│   ├── tts_engine.py       # IndexTTS2 单例封装：init_engine / synthesize_segment / empty_cache（全局 RLock 互斥）
│   ├── audio_pipeline.py   # 拼接 + 静音 + 响度归一 + ffmpeg 转码；新增 export_supplement
│   ├── queue.py            # 整本逐段串行合成、段状态表、缓存
│   ├── project_manager.py  # 项目 / 角色下拉构造；新增 build_bound_role_choices
│   ├── segment_cache.py    # 段级参数感知缓存
│   ├── config.py           # 数据目录 / 预览目录
│   ├── postprocess.py      # 响度归一（pyloudnorm）/ 均衡
│   ├── metadata.py         # mp3/m4b 标签
│   └── voice_lib.py / dataframe_style.py / progress.py / exceptions.py
├── services/               # 业务编排层（无 Gradio 依赖）
│   ├── project.py          # ProjectService：创建 / 打开项目、绑定音色
│   ├── session.py          # SessionState（ss.project / ss.script / ss.bindings）
│   ├── supplement.py       # ⭐ SupplementService：补录合成编排（拆句 / 校验 / 合成 / 导出路径）
│   ├── synthesis.py        # 整本合成编排
│   └── export.py           # 整本导出编排
├── docs/                   # 设计文档 + Mermaid 图
│   ├── system_design.md    # 完整系统设计
│   ├── class-diagram.mermaid / sequence-diagram.mermaid
│   ├── PRD_补录合成.md      # ⭐ 补录合成功能 PRD（见下）
│   └── 设计_补录合成.md      # ⭐ 补录合成功能系统设计（见下）
├── tests/                  # pytest 套件（monkeypatch 桩，无需 GPU）
├── prototype/              # 早期原型
├── workspace/              # ⚠️ 运行时用户数据（不入库：项目 / 合成 wav / 配置）
└── voice_library/          # ⚠️ 用户参考音频（不入库）
```

---

## 快速开始

**环境前提**
- Python 3.10（项目自带 `index-tts\.venv` 虚拟环境，含 torch / gradio / indextts）
- 已下载 IndexTTS2 模型（默认路径 `C:\Users\rakliang\WorkBuddy\2026-06-28-19-01-02\index-tts`）
- `ffmpeg` 系统二进制（用于 mp3 / m4b 转码；**缺失时导出 mp3 / m4b 会显式报错**，已生成的中间 WAV 仍保留，可改选 WAV 格式导出）
- 安装依赖：`pip install -r requirements.txt`

**启动**
```bash
# 方式一：Windows 双击
start.bat
# 方式二：直接运行（注意 -u 让 Gradio 日志实时输出）
python -u app.py
# 默认监听 http://0.0.0.0:7862
```

**典型工作流**
1. 「项目」分区上传由 WorkBuddy 生成的 `structured_script.json` → 创建项目。
2. 「音色资产」分区为每个角色绑定参考音频。
3. 「合成」分区启动整本逐段合成（支持暂停 / 恢复、段落级 VRAM 管理）。
4. 「试听与质检」分区逐段试听 / 重合成。
5. 「导出」分区拼接导出整本 mp3 / m4b。
6. **「补录合成」分区**：打开项目后，选角色 + 粘贴几句 / 传小 JSON → 独立合成并导出音频（详见 `docs/PRD_补录合成.md`）。

---

## 架构概览（数据流）

```
structured_script.json
   │  lib/script_loader.from_dict + validate_script（诊断式校验 + 别名容错）
   ▼
ProjectService.open_project → (meta, script, voice_bindings)
   │  voice_bindings["bindings"][role] = 参考音频路径（音色唯一真相源）
   ▼
整本合成：lib/queue → 逐段 lib/tts_engine.synthesize_segment（全局 RLock 单例，互斥）
   │           段级 VRAM 管理（段间 CUDA cleanup + OOM 自动拆段降级）
   ▼
lib/audio_pipeline.concat_for_preview / export_book
   │  拼接 + SEG_SILENCE_SEC 静音 + int16 归一 + normalize_loudness(LUFS) + ffmpeg 转码
   ▼
导出 mp3 / m4b（best-effort 写标签）

补录合成（独立路径，不进整本拼接）：
   选角色 → 粘贴文本 / 上传小 JSON → SupplementService.synthesize_lines
        → 逐段 tts_engine.synthesize_segment（同单例，自动互斥）
        → audio_pipeline.export_supplement（拼接 + LUFS + 转码 → 独立文件）
        → app._safe_path_for_file_component 落盘（Gradio allowed_paths 内）供 gr.File 下载
```

**关键约束**
- `tts_engine` 的 `init_engine` / `synthesize_segment` 整体包裹在模块级 `threading.RLock`（`_ENGINE_LOCK`）内，所有调用方（整本 / 整本重合成 / 补录）自动串行互斥，无需在 handler 层再加锁；用 `RLock` 是因其 OOM 时会递归调用自身。
- 导出文件必须经 `app._safe_path_for_file_component` 落盘到 `config.get_data_dir()` 子树（已在 `app.launch(allowed_paths=[...])` 放行），否则 `gr.File` 报 `InvalidPathError`。
- 补录中间产物隔离在 `supplement_cache/`，**不写** `segments/` 与 `project.json`，与整本断点续跑状态相互独立。

---

## 补录合成（新增功能）

针对「只给某个角色补几句 / 补几段」的高频诉求：无需重传整本书，打开项目后选已绑定音色角色，粘贴台词或上传小 JSON，用该角色音色独立合成并导出音频。

- 产品需求（PRD）：[docs/PRD_补录合成.md](docs/PRD_补录合成.md)
- 系统设计：[docs/设计_补录合成.md](docs/设计_补录合成.md)

小 JSON 最小格式：
```json
{
  "role": "旁白",
  "lines": [
    {"text": "你放心，我自有道理。", "emotion": "sad", "emo_alpha": 1.0, "speech_rate": 1.0},
    {"text": "这可奇了。"}
  ]
}
```
`role` 必须命中项目 `voices`；缺 `text` 的段落会被前置诊断拦截，不再误报成功。

---

## 测试

纯 Python + `lib` 单测，**无需 GPU / 模型**（用 `monkeypatch` 把 `tts_engine.synthesize_segment` 替换成写哑 wav 的桩）：
```bash
# 用项目自带 venv
index-tts\.venv\Scripts\python.exe -m pytest tests/ -q
```
重点套件：`test_supplement.py`（补录编排 + 引擎锁 + export_supplement）、`test_supplement_wiring.py`（导航接线回归）、`test_script_loader_diagnostics.py`（校验诊断）、`test_project_service.py` / `test_project_manager.py`。

---

## 文档索引

| 文档 | 内容 |
|------|------|
| `ARCHITECTURE.md` | 总体架构、`structured_script.json` 规范、ADR 决策记录 |
| `DESIGN.md` / `brand-spec.md` | UI 设计稿与品牌规范（浅色 Stripe 风） |
| `docs/system_design.md` | 完整系统设计 + Mermaid 类图 / 时序图 |
| `docs/PRD_补录合成.md` | 补录合成功能 PRD |
| `docs/设计_补录合成.md` | 补录合成功能系统设计 |
| `docs/PRD_UI_Redesign.md` | UI 浅色重设计 PRD |
| `增量设计_O3队列列表+O12暂停恢复.md` 等 | 各增量迭代的设计文档 |
| `调研报告_*.md` | 功能 / 前端结构对比、开源方案调研 |

---

## 不在仓库中的内容（已被 .gitignore 排除）

- `workspace/`：用户项目数据 + 合成产物（约 300M wav），含 `projects/<书名>/structured_script.json`、`segments/`、`output/`
- `config.json`：含数据目录绝对路径的本地配置
- `voice_library/`：用户参考音频
- `*.bak*`：工程师临时备份（含 `app.py.bak-<时间戳>` 这类后缀变体）
- `*.log` / `tests/run_log.txt`：运行日志
- `*.wav`：合成音频

> 仓库只保留**源码 + 文档 + 示例 JSON + 测试**，便于他人克隆后基于 WorkBuddy 产出的剧本快速复现。
