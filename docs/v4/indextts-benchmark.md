# IndexTTS2 benchmark 方案

工具：`tools/benchmark_indextts2.py`

目标机使用真实 IndexTTS2 venv 运行。测试 token 档位为 40、60、80、100、120；每档记录冷启动 1 次和重复推理，推荐配置执行 100 次。记录字符数、token 数、耗时、音频时长、峰值/空闲显存、成功状态和错误分类。

结果写入项目 `runtime/benchmarks/*.json`，报告必须带 IndexTTS2 SHA、dirty 状态、Python、GPU、构造器和 infer 签名。

初始 `max_text_tokens` 为 100。自动分析只允许因 OOM 或稳定性问题下调，不允许无人值守地提高到 100 以上。120 档仅用于边界观测，不是自动推荐值。

本 Phase 1 只交付可重复的审计/基准 harness，不声明已完成 Windows benchmark。
