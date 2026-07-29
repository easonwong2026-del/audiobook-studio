# 有声书合成工作台 (Audiobook Studio) · v3.3.1

[![Tests](https://github.com/easonwong2026-del/audiobook-studio/actions/workflows/tests.yml/badge.svg)](https://github.com/easonwong2026-del/audiobook-studio/actions/workflows/tests.yml)

**AI 驱动的本地有声书制作工作台。** 上传 TXT、DOCX 或 EPUB 后，内置 AI
剧本导演完成角色识别、段落拆分、情绪与停顿设计；经人工校正后直接创建项目，
再通过本地 IndexTTS2 完成音色绑定、合成、质检和 mp3 / m4b / wav / 字幕导出。
也可导入既有 `structured_script.json`，兼容原有 v2 项目和高级工作流。

---

## 版本与定位

| 项 | 说明 |
|----|------|
| 当前版本 | **v3.3.1**（AI 剧本导演工作流重构） |
| 产品定位 | **AI 驱动的本地有声书制作工作台** |
| 是否独立安装软件 | **否** —— 不提供 Windows 安装包，也不内置模型 / Torch / CUDA / FFmpeg / IndexTTS2 本体 |
| 部署方式 | **轻量工作台源码 + 外部推理环境**（IndexTTS2 仓库及其虚拟环境由用户单独准备） |

> 模型、Torch、CUDA、Python 虚拟环境、FFmpeg 等体积可能达到十几 GB 甚至几十 GB，因此一律作为**外部依赖**由用户准备，不打包进本仓库。详见下文「[为什么不提供一键安装包](#为什么不提供一键安装包)」。

---

## 制作流程

默认流程：

```text
上传 TXT / DOCX / EPUB
→ AI 剧本导演分析
→ 人工校正
→ 直接创建项目
→ 绑定音色
→ 合成
→ 质检
→ 导出
```

高级兼容流程：

```text
上传已有 structured_script.json
→ 创建项目
```

## 核心能力

| 分区 | 职责 |
|------|------|
| 工作台 | 当前项目、章节进度、角色绑定、最近任务和待处理问题 |
| 项目创建 | TXT / DOCX / EPUB 导入、AI 剧本导演、人工校正、原子创建；高级入口支持 `structured_script.json` |
| 角色与声音 | 按“选择角色 → 选择声音 → 试听 → 确认绑定”配置 `voice_bindings.json` |
| 生产与质检 | 内含合成中心、试听质检、角色补录；支持队列、暂停 / 恢复、断点续跑 |
| 交付 | 章节拼接 → 均衡 → LUFS 归一 → 转码 mp3 / m4b / wav，并生成字幕 |

---

## 目录结构

```text
audiobook-studio/
├── app.py                  # Gradio 应用入口、事件接线及部分 UI callback 编排
├── launcher.py             # 启动器：自动查找 Python 解释器
├── start.bat               # Windows 双击启动入口
├── config.json             # 本地配置（含数据目录路径，已 .gitignore）
├── requirements.txt        # 运行时依赖
├── requirements-dev.txt    # 测试 / CI 依赖（不含 torch）
├── structured_script.example.json # 项目创建页可下载的最小示例
├── CHANGELOG.md            # 变更记录
├── ARCHITECTURE.md         # 系统架构
├── lib/                    # 领域工具
│   ├── __init__.py         # 唯一版本来源（__version__）
│   ├── tts_engine.py       # IndexTTS2 推理封装（函数内 lazy import torch）
│   ├── audio_pipeline.py   # 段拼接 / 导出 / ffmpeg 转码
│   ├── audio_format.py     # WAV 加载 / 重采样 / 声道 / dtype 归一
│   ├── postprocess.py      # LUFS 响度归一 + 人声均衡
│   ├── config.py           # 配置（环境变量 / config.json / 默认）
│   ├── environment.py      # 运行环境探测
│   ├── snapshot.py         # ProjectSnapshot
│   ├── script_loader.py    # JSON 剧本加载
│   ├── segment_cache.py    # 合成段缓存
│   ├── voice_lib.py        # 音色库管理
│   ├── progress.py         # 进度跟踪
│   ├── logging_setup.py    # 日志系统（自动轮转）
│   ├── project_manager.py  # 项目管理
│   ├── queue.py            # 合成队列
│   ├── metadata.py         # 音频元数据
│   ├── exceptions.py       # 异常定义
│   └── types.py            # 类型定义
├── repositories/           # 持久化层（Repository + 原子 JSON 写入）
├── services/               # 业务服务、环境诊断、剧本一致性检查
├── scripts/                # 参数化真实环境验收工具
├── ui/                     # UI 页面与 ui/wiring/* 事件接线
├── domain/                 # 领域类型
├── tests/                  # 测试
├── docs/                   # 设计文档
│   └── releases/v3.3.1.md  # 当前版本发布与验收说明
└── 更新日志.txt             # 中文变更日志
```

> 注：`workspace/`（旧版项目数据）与 `voice_library/`（用户参考音频）属于本地 / 历史数据，已被 `.gitignore` 排除，**不入库**（见文末「不在仓库中的内容」）。V3.1 起项目与合成产物默认外置到**数据目录**（见「[如何切换数据目录](#如何切换数据目录)」）。

---

## 环境要求

### 推荐部署方式

```text
workspace-root/
├── audiobook-studio/      # 本仓库
└── index-tts/             # IndexTTS2 主项目
    └── .venv/             # 虚拟环境（Python 3.10 + torch + CUDA + TTS 推理依赖）
```

### 各组件详解

- **操作系统**：Windows 10 / 11（推荐）。macOS / Linux 可运行 Gradio 界面，但 `start.bat`、`os.startfile()` 和 IndexTTS2 的 Windows 倾向可能影响体验。
- **Python 版本**：由 IndexTTS2 虚拟环境提供。项目随附的 `index-tts/.venv` 使用 Python 3.10；请以 **IndexTTS2 官方要求的 Python 版本**为准。
- **IndexTTS2 模型**：需从 IndexTTS2 官方渠道获取其模型 checkpoint，自行放置到 `index-tts/checkpoints/` 或 `AUDIOBOOK_STUDIO_MODEL_DIR` 指向的位置。
- **GPU**：推荐 **NVIDIA GPU 12 GB+ VRAM**。CUDA、cuDNN 由 IndexTTS2 的虚拟环境负责。
- **Torch**：由 IndexTTS2 的虚拟环境安装（GPU 版本），不通过本仓库的 pip 安装。
- **FFmpeg**：导出 mp3 / m4b 需要 FFmpeg（系统级二进制，**不是** pip 包）。可选设置 `AUDIOBOOK_STUDIO_FFMPEG` 环境变量指向自定义路径；缺失时 launcher 会显式警告（可改用 WAV 导出）。
- **本仓库 Python 依赖**：`requirements.txt`（Gradio 5.50–5.x、numpy、scipy、pyloudnorm、mutagen）—— 这些**不依赖 GPU / CUDA**，使用 Python 3.10+ 安装。

### `AUDIOBOOK_STUDIO_PYTHON` 环境变量

`launcher.py` 按以下优先级查找 Python 解释器：

1. **优先使用环境变量 `AUDIOBOOK_STUDIO_PYTHON`**（若已设置）。
2. 否则检查仓库**同级目录**下的 `index-tts/.venv`（相对路径，仓库可整体移动）。
3. 最后回退到系统 `PATH` 中的 `python`。

```bash
# Windows cmd
set AUDIOBOOK_STUDIO_PYTHON=D:\path\to\index-tts\.venv\Scripts\python.exe

# Windows PowerShell
$env:AUDIOBOOK_STUDIO_PYTHON = "D:\path\to\index-tts\.venv\Scripts\python.exe"

# macOS / Linux
export AUDIOBOOK_STUDIO_PYTHON=/path/to/index-tts/.venv/bin/python
```

### 环境变量总表

| 变量 | 用途 | 默认值 |
|------|------|--------|
| `AUDIOBOOK_STUDIO_PYTHON` | Python 解释器（启动本工作台） | 仓库同级 `index-tts/.venv` → PATH 中的 `python` |
| `AUDIOBOOK_STUDIO_MODEL_DIR` | IndexTTS2 模型 checkpoint 位置 | 仓库同级 `index-tts/checkpoints` |
| `AUDIOBOOK_STUDIO_DATA_DIR` | 外置数据目录（项目 / 合成产物 / 音色库） | `~/AudiobookStudio` |
| `AUDIOBOOK_STUDIO_FFMPEG` | FFmpeg 二进制路径 | 搜索 `PATH` |
| `AUDIOBOOK_STUDIO_LEGACY_DIR` | 旧版数据目录，迁移用 | 无 |
| `OPENAI_API_KEY` | OpenAI 剧本导演密钥 | 无 |
| `DEEPSEEK_API_KEY` | DeepSeek 剧本导演密钥 | 无 |
| `AUDIOBOOK_STUDIO_OPENAI_MODEL` | OpenAI 导演模型 | `gpt-5.6` |
| `AUDIOBOOK_STUDIO_DEEPSEEK_MODEL` | DeepSeek 导演模型 | `deepseek-v4-pro` |
| `AUDIOBOOK_STUDIO_AI_MAX_INPUT_CHARS` | 远程分析单批最大字符数 | `50000` |

---

## 快速开始

### 1. 克隆仓库

```bash
git clone https://github.com/easonwong2026-del/audiobook-studio.git
cd audiobook-studio
```

### 2. 准备 IndexTTS2

从 [IndexTTS2 GitHub 仓库](https://github.com/IndexTeam/IndexTTS2) 克隆，安装依赖并下载模型 checkpoint。推荐与本仓库同级放置：

```text
workspace-root/
├── audiobook-studio/      # 本仓库
└── index-tts/             # IndexTTS2
    ├── .venv/
    └── checkpoints/
```

### 3. 安装本仓库依赖

```bash
pip install -r requirements.txt
```

> `requirements.txt` 不含 torch 或任何推理依赖，可以用系统的默认 Python 安装。

### 4. 配置 Python 解释器

若 `index-tts/.venv` 在仓库同级，可跳过；否则设置 `AUDIOBOOK_STUDIO_PYTHON`（见上）。

### 5. 检查 FFmpeg

```bash
ffmpeg -version
```

若缺失，导出 mp3 / m4b 会显式报错（中间 WAV 仍保留），不影响 WAV 导出。

### 6. 启动应用

```bash
python launcher.py
```

首次启动会依序完成：依赖检查（缺失时自动 pip install），FFmpeg 检测，然后加载 IndexTTS2 模型
（首次约 10–30 秒），最后在 `http://localhost:7862` 打开工作台。

### 7. 创建项目

默认在「新建项目」上传 TXT、DOCX 或 EPUB，选择已配置的 Local / OpenAI /
DeepSeek Provider，完成 AI 分析和人工校正后直接创建项目。远程 Provider 的 API
Key 优先保存在系统 Keyring；报告、日志和前端状态只显示是否配置及来源。

也可在高级区域上传已有 `structured_script.json` 创建项目。

命令行可先把原稿转换为 v3 剧本：

```bash
python script_director_cli.py novel.epub \
  --provider deepseek \
  --title "作品名" \
  --author "作者" \
  -o structured_script.json
```

`--provider` 支持 `local`、`openai` 和 `deepseek`。Local 可离线运行；
OpenAI 和 DeepSeek 分别读取 `OPENAI_API_KEY` 与 `DEEPSEEK_API_KEY`。
产物包含角色、情绪、速度、强度、停顿和呼吸 metadata，并保留现有 TTS 链所需的兼容字段。

也可以直接在工作台使用“AI 剧本导演”。分析完成后可在 Segment 导演表中
人工修改角色、文本、情绪、速度、强度、呼吸和停顿，并可撤销最近一次保存。

导演台还会根据角色描述、主要情绪与音色文件名 / 分类标签生成可解释的声音候选。
推荐结果不会自动绑定；用户选择声音和 segment 后，可以主动生成带情绪、语速、
强度及精确停顿的单段导演试听。相同文本、音色和参数会复用试听缓存。
试听后可以提交“太快 / 太慢、太强 / 太弱、停顿太长 / 太短、呼吸太重 / 不足”
反馈。系统只对当前 segment 做有边界的小步调整，清空旧试听并要求重新生成；
每次反馈都可以通过“撤销上次保存”恢复。

structured_script v3 的内部停顿与前后留白同时进入正式整书合成。v3 段音频已包含
导演设计的时序，因此导出、章节试听和字幕不会再叠加旧版固定静音；v2 项目仍维持
原来的 300ms 段间和 800ms 章间间隔。

### 8. 绑定音色 → 合成 → 质检 → 导出

各页面提供对应功能（见上文「核心能力」表）。

### 9. 运行环境诊断与验收

在「设置 → 系统信息」运行环境诊断；该功能不会安装或加载 CUDA、Torch、模型。
命令行验收示例：

```bash
python scripts/acceptance_check.py --environment
python scripts/acceptance_check.py --project "项目名"
python scripts/acceptance_check.py --provider openai
python scripts/acceptance_check.py --export-check "项目名"
```

Provider 验收默认只检查配置。只有显式追加 `--allow-real-request` 才会向当前
Base URL 发送连接与认证检查；该检查不调用具体模型，也不验证模型推理能力。

---

## 为什么不提供一键安装包

IndexTTS2 模型 + Torch + CUDA 运行时 + Python 虚拟环境的总体积可能达到**十几 GB 甚至几十 GB**，而且 GPU 环境与 CUDA 版本、Torch 版本强相关、因人而异。强行打包会形成：

- 体积过大（十多 GB），下载和分发不现实
- 对已经拥有 IndexTTS2 环境的用户造成大量重复下载和磁盘浪费
- GPU 版本绑定（CUDA 11.x / 12.x / CPU-only）与用户实际环境不匹配

因此**本仓库不提供包含模型或运行环境的 Windows 安装包、AppImage 或 Docker 镜像**。项目采用「**轻量工作台源码 + 外部推理环境**」的分发方式：

- 工作台代码本身只有数百 KB，clone / 更新快速
- 用户维护自己的 IndexTTS2 环境（含模型和 CUDA），按需运行
- 两者只需满足目录约定或环境变量配置即可协作

这是一个有意识的架构选择，而非项目缺陷。

---

## 常见问题

### 找不到 Python 解释器怎么办

检查 `AUDIOBOOK_STUDIO_PYTHON` 环境变量是否设置正确，或确认 `index-tts/.venv` 位于本仓库同级目录。

### 模型放在哪里

由 `AUDIOBOOK_STUDIO_MODEL_DIR` 环境变量控制，默认为本仓库同级 `index-tts/checkpoints`。

### 找不到 FFmpeg 怎么办

安装系统 FFmpeg（`winget install FFmpeg`、`brew install ffmpeg`、`apt install ffmpeg`），或设置 `AUDIOBOOK_STUDIO_FFMPEG` 指向其路径。缺失时仍可启动并导出 WAV。

### 是否支持 TXT、DOCX 或 EPUB

支持。TXT、DOCX、EPUB 是默认项目创建入口；已有结构化剧本可通过高级入口导入。
TXT 支持 UTF-8 和 GB18030；EPUB 按 spine 阅读顺序提取正文。

### 环境诊断会自动安装依赖吗

不会。它只报告数据目录、IndexTTS2、模型、FFmpeg、NVIDIA、Torch/CUDA 和
Provider 配置状态，并给出修复建议。

### 为什么仓库里没有模型和音色文件

模型与音色属于**外部依赖 / 用户资产**，体积大且具隐私性，按设计不入库（见 `.gitignore`）。模型由 IndexTTS2 侧准备；参考音色由用户在「音色资产」分区录制 / 上传 / 克隆，保存在外置数据目录。

### 合成结果和项目文件保存在哪里

默认在用户主目录下的 `~/AudiobookStudio/`（可通过 `AUDIOBOOK_STUDIO_DATA_DIR` 或 `config.json` 修改），在应用 UI 的「设置」分区也可在线切换。

### MP3 或 M4B 导出失败怎么办

确认 FFmpeg 已安装。失败时中间 WAV 文件仍保留在原位（不丢失），可改用 WAV 导出。

### 是否支持无 GPU 环境

Gradio 界面和项目管理可在无 GPU 环境运行，但实际的 TTS 合成需要 CUDA GPU。合成按钮在加载引擎失败时会明确报错，不静默回退。

### 是否提供 Windows 安装包

不提供。原因见上文「[为什么不提供一键安装包](#为什么不提供一键安装包)」。本工作台以源码形式分发，配合用户自备的 IndexTTS2 推理环境使用。

---

## 不在仓库中的内容

以下内容**不在 git 跟踪**中，用户需自行准备 / 配置：

- `index-tts/`：IndexTTS2 项目（含 `.venv` 和模型 checkpoint）—— 外部依赖。
- `config.json`：本地配置（含数据目录绝对路径）—— 已被 `.gitignore` 排除。
- `workspace/`：旧版项目数据 + 合成产物（不入库；V3.1 起默认外置到数据目录）。
- `voice_library/`：用户参考音频 / 克隆音色（不入库）。
- `*.wav`、`*.mp3`、`*.m4b`：运行时音频输出（*.wav 已 gitignore；mp3 / m4b 默认在外置数据目录）。
- `*.log`、`probe_*.txt`：运行时日志 / 探针文件（默认在外置数据目录；本地日志已 gitignore）。
- `backups/`：本地备份归档。
- 模型检查点（checkpoint 文件，GB ~ 几十 GB 级）：由用户从 IndexTTS2 官方渠道获取。

---

## 相关文档

| 文档 | 内容 |
|------|------|
| [`ARCHITECTURE.md`](ARCHITECTURE.md) | V3.2 系统架构和测试策略 |
| [`CHANGELOG.md`](CHANGELOG.md) | 完整变更记录 |
| [`docs/system_design.md`](docs/system_design.md) | 系统详细设计 |
| [`docs/releases/v3.2.1.md`](docs/releases/v3.2.1.md) | GitHub Release 说明 |
| [`icon.png`](icon.png) / [`icon.ico`](icon.ico) | 快捷方式与启动器图标（PNG 256px / ICO 7 种尺寸） |
| [`DESIGN.md`](DESIGN.md) | UI 设计系统（Stripe 浅色招牌风） |

---

*Built with Gradio · IndexTTS2 · Python 3.10*
