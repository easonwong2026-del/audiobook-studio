# Audiobook Studio — 系统架构（v3.3.0）

> 本文档与代码同步维护。若发现与实现不符之处，以 `docs/system_design.md` 与源码为准。

## 1. 定位

基于 **Gradio + IndexTTS2** 的本地有声书合成工作台。本仓库是**轻量工作台源码**，
推理依赖（IndexTTS2 / Torch / CUDA / 模型权重 / FFmpeg）由用户本地环境单独准备，**不随仓库发货**、不打包进安装程序。

- 界面：Gradio（Blocks，默认端口 **7862**）
- 推理：IndexTTS2（经同级 `index-tts` 的 venv 提供 torch / CUDA）
- 音频后处理：numpy / scipy / pyloudnorm / pydub / mutagen（纯 CPU）
- 运行时：Python 3.10

## 2. 分层架构

```
app.py                      # Gradio 接线 + 导航（不含导演业务实现）
├── ai/providers/           # AI Provider 抽象：Local / OpenAI / DeepSeek
├── ui/                     # UI 页面、组件与轻量事件编排
│   └── settings_handlers   # 设置 UI 回调，避免 app.py 继续膨胀
├── services/               # 业务服务层
│   ├── script_director     # 分析、规范化、分章编辑与历史快照
│   └── voice_director      # 音色推荐、试听与反馈
├── repositories/           # 持久化边界（Repository Pattern，原子 JSON 写）
├── lib/                    # 领域工具
│   ├── tts_engine          # IndexTTS2 推理封装（函数内 lazy import torch / indextts）
│   ├── audio_pipeline      # 段拼接 / 导出 / ffmpeg 转码（参数透传，失败明确报错）
│   ├── audio_format        # WAV 加载 / 重采样 / 声道 / dtype 归一
│   ├── postprocess         # D1 响度 LUFS 归一 + D3 人声均衡（纯 CPU）
│   ├── config / environment# 配置与运行环境探测
│   ├── snapshot            # ProjectSnapshot 构建
│   ├── text_importer       # TXT / DOCX / EPUB 安全导入
│   ├── directed_synthesis  # 导演停顿进入试听与正式生产
│   ├── script_loader / segment_cache / voice_lib / progress
│   ├── dataframe_style / logging_setup / exceptions
├── domain/                 # 领域类型（如 results.py）
tests/                      # 纯 Python 单元测试（无 GPU / 无模型 / 无真实推理）
docs/                       # 设计文档（system_design.md 等）
```

### 关键设计点

- **Repository 持久化层**：`repositories/` 是唯一磁盘 JSON 边界；所有写入使用
  `临时文件 + f.flush() + os.fsync + os.replace` 原子替换，避免写中断损坏。
- **ProjectSnapshot**：打开项目时构建的内存快照，聚合 `meta / script / voice_bindings`，
  避免合成期反复读盘。
- **统一日志系统**：`lib/logging_setup.py` 集中配置；错误明确向上传递，禁止
  `except Exception: pass` 式吞异常。
- **解释器解析（launcher.py）**：优先 `AUDIOBOOK_STUDIO_PYTHON` 环境变量 →
  仓库同级 `index-tts/.venv` → PATH 中的 `python`，不绑定个人绝对路径。
- **数据目录外置**：经 `AUDIOBOOK_STUDIO_DATA_DIR` 环境变量或 `config.json` 配置，
  项目与产物与程序目录分离。
- **长篇剧本按章编辑**：导演台只向浏览器传输当前章节，不再序列化整本小说；保存使用
  流式 JSON 原子替换，规范化过程只复制实际输出字段，降低长篇项目的峰值内存。
- **AI Provider 隔离**：OpenAI、DeepSeek 和离线 Local Provider 共享统一接口；
  远程模型按章节/语义批次分析，Provider 不侵入 UI 与生产链路。

## 3. 测试策略

- 纯 Python 单元测试，CI 在 **Ubuntu + Python 3.10** 运行：
  **不下载模型、不安装 CUDA、不运行真实 GPU 推理、不制作安装包**。
- TTS 引擎按需导入（函数内 `import torch` / `from indextts...`），模块顶层不依赖
  torch，便于以 mock / 哑 WAV 替代真实推理。
- FFmpeg：真实转码由导出测试按需调用；**缺失时相关用例 `skip`**，其余导出用例用
  `monkeypatch` 验证参数（比特率 / 编码器 / 后处理链顺序）透传。
- 界面交互以 AST 静态校验 / 桩函数验证接线，不启动浏览器。
- 平台相关（如 `start.bat` 字节完整性）以静态文件检查覆盖，跨平台可执行。

## 4. 交互收敛说明（D5）

原 Tab2 日志行内嵌试听 / 重合成按钮（⏯ 🔄）的承诺，已收敛为
**Tab3 段落下拉式试听 / 重合成（功能等价）**，消除文档漂移。日志区域保持只读 Textbox。
