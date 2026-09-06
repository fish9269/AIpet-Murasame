# -*- coding: utf-8 -*-
"""
成人（限制级）模式状态 — 默认关闭。

- 主人（白名单）@ 或在私聊中说「开启成人模式 / 关闭限制级模式」等可切换；
- 开启后允许 18+ 成人向对话；关闭时要求回避露骨内容；
- 状态存 data/qq_adult_mode.json（默认缺失 = 关闭）；每次启动读一次，进程内缓存。
"""

import os
import json
import time
import threading

from tool.paths import data_path

STATE_FILE = data_path("data", "qq_adult_mode.json")

_lock = threading.Lock()
_cache = None  # None=未读；bool=当前状态


def is_enabled() -> bool:
    """读取成人模式是否开启（进程内缓存，首次读文件）。
    受启动器插件总开关 qq_adult_enable 管控：插件被禁用时强制关闭。"""
    global _cache
    try:
        from qq.qq_config import get_qq_config as _gqc
        if not _gqc().get("adult_allowed", True):
            return False
    except Exception:
        pass
    with _lock:
        if _cache is None:
            try:
                if os.path.exists(STATE_FILE):
                    with open(STATE_FILE, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    _cache = bool(data.get("enabled", False))
                else:
                    _cache = False
            except Exception:
                _cache = False
        return _cache


def set_enabled(enabled: bool) -> None:
    """设置成人模式状态（持久化）；插件禁用时拒绝开启"""
    try:
        from qq.qq_config import get_qq_config as _gqc
        if enabled and not _gqc().get("adult_allowed", True):
            print("[QQAdult] ⚠ 成人模式插件已禁用，拒绝开启")
            return
    except Exception:
        pass
    global _cache
    with _lock:
        _cache = bool(enabled)
        try:
            os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
            with open(STATE_FILE, "w", encoding="utf-8") as f:
                json.dump({
                    "enabled": bool(enabled),
                    "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                }, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[QQAdult] ⚠ 保存成人模式状态失败: {e}")


def parse_toggle(text: str):
    """解析主人的自然语言开关指令。

    开启：含「开启/打开/启动/启用」等且含「成人模式/限制级模式/18+」
    关闭：含「关闭/关掉/停止/取消/退出」等且含「成人模式/限制级模式/18+」
    同时出现时以更靠后的关键词为准（如「先关掉再说…算了开启成人模式」→ 开）。
    返回 True/False；与模式无关的文本返回 None。
    """
    if not text or not isinstance(text, str):
        return None
    t = text.strip().lower().replace(" ", "").replace("＋", "+")
    has_mode = ("成人模式" in t) or ("限制级" in t) or ("限制模式" in t) or ("18+" in t)
    if not has_mode:
        return None
    on_words = ("开启", "打开", "启动", "开一下", "进入", "启用", "开始")
    off_words = ("关闭", "关掉", "停掉", "停止", "取消", "退出", "解除", "结束")
    on_idx = max([t.find(w) for w in on_words if w in t] or [-1])
    off_idx = max([t.find(w) for w in off_words if w in t] or [-1])
    if on_idx < 0 and off_idx < 0:
        return None  # 提到了模式但没有开关词 → 不处理
    return on_idx > off_idx
