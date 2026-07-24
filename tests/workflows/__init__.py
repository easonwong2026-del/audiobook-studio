"""工作流集成测试：完整用户操作链路（§10.3 集成测试 + §10.4 工作流测试）。

测试在临时目录下完全隔离运行：
- 每个测试使用 ``tmp_path`` 作为数据根目录
- ``monkeypatch.setenv("AUDIOBOOK_STUDIO_DATA_DIR", str(tmp_path))`` 重定向全部写操作
- 无外部文件 / GPU / IndexTTS 依赖
"""
