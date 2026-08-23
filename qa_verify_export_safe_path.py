#!/usr/bin/env python3
"""QA 验证：导出路径安全性 — shared safe-path adapter 三态行为。

直接调用真实的 ui.file_component_paths.safe_path_for_file_component owner。
使用隔离临时数据目录，不触碰用户真实文件。

本脚本可与 pytest 配合使用（``python -m pytest qa_verify_export_safe_path.py -v``），
也可独立运行（``python qa_verify_export_safe_path.py``）。
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile

# 加入项目路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


# ──────────── 隔离数据目录 ────────────
DATA_DIR = tempfile.mkdtemp(prefix="qa_export_data_")
os.environ["AUDIOBOOK_STUDIO_DATA_DIR"] = DATA_DIR

# 二次导入 config（环境变量生效后）
from lib import config  # noqa: E402

print(f"[setup] DATA_DIR    = {DATA_DIR}")

# ──────────── 调用真实 shared owner ────────────
from ui.file_component_paths import safe_path_for_file_component as safe_path

print(f"[setup] gettempdir  = {tempfile.gettempdir()}")
print(f"[setup] data_dir()  = {config.get_data_dir()}")
print()

# ──────────── 测试用例 ────────────
results: list[tuple[str, bool, str]] = []

# 状态1：外部分支（path 在 data_dir 之外）
ext_dir = tempfile.mkdtemp(prefix="qa_ext_")
ext_file = os.path.join(ext_dir, "clip.wav")
with open(ext_file, "wb") as f:
    f.write(b"RIFF....WAVEfake")
ret = safe_path(ext_file)
ok_ext = (
    os.path.dirname(ret) == tempfile.gettempdir()
    and ret != ext_file
    and os.path.isfile(ret)       # 副本存在
    and os.path.isfile(ext_file)  # 原文件未被移动/删除
)
results.append((
    "外部分支（路径在 data_dir 外）",
    ok_ext,
    f"返回={ret} | 副本存在={os.path.isfile(ret)} | 原文件仍在={os.path.isfile(ext_file)}",
))

# 状态2：内部分支（path 在 data_dir 内）
in_file = os.path.join(DATA_DIR, "_qa_tmp_test.wav")
with open(in_file, "wb") as f:
    f.write(b"RIFF....WAVEfake")
ret2 = safe_path(in_file)
ok_in = (ret2 == in_file) and os.path.isfile(in_file)
results.append((
    "内部分支（路径在 data_dir 内）",
    ok_in,
    f"返回==原路径={ret2 == in_file} | 返回={ret2}",
))

# 状态3：None 分支
ret3 = safe_path(None)
results.append(("None 分支", ret3 is None, f"返回={ret3}"))

# ──────────── 报告 ────────────
print()
print("=" * 60)
print("  safe_path_for_file_component ���态验证")
print("=" * 60)
all_ok = True
for name, ok, detail in results:
    status = "✅ PASS" if ok else "❌ FAIL"
    if not ok:
        all_ok = False
    print(f"  {status} | {name}")
    if not ok:
        print(f"            {detail}")

print()
print(f"{'✅ 全部通过' if all_ok else '❌ 存在失败项'}")
print()

# 清理
shutil.rmtree(DATA_DIR, ignore_errors=True)
ext_dir_name = os.path.dirname(ext_file)
if os.path.isdir(ext_dir_name) and ext_dir_name != DATA_DIR:
    shutil.rmtree(ext_dir_name, ignore_errors=True)

sys.exit(0 if all_ok else 1)
