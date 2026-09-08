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


# 话题 → 背景搜索词（联网找背景用；未命中时用默认渐变背景）
BG_KEYWORDS = {
    "公园": "公园 湖 风景", "海边": "海边 沙滩 蓝天", "大海": "海边 浪花", "沙滩": "沙滩 海",
    "夜景": "城市 夜景", "晚上": "夜景 星空", "星空": "星空 夜晚", "学校": "学校 操场",
    "教室": "教室 黑板", "房间": "温馨 房间 窗", "卧室": "卧室 温馨", "客厅": "客厅 沙发",
    "咖啡": "咖啡馆 下午茶", "餐厅": "餐厅 美食", "街道": "城市 街道 夜景", "城市": "城市 街景",
    "樱花": "樱花 场景", "神社": "神社 日本", "森林": "森林 阳光", "草地": "草地 天空",
    "天空": "蓝天 白云", "雪": "雪景 白色", "温泉": "温泉 露天", "夏日": "夏日 海滩",
    "夏天": "夏日 蝉 绿荫", "雨": "雨景 窗", "雨天": "雨天 街道", "黄昏": "黄昏 晚霞",
    "夕阳": "夕阳 海边", "月亮": "夜晚 月亮", "花园": "花园 花", "操场": "操场 学校",
    "天台": "天台 天空", "山顶": "山顶 云海", "家乡": "乡村 田园", "田野": "田园 田野",
}


def extract_bg_kw(text: str) -> str:
    """从对话文本粗略提取话题场景词（供立绘背景搜索）；无命中返回空串"""
    t = text or ""
    for k, q in BG_KEYWORDS.items():
        if k in t:
            return q
    return ""


def _default_bg(h=880, w=720):
    """默认立绘背景：柔和竖向渐变（粉白→淡蓝），避免透明空白"""
    top = np.array([255, 240, 248], dtype=np.float32)   # 淡粉
    mid = np.array([240, 244, 255], dtype=np.float32)   # 淡蓝
    grad = np.zeros((h, w, 3), dtype=np.float32)
    for y in range(h):
        t = y / max(1, h - 1)
        if t < 0.6:
            c = top + (mid - top) * (t / 0.6)
        else:
            c = mid + (np.array([255, 255, 255], dtype=np.float32) - mid) * ((t - 0.6) / 0.4)
        grad[y, :, :] = c
    bg = np.zeros((h, w, 4), dtype=np.uint8)
    bg[..., :3] = grad.astype(np.uint8)
    bg[..., 3] = 255
    return bg


def _search_bg(kw):
    """联网找背景图 → 返回 720x880(cover 裁切) BGRA；失败返回 None"""
    try:
        from qq.qq_search import search_images
        import requests as _req
        imgs = search_images(kw + " 背景", 2) or search_images(kw, 2)
        for im in imgs:
            u = im.get("url")
            if not u:
                continue
            try:
                r = _req.get(u, timeout=15, headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0"})
                if r.status_code != 200 or len(r.content) < 2000:
                    continue
                arr = np.frombuffer(r.content, dtype=np.uint8)
                img = cv2.imdecode(arr, cv2.IMREAD_UNCHANGED)
                if img is None:
                    continue
                if img.shape[2] == 3:
                    img = cv2.cvtColor(img, cv2.COLOR_BGR2BGRA)
                # cover 裁切到 720x880
                ih, iw = img.shape[:2]
                scale = max(720.0 / iw, 880.0 / ih)
                img = cv2.resize(img, (int(iw * scale + 0.5), int(ih * scale + 0.5)),
                                 interpolation=cv2.INTER_AREA)
                ih2, iw2 = img.shape[:2]
                x0 = (iw2 - 720) // 2
                y0 = (ih2 - 880) // 2
                return img[y0:y0 + 880, x0:x0 + 720].copy()
            except Exception:
                continue
    except Exception:
        pass
    return None


def build_portrait(emotion: str = "", bg_kw: str = "") -> str:
    """按情绪合成一张【半身】立绘 PNG（可带话题背景），返回文件路径。

    - 半身：只取全身立绘的上半部分（头部+上半身）；
    - 背景：bg_kw 提供时先联网搜索背景图合成；否则用默认柔和渐变，
      避免 QQ 里出现透明空白背景。"""
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
        alpha = canvas[..., 3]
        ys, xs = np.where(alpha > 0)
        if len(xs) == 0:
            print(f"[QQPortrait] ⚠ 合成结果为空（情绪 {emo}）")
            return ""
        x0, x1, y0, y1 = xs.min(), xs.max(), ys.min(), ys.max()
        full = canvas[y0:y1 + 1, x0:x1 + 1]
        fh, fw = full.shape[:2]
        # ── 半身：保留上部 ~62%（头+上半身），去掉下半身 ──
        half_h = int(fh * 0.62)
        half = full[:half_h, :, :].copy()
        # ── 输出画布 720x880 ──
        OUT_W, OUT_H = 720, 880
        bg = _search_bg(bg_kw) if (bg_kw or "").strip() else None
        if bg is None:
            bg = _default_bg(OUT_H, OUT_W)
        out = bg.copy()
        # 人物贴到底部：等比缩放到高约 OUT_H*0.82，宽不超过 OUT_W-60
        ph, pw = half.shape[:2]
        target_h = int(OUT_H * 0.82)
        scale = min(target_h / ph, (OUT_W - 60) / pw)
        nw, nh = int(pw * scale), int(ph * scale)
        person = cv2.resize(half, (nw, nh), interpolation=cv2.INTER_AREA)
        px = (OUT_W - nw) // 2
        py = OUT_H - nh - 10  # 底部留 10px
        a = person[..., 3:4] / 255.0
        a2 = 1.0 - a
        reg = out[py:py + nh, px:px + nw]
        for c in range(3):
            reg[..., c] = (a[..., 0] * person[..., c] + a2[..., 0] * reg[..., c])
        reg[..., 3] = np.maximum(person[..., 3], reg[..., 3])
        out_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                               "tmp")
        os.makedirs(out_dir, exist_ok=True)
        out_p = os.path.join(out_dir, "qq_portrait_latest.png")
        cv2.imencode(".png", out)[1].tofile(out_p)
        return out_p
    except Exception as e:
        print(f"[QQPortrait] ⚠ 立绘合成失败: {e}")
        return ""
