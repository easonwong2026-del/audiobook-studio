# IndexTTS2 运行时审计

## 目标环境

- 应用：`D:\AudiobookStudio\project\audiobook-studio`
- IndexTTS2：`D:\AudiobookStudio\project\index-tts`
- Python：`D:\AudiobookStudio\project\index-tts\.venv\Scripts\python.exe`
- GPU：NVIDIA GeForce RTX 5070 Ti Laptop GPU，12 GB VRAM
- 平台：Windows

当前开发工作区不是上述目标环境，也无法访问 `D:` 或 `index-tts` checkout。因此本机硬件不用于推导 IndexTTS2 参数；目标安装的 commit、dirty 状态、构造器和 `infer` 签名仍是待目标机实测项，本文不会从 README 或应用侧注释伪造它们。

## 目标机取证命令

```powershell
cd D:\AudiobookStudio\project\index-tts
git rev-parse HEAD
git status --short
git log -1 --decorate --oneline
D:\AudiobookStudio\project\index-tts\.venv\Scripts\python.exe -c "import inspect; from indextts.infer_v2 import IndexTTS2; print(inspect.signature(IndexTTS2)); print(inspect.signature(IndexTTS2.infer))"
```

运行 `tools/benchmark_indextts2.py` 时也会写入相同 provenance 和签名信息。

## 应用侧实际调用链

`services/synthesis.py` → `lib/queue.py` → `lib/directed_synthesis.py` → `lib/tts_engine.py`

`lib/tts_engine.py` 当前延迟创建单例并保持驻留。构造参数包括配置、模型目录、FP16、DeepSpeed、accel、CUDA kernel；推理至少传入 speaker prompt、文本、输出路径和情绪参数，并根据真实签名选择 speed、拼音与 beam 参数。

这只能证明应用侧当前适配方式，不能证明目标 IndexTTS2 checkout 的真实 API。Phase 4 必须以目标机签名审计结果为准。

## Phase 1 结论

- 不修改 IndexTTS2 源码。
- 不在当前 Mac 上做 GPU benchmark。
- 5070 Ti 12GB profile 是面向目标 Windows 环境的保守 provisional 默认值，尚未 benchmark。
- Phase 4 开始前必须提交目标机 provenance、签名和 benchmark JSON。
