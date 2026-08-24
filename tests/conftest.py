"""测试隔离：在外置数据目录特性下，将 DATA_DIR / LEGACY_DIR 重定向到临时区。

- 避免测试在用户主目录（~/AudiobookStudio）落下文件；
- 避免 ``scan_projects`` 扫描到程序目录内真实的历史项目（legacy 兼容目录）；
- 隔离 runtime OS lock（``AUDIOBOOK_STUDIO_RUNTIME_LOCK``）：真实生产 runtime
  持有系统 Temp 下的 ``audiobook-studio-production-<user>.lock``，若测试复用同一
  lock 路径，inline runtime 无法 claim 任务（task 卡 pending）——2026-08-16
  全量测试 16 failed 的根因之一。

必须在测试模块导入项目存储组件（包括兼容 facade）之前设置，conftest 由 pytest
在收集前加载，满足该时序。
"""
import os
import tempfile

_TEST_ROOT = os.path.join(tempfile.gettempdir(), "audiobook_studio_test_data")
os.makedirs(_TEST_ROOT, exist_ok=True)
os.environ["AUDIOBOOK_STUDIO_DATA_DIR"] = _TEST_ROOT
os.environ["AUDIOBOOK_STUDIO_LEGACY_DIR"] = os.path.join(_TEST_ROOT, "legacy")
os.environ["AUDIOBOOK_STUDIO_RUNTIME_LOCK"] = os.path.join(
    _TEST_ROOT, "test-runtime.lock"
)
