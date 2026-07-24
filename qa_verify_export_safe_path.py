#!/usr/bin/env python3
"""QA 实证：验证 app.py 中 _safe_path_for_file_component 的三态行为。

从真实 app.py 源码中 AST 提取该函数定义并执行，确保被测的是工程师提交的
实际源码（而非手抄副本）。仅依赖 os/tempfile/shutil/time/config，不 import gradio。
"""
import os
import sys
import ast
import time
import tempfile
import shutil

PROJECT_ROOT = r"C:\Users\rakliang\WorkBuddy\2026-06-29-18-28-53\audiobook-studio"
sys.path.insert(0, PROJECT_ROOT)

# 隔离数据目录，避免触碰用户真实 AudiobookStudio
DATA_DIR = tempfile.mkdtemp(prefix="qa_data_dir_")
os.environ["AUDIOBOOK_STUDIO_DATA_DIR"] = DATA_DIR

from lib import config

# ---- 从真实 app.py 提取函数源码（AST，确保测的是真源码） ----
APP_PATH = os.path.join(PROJECT_ROOT, "app.py")
src = open(APP_PATH, encoding="utf-8").read()
tree = ast.parse(src)
func_src = None
for node in tree.body:
    if isinstance(node, ast.FunctionDef) and node.name == "_safe_path_for_file_component":
        func_src = ast.get_source_segment(src, node)
        break
assert func_src is not None, "未在 app.py 中找到 _safe_path_for_file_component 定义"

ns = {"os": os, "tempfile": tempfile, "shutil": shutil, "time": time, "config": config}
exec(compile(func_src, "<app.py:_safe_path_for_file_component>", "exec"), ns)
safe_path = ns["_safe_path_for_file_component"]

print(f"[setup] DATA_DIR    = {DATA_DIR}")
print(f"[setup] gettempdir  = {tempfile.gettempdir()}")
print(f"[setup] data_dir()  = {config.get_data_dir()}")

results = []

# 状态1：外部分支（path 在 data_dir 之外）
ext_dir = tempfile.mkdtemp(prefix="qa_ext_")
ext_file = os.path.join(ext_dir, "clip.wav")
with open(ext_file, "wb") as f:
    f.write(b"RIFF....WAVEfake")
ret = safe_path(ext_file)
ok_ext = (
    os.path.dirname(ret) == tempfile.gettempdir()
    and ret != ext_file
    and os.path.isfile(ret)        # 副本存在
    and os.path.isfile(ext_file)   # 原文件未被移动/删除
)
results.append(("外部分支(路径在 data_dir 外)", ok_ext,
                f"返回={ret} | 副本存在={os.path.isfile(ret)} | 原文件仍在={os.path.isfile(ext_file)}"))

# 状态2：内部分支（path 在 data_dir 内）
in_file = os.path.join(DATA_DIR, "_qa_tmp_test.wav")
with open(in_file, "wb") as f:
    f.write(b"RIFF....WAVEfake")
ret2 = safe_path(in_file)
ok_in = (ret2 == in_file) and os.path.isfile(in_file)
results.append(("内部分支(路径在 data_dir 内)", ok_in,
                f"返回==原路径={ret2 == in_file} | 返回={ret2}"))

# 状态3：None 分支
ret3 = safe_path(None)
ok_none = (ret3 is None)
results.append(("None 分支", ok_none, f"返回={ret3!r}"))

# 边界：tempdir 同名冲突 -> 毫秒时间戳改名
dup_base = "_qa_dup.wav"
dup_src = os.path.join(tempfile.mkdtemp(prefix="qa_dup_"), dup_base)
with open(dup_src, "wb") as f:
    f.write(b"x")
prefab = os.path.join(tempfile.gettempdir(), f"audiobook_export_{dup_base}")
shutil.copy2(dup_src, prefab)  # 预置一个同名副本
ret_dup = safe_path(dup_src)
ok_dup = (
    os.path.isfile(ret_dup)
    and ret_dup != prefab          # 没有覆盖预置副本
    and os.path.isfile(prefab)     # 预置副本仍在
)
results.append(("边界:tempdir 同名冲突改名", ok_dup,
                f"返回={ret_dup} | 未覆盖预置副本={ret_dup != prefab}"))

# ---- 汇总 ----
print("\n==== QA 实证结果 ====")
all_ok = True
for name, ok, detail in results:
    all_ok = all_ok and ok
    print(f"[{'PASS' if ok else 'FAIL'}] {name} :: {detail}")
print(f"\n总通过率: {sum(1 for _, ok, _ in results if ok)}/{len(results)}")
sys.exit(0 if all_ok else 1)
