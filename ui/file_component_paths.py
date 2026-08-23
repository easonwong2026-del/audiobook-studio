"""Safe paths returned to Gradio File components."""
from __future__ import annotations

import os
import shutil
import tempfile
import time

from lib import config


def safe_path_for_file_component(path):
    """Return a Gradio-safe path without moving or deleting the source file.

    Gradio only serves files below the application's allowed data directory (or
    its temp directory).  External artifacts therefore get a temporary copy;
    copy failures deliberately fall back to the original path so an optional
    download adaptation cannot turn a successful business operation into a
    failed export.
    """
    if not path or not os.path.isfile(path):
        return path
    data_dir = config.get_data_dir()
    if data_dir:
        try:
            if os.path.commonpath([os.path.abspath(path), os.path.abspath(data_dir)]) == os.path.abspath(data_dir):
                return path  # 已在白名单内，原样返回
        except ValueError:
            pass
    tmp_dir = tempfile.gettempdir()
    base = os.path.basename(path)
    dst = os.path.join(tmp_dir, f"audiobook_export_{base}")
    if os.path.exists(dst):
        dst = os.path.join(tmp_dir, f"audiobook_export_{int(time.time() * 1000)}_{base}")
    try:
        shutil.copy2(path, dst)
    except Exception:
        return path  # 复制失败就退回原路径，不阻断导出结果
    return dst
