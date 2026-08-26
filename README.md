# Audiobook Studio

Audiobook Studio 是一个本地有声书制作工作台。它不理解小说原文，也不在工作台内
调用模型；外部 Agent / Skill 先生成一个 `structured_script.json`，工作台再完成：

```text
外部 Agent + 有声书分析 Skill
        ↓
structured_script.json
        ↓
Audiobook Studio 导入与校验
        ↓
角色与声音 → 合成 → 试听质检 → WAV / MP3 / M4B / 字幕导出
```

## 快速开始

当前基线版本：`v3.3.3`（本地开发线，不代表本轮正式 Release）。

1. 使用外部 Agent 生成结构化剧本 JSON。
2. 先运行离线检查：

   ```bash
   python tools/validate_structured_script.py path/to/structured_script.json
   ```

3. 启动工作台：

   ```bash
   python launcher.py
   ```

4. 打开「新建项目」，上传 JSON，查看预览和 warning/error。
5. 创建后进入「角色与声音」，绑定参考音频，再进入生产、质检和交付。

`python tools/validate_structured_script.py` 与 UI 复用同一个
`StructuredScriptImportService`，不访问网络。

## JSON 入口

唯一项目交换文件是 `structured_script.json`，规范结构为：

```json
{
  "meta": {"title": "作品名", "author": "作者"},
  "voices": {
    "旁白": {"description": "沉稳叙事"}
  },
  "chapters": [
    {
      "id": 1,
      "title": "第一章",
      "segments": [
        {"id": "1-001", "role": "旁白", "text": "正文", "emotion": "neutral"}
      ]
    }
  ]
}
```

完整协议、字段范围、拒绝条件和 warning 条件见
[`docs/structured_script_contract.md`](docs/structured_script_contract.md)，合法示例见
[`tests/fixtures/structured_script_valid.json`](tests/fixtures/structured_script_valid.json)。

导入流程使用：

```text
StructuredScriptImportService.inspect()
  → lib.script_loader.load_script()
  → lib.script_loader.validate_script()
  → services.script_consistency.check_script_consistency()
  → 预览（不创建项目）

StructuredScriptImportService.create()
  → 再次 inspect
  → StructuredScriptImportService._assert_slot_available()
  → ProjectRepository.create_project()
```

创建使用临时目录和原子替换。已有合法、Legacy、不完整、临时或损坏目录都不会被
覆盖或自动删除。

## 保留的本地工作流

- V3 项目扫描、打开、删除和目录管理；
- `project.json`、`structured_script.json`、`voice_bindings.json`；
- 角色列表、音色筛选、参考音频上传/录制、绑定和保存；
- 本地 IndexTTS 2 Legacy / IndexTTS 2.5 双引擎合成、暂停、恢复、断点续跑和引擎隔离的段级缓存；
- 章节试听、段落试听、重合成、角色补录和章节拼接；
- WAV、MP3、M4B 章节标签及字幕导出；
- 数据目录、TTS/FFmpeg 诊断、异常项目回收站。

工作台不提供原始 TXT / DOCX / EPUB 分析入口，不保存或管理模型服务配置。

## 目录和数据

```text
lib/                         V3 数据类型、JSON 读取、队列、TTS、导出
repositories/                项目、绑定、配置仓库
services/                    JSON 导入、项目、合成、补录、导出、诊断
ui/                          Gradio 页面、回调和事件接线
docs/structured_script_contract.md
tools/validate_structured_script.py
```

默认项目数据位于 `~/AudiobookStudio/`，可用 `AUDIOBOOK_STUDIO_DATA_DIR` 或设置页
切换。程序仍能读取旧版项目目录，但新项目只写入外置数据目录。

支持的本地环境变量：

| 变量 | 用途 |
| --- | --- |
| `AUDIOBOOK_STUDIO_DATA_DIR` | 项目、音色库和产物根目录 |
| `AUDIOBOOK_STUDIO_LEGACY_DIR` | 旧版项目只读兼容目录 |
| `AUDIOBOOK_STUDIO_ENGINE_VERSION` | 选择 `2` 或 `2.5`（旧别名 `AUDIOBOOK_STUDIO_VERSION`） |
| `AUDIOBOOK_STUDIO_MODEL_DIR_V2` | IndexTTS 2 Legacy 模型目录 |
| `AUDIOBOOK_STUDIO_MODEL_DIR_V25` | IndexTTS 2.5 模型目录 |
| `AUDIOBOOK_STUDIO_MODEL_DIR` | Legacy `model_dir` 兼容别名 |
| `AUDIOBOOK_STUDIO_PYTHON` | IndexTTS Python 解释器 |
| `AUDIOBOOK_STUDIO_FFMPEG` | FFmpeg 可执行文件 |

用户数据目录不会被本仓库的启动、导入或诊断流程批量迁移、转换或清理。

## 开发验证

```bash
python -m compileall -q .
git diff --check
python -m pytest -q
```

本地开发分支以 V3.3.3 基线提交为准；本轮不创建正式版本、Tag 或 Release。
