# Phase 4：合成执行、缓存与章节装配

## IndexTTS2 adapter

`tts/indextts2_adapter.py` 动态导入目标机真实 `indextts.infer_v2.IndexTTS2`，根据构造器和 `infer` 实际签名传参。引擎实例在 adapter 生命周期内常驻并由单一 RLock 串行访问；默认 concurrency 必须为 1。adapter 不修改 IndexTTS2 checkout。

目标 Windows 安装的 SHA、dirty 状态和签名仍须按 `indextts-runtime-audit.md` 取证。当前 Mac 测试只使用签名兼容 fake engine，不声称完成 GPU benchmark。

## 事务队列

runtime schema v4 在 synthesis task 中保存 voice、actual text、输入指纹、测量长度和失败长度。claim 使用 `BEGIN IMMEDIATE` 原子地把一条 pending 改为 running。完成、失败、OOM 拆分、缓存写入和章节成品记录均使用事务。

异常退出遗留 running 会恢复为 pending；completed 不重跑；cancel 在 claim 前停止；每个任务独立失败。

## OOM 与可恢复错误

CUDA OOM、长度限制和空音频被归类为 recoverable。执行器只拆当前 task：

1. 释放 Python 临时对象；
2. profile 允许时清理 CUDA cache，但不卸载模型；
3. 按 punctuation/safe boundary 拆成两个 child task；
4. child 记录 parent_task_id 和 split_depth；
5. parent 标记 skipped，并记录失败实际长度；
6. 达到 max depth 或 minimum 后标记 failed，不无限递归。

## Cache

cache key 来自 Phase 3 的真实输入指纹。cache entry 记录相对路径、SHA、时长、采样率、声道和大小。命中前同时检查文件存在与 SHA；缺失或篡改会事务标记 invalid 并重新合成。

## Chapter assembler

assembler 按 plan task 顺序递归解析 completed OOM leaf outputs，统一采样率、声道和 int16，再按 continuation、speaker change 和普通边界插入不同停顿；可配置短 crossfade。章节 WAV 的相对路径、plan revision、内容指纹和时长写入 `chapter_outputs`。

## 测试与实机边界

自动测试覆盖常驻引擎、签名过滤、cache 命中/损坏、断点恢复、取消、OOM 递归上限、父子任务、不同 WAV 格式装配和章节指纹。真实 RTX 5070 Ti Laptop 12GB 性能、VRAM 与音质只在目标 Windows 环境通过 benchmark harness 验收。
