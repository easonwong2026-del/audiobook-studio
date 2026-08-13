# IndexTTS v2 / v2.5 双引擎迁移说明

本文说明 Audiobook Studio 对 IndexTTS v2 与 v2.5 的并行诊断约定。当前实现只
负责识别配置、检查本地目录和报告运行时能力；不联网、不下载模型、不加载模型，
也不会自动修改用户配置。

## 1. 目标与边界

工作台可以同时保留两套 checkpoint，诊断会分别检查：

- 当前选中的 engine 与 version；
- v2 与 v2.5 各自的 model dir 是否存在；
- 当前官方/发行包常见顶层结构中的核心文件组（同一角色允许官方常见后缀变体）；
- Python、Torch、CUDA runtime version、GPU 名称与 BF16 capability；
- 选中版本与模型配置中显式声明版本是否匹配；
- 缺少的核心文件名。

这里的 “required” 是 best-effort 检查：不同官方发行包或镜像可能改变权重容器后缀，
因此检查按 checkpoint 角色接受少量常见替代名；不能据此替代引擎实际加载验证。
不在当前目录中的文件不会被搜索，也不会触发下载。

## 2. 双引擎与目录

当前引擎标识为 `indextts`，版本使用 `v2` 或 `v2.5`。建议将两套模型放在相互
独立的目录，例如：

```text
index-tts/
├── checkpoints/        # v2
└── checkpoints-v2.5/   # v2.5
```

诊断对每个版本做本地目录检查。v2 核心文件组包括 `config.yaml`（或 `config.yml`）、
GPT、S2Mel、DVAE、BPE/tokenizer、CampPlus 和 wav2vec2-BERT stats；v2.5 在此基础
上检查 `feat1.pt` 与 `feat2.pt`。同一角色存在一个支持的常见文件名即可。检查是
best-effort，不读取权重内容，也不替代引擎实际加载验证。

## 3. 配置优先级

### 3.1 engine / version

优先级从高到低为：

1. `AUDIOBOOK_STUDIO_ENGINE`、`AUDIOBOOK_STUDIO_ENGINE_VERSION`；
2. `AUDIOBOOK_STUDIO_VERSION` 作为 version 兼容别名；
3. `config.json` 中的 `engine` / `engine_backend` / `tts_engine`、
   `engine_version` / `tts_version` / `version`；
4. 若未明确指定，按本地 sibling 目录做有限自动识别；新安装默认推荐 v2.5，
   只有检测到旧的共享 `model_dir` / Legacy 目录时才保持 v2 回滚兼容。

### 3.2 model dir

版本专用目录优先使用：

- v2：`AUDIOBOOK_STUDIO_MODEL_DIR_V2`；
- v2.5：`AUDIOBOOK_STUDIO_MODEL_DIR_V25`，或兼容别名
  `AUDIOBOOK_STUDIO_MODEL_DIR_2_5`、`AUDIOBOOK_STUDIO_MODEL_DIR_25`、
  `AUDIOBOOK_STUDIO_INDEXTTS25_MODEL_DIR`。

随后读取 `config.json` 中的 `model_dir_v2` / `model_dir_v25`（也接受
`model_dir_v2_5` 等兼容键）。旧配置保持有效：`AUDIOBOOK_STUDIO_MODEL_DIR` 和
`config.json.model_dir` 仍作为 v2 的旧别名。最后才使用 sibling 默认目录。

诊断/UI/MCP 输出只展示类似 `<model-dir>/checkpoints-v2.5` 的末级标签，不返回用户
主目录、仓库目录或 Python 可执行文件的完整绝对路径。内部解析仍保留完整路径供本地
文件检查使用。

## 4. Python 与 Windows 基线

推荐 Python **3.10–3.11**，并为 IndexTTS 使用独立虚拟环境。解释器解析仍兼容：

1. `AUDIOBOOK_STUDIO_PYTHON`；
2. 仓库同级 `index-tts/.venv/Scripts/python.exe`（Windows）或 `.venv/bin/python`；
3. PATH 中的 `python` / `python3`。

Windows RTX 5070 Ti 是待执行的验证画像：应确认 NVIDIA 驱动、Torch CUDA
runtime 与当前驱动兼容。诊断中的 GPU 名称、Torch version、CUDA version、BF16
capability 仅表示能力探针，不等于已完成推理验收；当前分支不宣称已完成真实 GPU
推理验证。

## 5. A/B 验证

迁移建议保留旧 v2 目录，先以 v2 为 A、v2.5 为 B：

1. 固定同一 Python、Torch/CUDA、参考音频、短文本和合成参数；
2. A：明确设置 `AUDIOBOOK_STUDIO_ENGINE_VERSION=v2`，运行环境诊断并完成短段试听；
3. B：只切换到 `v2.5` 及其 model dir，重复同一短段试听；
4. 对比启动、首段延迟、显存峰值、错误日志、音质、语速/停顿和导出结果；
5. 再用五段包含多角色、数字、标点和长文本的样本做小批量回归；
6. 记录诊断报告中的缺失文件、version match 与 GPU 能力，不把“目录齐全”当作
   “模型可用”。

诊断本身不执行 GPU 推理，因此 A/B 的最终结论必须来自真实短段和小批量样本。

## 6. 真实 GPU 验证状态

以下项目在 Windows 11 + RTX 5070 Ti 真机完成前均为 **Pending / NOT VERIFIED**：

- IndexTTS 2 FP16 真实推理与五段 Production；
- IndexTTS 2.5 BF16 真实推理与五段 Production；
- QwenEmotion 与 pronunciation annotation 的真实输出；
- 显存峰值、RTF、章节生产和全书生产。

GPU-free CI 只能验证 profile、adapter 参数、缓存隔离、任务冻结、runtime 状态和
UI/交付 provenance，不能伪造以上硬件结果。

## 7. 回滚

回滚只需恢复原来的 v2 选择和目录配置，保留 v2.5 文件以便后续复测：

```text
AUDIOBOOK_STUDIO_ENGINE=indextts
AUDIOBOOK_STUDIO_ENGINE_VERSION=v2
AUDIOBOOK_STUDIO_MODEL_DIR_V2=<原 v2 目录>
```

如果使用 `config.json`，恢复原 `model_dir` 并删除或改回新增的 version-specific
键即可。不要删除旧模型、缓存或项目产物作为“回滚”步骤；先停止正在运行的合成任务，
重新启动工作台，再运行环境诊断和一段试听确认。

## 8. 风险与注意事项

- v2 与 v2.5 的配置、权重和 tokenizer 不应混放；目录存在不代表版本正确。
- checkpoint 文件名存在官方发行差异，best-effort 检查可能给出 warning；缺失提示应
  回到对应发行包的官方清单核对。
- Torch CUDA runtime、NVIDIA 驱动、GPU 架构和 Windows wheel 需要整体匹配；仅看到
  GPU 名称或 BF16=True 不能证明引擎加载成功。
- RTX 5070 Ti 基线上的显存峰值、速度和音质不应直接外推到其他 GPU；变更 fp16、
  kernel、加速或 beam 参数时应重新做 A/B。
- 迁移配置只影响选择与诊断范围。本次实现不改 adapter、cache、runtime，不自动迁移
  模型文件，不联网，也不提交任何变更。
