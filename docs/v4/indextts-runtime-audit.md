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

v4 默认流程：

`app.py` 的 v4 合成事件 → `ui/v4_workspace_handlers.run_v4_synthesis` → `SynthesisExecutor` → `IndexTTS2Adapter` → 目标 checkout 的 `IndexTTS2.__init__` / `infer`

v4 adapter 延迟创建单例并在 Worker 生命周期内保持驻留，以 `RLock` 保证 GPU 并发为 1。构造和 infer 参数都先检查实际 Python 签名；文本切分由 `SynthesisPlanner` 完成，任务状态、失败恢复和缓存索引使用 `runtime.db`，OOM 只拆当前 task，章节输出由 `ChapterAssembler` 读取已完成 leaf task 后装配。每次执行把不含原文的耗时、音频时长、缓存和可用 CUDA 显存指标写入 `synthesis_metrics`。

v3 回滚流程仍为：

`services/synthesis.py` → `lib/queue.py` → `lib/directed_synthesis.py` → `lib/tts_engine.py`

它只服务显式 v3 兼容项目，不是新建书稿的默认链。

这只能证明应用侧当前适配方式，不能证明目标 IndexTTS2 checkout 的真实 API。Phase 4 必须以目标机签名审计结果为准。

## 当前仓库结论

- 不修改 IndexTTS2 源码。
- 不在当前 Mac 上做 GPU benchmark。
- 5070 Ti 12GB profile 是面向目标 Windows 环境的保守 provisional 默认值，尚未 benchmark。
- 仓库已提供目标机取证和 benchmark 工具；真实 provenance、签名和 benchmark JSON 必须在目标 Windows 安装上生成后再把 Profile 标记为 verified。
