# -*- coding: utf-8 -*-
"""
Galgame 对话立绘 — 情绪 → 桌宠分层立绘合成（QQ 发送用）。

桌宠 fgimages 是「千恋万花·丛雨」的分层立绘素材：
每个编号是一张透明 PNG 图层（基础人物/表情/装饰/头发），带坐标索引 txt。
这里按情绪选层（固定校服装扮），用 alpha 合成完整立绘并保存 PNG，
供 QQ 消息作为图片发送。情绪词由对话模型按 Galgame 语境输出 [立绘:xxx]。
"""

import csv
import os
import threading

import cv2
import numpy as np

from pets.pet_registry import get_fgimages_dir, get_active_pet_id, get_pet_config

_lock = threading.Lock()

# ── 情绪 → (表情层ID, 可选装饰层ID) ──────────────────────────
EMOTION_MAP = {
    # 开心/笑
    "开心": (1964, None), "高兴": (1964, None), "笑容": (1964, None),
    "笑": (1964, None), "嘿嘿": (1904, None), "得意": (1904, None),
    # 害羞/撒娇
    "害羞": (1480, 1958), "羞涩": (1480, 1958), "脸红": (1480, 1958),
    "撒娇": (1504, 1958), "腼腆": (1455, 1958),
    # 生气/不满
    "生气": (1620, None), "愤怒": (1620, None), "不满": (1801, None),
    # 难过/哭
    "难过": (1994, None), "伤心": (1994, None), "委屈": (1995, None),
    "哭": (1994, None),
    # 惊讶
    "惊讶": (1368, None), "震惊": (1368, None), "惊奇": (1368, None),
    # 思考/疑惑
    "思考": (1572, None), "疑惑": (1572, None), "困惑": (1572, None),
    # 平静/普通
    "平静": (1292, None), "普通": (1292, None), "正常": (1292, None),
    # 其它
    "严肃": (1822, None), "认真": (1822, None), "叹气": (1399, 1940),
    "累": (1399, None), "寂寞": (1528, None), "真挚": (1548, None),
    "紧张": (1935, None), "愣住": (1690, None), "孩子气": (1738, None),
    "窃笑": (1644, None), "恐惧": (1856, None), "焦急": (1399, None),
}
# 固定装扮（a 套校服·自然下垂 + 必选头发），保证合成稳定
_BASE_BODY = 1952
_HAIR = 1959
_CANVAS_W, _CANVAS_H = 3600, 5100

_cache = {"set": None, "infos": None, "dir": None, "prefix": None}


def emotion_words() -> str:
    """给模型的可用情绪词列表（去重保序）"""
    seen = []
    for k in EMOTION_MAP:
        if k not in seen:
            seen.append(k)
    return "、".join(seen)


def _resolve_dir():
    pet_id = get_active_pet_id()
    fg_dir = get_fgimages_dir(pet_id)
    cfg = get_pet_config(pet_id)
    prefix = (cfg.get("model") or {}).get("fgimages_prefix", "") or "ムラサメ"
    return fg_dir, prefix


def _load_index():
    """读 {prefix}a.txt(UTF-16 TSV) → {layer_id: (left, top, w, h)}"""
    with _lock:
        if _cache["set"] is not None:
            return _cache
        fg_dir, prefix = _resolve_dir()
        idx_path = os.path.join(fg_dir, f"{prefix}a.txt")
        infos = {}
        if os.path.exists(idx_path):
            try:
                with open(idx_path, encoding="utf-16") as f:
                    rows = list(csv.reader(f, delimiter="\t"))
                for x in rows:
                    if len(x) >= 10 and x[9].strip().isdigit():
                        try:
                            infos[int(x[9])] = (int(x[2]), int(x[3]),
                                                int(x[4]), int(x[5]))
                        except Exception:
                            pass
            except Exception as e:
                print(f"[QQPortrait] ⚠ 读取图层索引失败: {e}")
        _cache.update({"set": 1, "infos": infos, "dir": fg_dir, "prefix": prefix})
        return _cache


def _paste(canvas, img, left, top):
    h, w = img.shape[:2]
    y2 = min(canvas.shape[0], top + h)
    x2 = min(canvas.shape[1], left + w)
    if top >= y2 or left >= x2:
        return
    reg_img = img[0:y2 - top, 0:x2 - left]
    reg_can = canvas[top:y2, left:x2]
    a_img = reg_img[..., 3:4] / 255.0
    a_can = 1.0 - a_img
    for c in range(3):
        reg_can[..., c] = a_img[..., 0] * reg_img[..., c] + a_can[..., 0] * reg_can[..., c]
    reg_can[..., 3] = np.maximum(reg_img[..., 3], reg_can[..., 3])


def _read_layer(fg_dir, prefix, layer_id):
    p = os.path.join(fg_dir, f"{prefix}a_{layer_id}.png")
    if not os.path.exists(p):
        return None
    img = cv2.imdecode(np.fromfile(p, dtype=np.uint8), cv2.IMREAD_UNCHANGED)
    return img if img is not None else None


def build_portrait(emotion: str = "") -> str:
    """按情绪合成一张立绘 PNG，返回文件路径；失败返回空串。"""
    try:
        emo = (emotion or "").strip()
        emo_id, decor_id = EMOTION_MAP.get(emo, EMOTION_MAP.get("平静", (1292, None)))
        info = _load_index()
        fg_dir, prefix = info["dir"], info["prefix"]
        infos = info["infos"]
        layers = [_BASE_BODY, emo_id, _HAIR]
        if decor_id:
            layers.append(decor_id)
        canvas = np.zeros((_CANVAS_H, _CANVAS_W, 4), dtype=np.uint8)
        for lid in layers:
            pos = infos.get(lid)
            img = _read_layer(fg_dir, prefix, lid)
            if pos and img is not None:
                _paste(canvas, img, pos[0], pos[1])
        # 裁剪内容区并等比缩小（QQ 图片友好）
        alpha = canvas[..., 3]
        ys, xs = np.where(alpha > 0)
        if len(xs) == 0:
            print(f"[QQPortrait] ⚠ 合成结果为空（情绪 {emo}）")
            return ""
        x0, x1, y0, y1 = xs.min(), xs.max(), ys.min(), ys.max()
        crop = canvas[y0:y1 + 1, x0:x1 + 1]
        h, w = crop.shape[:2]
        scale = min(1.0, 860.0 / h)
        if scale < 1.0:
            crop = cv2.resize(crop, (int(w * scale), int(h * scale)),
                              interpolation=cv2.INTER_AREA)
        out_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                               "tmp")
        os.makedirs(out_dir, exist_ok=True)
        out = os.path.join(out_dir, "qq_portrait_latest.png")
        cv2.imencode(".png", crop)[1].tofile(out)
        return out
    except Exception as e:
        print(f"[QQPortrait] ⚠ 立绘合成失败: {e}")
        return ""
