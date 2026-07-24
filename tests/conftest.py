"""测试隔离：在外置数据目录特性下，将 DATA_DIR / LEGACY_DIR 重定向到临时区。

- 避免测试在用户主目录（~/AudiobookStudio）落下文件；
- 避免 ``scan_projects`` 扫描到程序目录内真实的历史项目（legacy 兼容目录）。

必须在所有测试模块导入 ``lib.project_manager``（其在导入期据环境变量计算
WORKSPACE_ROOT / LEGACY_ROOT）之前设置，conftest 由 pytest 在收集前加载，满足该时序。
"""
import os
import tempfile

_TEST_ROOT = os.path.join(tempfile.gettempdir(), "audiobook_studio_test_data")
os.makedirs(_TEST_ROOT, exist_ok=True)
os.environ["AUDIOBOOK_STUDIO_DATA_DIR"] = _TEST_ROOT
os.environ["AUDIOBOOK_STUDIO_LEGACY_DIR"] = os.path.join(_TEST_ROOT, "legacy")
