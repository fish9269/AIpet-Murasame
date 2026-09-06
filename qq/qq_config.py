# -*- coding: utf-8 -*-
"""
QQ 配置模块 — 读取 config.json 中的 qq_* 配置项。
"""

import os
import json
import socket

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")

# 表情包根目录 — 按当前角色动态解析（不再固定根目录）
# 由 pet_registry.get_sticker_dir() 返回角色包内的 biaoqingbao/（若无则返回空 → QQ 不发图）
from pets.pet_registry import get_sticker_dir
STICKER_DIR = get_sticker_dir()
if not STICKER_DIR:
    STICKER_DIR = os.path.join(BASE_DIR, "biaoqingbao")  # 兜底旧路径

# F5-TTS 服务端口（长文本中文语音合成）
F5TTS_PORT = 9881


def _load_config():
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def check_port_open(port, host="127.0.0.1", timeout=1):
    """检查本机端口是否可连接"""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(timeout)
            return s.connect_ex((host, port)) == 0
    except Exception:
        return False


def _cfg_int_min(value, default, minimum):
    """读取整数配置：非数字/空值回退 default，再取不小于 minimum（分钟下限防呆）"""
    try:
        return max(minimum, int(float(str(value).strip())))
    except Exception:
        return max(minimum, default)


def _parse_master_ids(cfg):
    """解析主人白名单：qq_owner_id（第一位/主主人） + qq_master_ids（额外，最多 4 个）。
    总数上限 5；自动去重、过滤非法项。"""
    import re as _re
    ids = []

    def _add(v):
        s = str(v or "").strip()
        if s and s.isdigit() and s not in ids:
            ids.append(s)

    _add(cfg.get("qq_owner_id"))
    raw = cfg.get("qq_master_ids")
    if isinstance(raw, list):
        for v in raw:
            _add(v)
    elif isinstance(raw, str):
        for part in _re.split(r"[,，;；\s]+", raw):
            _add(part)
    return ids[:5]


def get_qq_config():
    """读取 QQ 相关配置（带默认值）"""
    cfg = _load_config()
    f5tts_ready = check_port_open(F5TTS_PORT)
    return {
        # NapCat WebSocket 地址（事件上报 + 调用 API 走同一连接）
        "ws_url": cfg.get("qq_napcat_ws", "ws://127.0.0.1:3001"),
        # NapCat 正向 WS token 鉴权（NapCat 开启 token 时必填，否则连上即断 retcode 1403）
        "napcat_token": str(cfg.get("qq_napcat_token", "") or "").strip(),
        # NapCat WebUI (HTTP API，主要用于发送消息等)
        "http_url": cfg.get("qq_napcat_http", "http://127.0.0.1:6099"),
        # 是否在回复时携带表情包 gif
        "send_sticker": str(cfg.get("qq_send_sticker", "true")).lower() == "true",
        # 是否在回复时附带 F5-TTS 语音
        "send_voice": str(cfg.get("qq_send_voice", "false")).lower() == "true",
        # 是否启用图片识别（收到图片时调用视觉模型识别，模型由 vision_model_name 配置）
        "vision_enabled": str(cfg.get("qq_vision_enabled", "true")).lower() == "true",
        # 是否启用语音识别（收到语音消息时用 faster-whisper 转文字）
        "stt_enabled": str(cfg.get("qq_stt_enabled", "false")).lower() == "true",
        # 是否允许群聊（只 @ 时回复）
        "allow_groups": str(cfg.get("qq_allow_groups", "true")).lower() == "true",
        # 离线消息补拉（默认开：启动时补回离线期间消息；NapCat 不支持时可在 PCL 设置关闭。
        # 即便开启，任何请求失败/超时也已做极短超时 + 不拖断主 WS）
        "offline_enabled": str(cfg.get("qq_offline_enable", "true")).lower() == "true",
        # 空闲自动离线（默认关：保持始终活跃，不会自动离线导致不回复。
        # 开启后：空闲超过 qq_auto_offline_minutes 分钟 → QQ 状态切为「离开」并暂停自动回复；
        # 收到主人 QQ 的消息 → 立即恢复在线并正常回复）
        "auto_offline_enabled": str(cfg.get("qq_auto_offline_enable", "false")).lower() == "true",
        # 非法值（0/负数/非数字）一律回退默认并取下限 1 分钟
        "auto_offline_minutes": _cfg_int_min(cfg.get("qq_auto_offline_minutes", 30), 30, 1),
        # 活泼模式（默认关：仅在被 @ 时回复群聊。开启后：监控所在群的消息，
        # 冷却间隔后若群里有新动静，会作为角色主动接一句话活跃群气氛）
        "lively_enabled": str(cfg.get("qq_lively_enable", "false")).lower() == "true",
        # 接话间隔最低 1 分钟（UI 调节范围 1~120）；非法值兜底 15
        "lively_interval": _cfg_int_min(cfg.get("qq_lively_interval", 15), 15, 1),
        # 主人白名单（最多 5 个 QQ 号；第一位 = 主主人，负责共享记忆/离线补拉）
        "master_ids": _parse_master_ids(cfg),
        # 主主人（owner 兼容键，供离线补拉/共享记忆等"主号"逻辑使用）
        "owner_id": (_parse_master_ids(cfg) or [""])[0],
        # 插件全局总开关（启动器「插件」页管控；默认均启用）
        "adult_allowed": str(cfg.get("qq_adult_enable", "true")).lower() == "true",
        "galgame_allowed": str(cfg.get("qq_galgame_enable", "true")).lower() == "true",
        "slang_allowed": str(cfg.get("qq_slang_enable", "true")).lower() == "true",
        # F5-TTS 服务是否就绪（端口 9881）
        "f5tts_ready": f5tts_ready,
    }


def load_config():
    """兼容旧调用：返回完整 config dict"""
    return _load_config()