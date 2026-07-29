# IndexTTS2 benchmark 方案

工具：`tools/benchmark_indextts2.py`

目标机使用真实 IndexTTS2 venv 运行。测试 token 档位为 40、60、80、100、120；每档重新加载引擎记录冷启动 1 次，再使用常驻 Worker 连续推理至少 10 次，100 Token 档连续执行至少 100 次。样本循环覆盖普通中文旁白、中文对白、中英文混合、数字日期、拼音标注、长句无标点和自动情绪。

结果固定写入 `runtime/benchmarks/indextts2-rtx5070ti-laptop-12gb.json`，并生成 `docs/v4/indextts2-benchmark-results.md`。报告带 IndexTTS2 SHA、dirty 状态、Python、GPU、构造器和 infer 签名；每次运行记录字符/Token 数及 tokenizer 来源、耗时、音频时长、torch allocated/reserved/peak、空闲显存、成功状态和错误分类，不保存完整测试文本或音色内容。

初始 `max_text_tokens` 为 100。自动分析会排除发生失败/OOM、峰值超过总显存 85%、空闲显存低于 1536 MB 或连续运行 reserved 显存增长超过 1536 MB 的档位，只能从 100 下调至 80、60 或 40，不允许无人值守地提高到 100 以上。120 档仅用于边界观测，不是自动推荐值。

示例：

```powershell
Set-Location D:\AudiobookStudio\project\audiobook-studio
& D:\AudiobookStudio\project\index-tts\.venv\Scripts\python.exe `
  .\tools\benchmark_indextts2.py `
  --checkout D:\AudiobookStudio\project\index-tts `
  --model-dir D:\AudiobookStudio\project\index-tts\checkpoints `
  --speaker-prompt D:\path\to\reference.wav `
  --run-inference
```

仓库交付可重复的审计/基准 harness，但只有目标 Windows 输出的真实报告才能把 Profile 从 `provisional-unbenchmarked` 改为 verified。
