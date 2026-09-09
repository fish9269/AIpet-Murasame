# -*- coding: utf-8 -*-
"""
QQ 媒体收藏 — 按需保存/发送图片、视频与群表情（各自最多 10 个）。

- images  ：保存的图片文件（可原样发送）
- videos  ：保存的视频条目（标题 + 链接 + 封面图文件，发送时发封面图+链接文本）
- stickers：收藏的群友表情包图片（与内置默认表情包分开，默认表情包不可删）
超过 10 个时自动删除最旧一个；也可指令手动删除。
状态: data/qq_saved.json；文件: data/qq_saved_media/{images,stickers,covers}/
"""

import os
import json
import time
import shutil
import threading

import requests

from tool.paths import data_path

MAX_PER_KIND = 10
_lock = threading.Lock()
_STATE = None
_BASE = None


def _paths():
    global _BASE
    if _BASE is None:
        _BASE = data_path("data")
    d = os.path.join(_BASE, "qq_saved_media")
    for sub in ("images", "stickers", "covers"):
        os.makedirs(os.path.join(d, sub), exist_ok=True)
    return _BASE, d


def _load():
    global _STATE
    if _STATE is None:
        try:
            p = os.path.join(_paths()[0], "qq_saved.json")
            if os.path.exists(p):
                with open(p, "r", encoding="utf-8") as f:
                    d = json.load(f)
                _STATE = {"images": d.get("images", []), "videos": d.get("videos", []),
                          "stickers": d.get("stickers", [])}
            else:
                _STATE = {"images": [], "videos": [], "stickers": []}
        except Exception:
            _STATE = {"images": [], "videos": [], "stickers": []}
    return _STATE


def _save():
    try:
        p = os.path.join(_paths()[0], "qq_saved.json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump(_STATE, f, ensure_ascii=False, indent=1)
    except Exception:
        pass


def _trim(kind):
    items = _STATE[kind]
    while len(items) > MAX_PER_KIND:
        old = items.pop(0)
        _try_del_file(old.get("file"))


def _try_del_file(p):
    try:
        if p and os.path.exists(p):
            os.remove(p)
    except Exception:
        pass


def _download(url, dst_dir, ext_hint=".jpg"):
    try:
        if url.startswith("//"):
            url = "https:" + url
        r = requests.get(url, timeout=20, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0",
            "Referer": "https://www.bilibili.com/"})
        if r.status_code == 200 and r.content:
            from urllib.parse import urlparse
            ext = os.path.splitext(urlparse(url).path)[1].lower()
            if ext not in (".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"):
                ext = ext_hint
            p = os.path.join(dst_dir, f"saved_{int(time.time() * 1000)}{ext}")
            with open(p, "wb") as f:
                f.write(r.content)
            return p
    except Exception:
        pass
    return None


def _uniq_name(kind, prefer=""):
    """生成不重名条目名：优先 prefer，冲突加序号"""
    exists = {str(x.get("name")) for x in _STATE[kind]}
    prefer = (prefer or "").strip()
    if prefer and prefer not in exists:
        return prefer
    base = prefer or f"{kind[:3]}{int(time.time()) % 10000}"
    i = 2
    name = base
    while name in exists:
        name = f"{base}{i}"
        i += 1
    return name


# ── 图片 ────────────────────────────────
def add_image(file_path=None, url=None, name=None) -> dict:
    """保存一张图片（本地文件或 URL）。返回条目 dict；失败返回 None"""
    with _lock:
        st = _load()
        base, d = _paths()
        saved = None
        if url:
            saved = _download(url, os.path.join(d, "images"), ".jpg")
        elif file_path and os.path.exists(file_path):
            ext = os.path.splitext(file_path)[1] or ".jpg"
            saved = os.path.join(d, "images", f"saved_{int(time.time() * 1000)}{ext}")
            try:
                shutil.copy2(file_path, saved)
            except Exception:
                saved = None
        if not saved:
            return None
        item = {"name": _uniq_name("images", name), "file": saved,
                "src": url or file_path or "", "t": time.time()}
        st["images"].append(item)
        _trim("images")
        _save()
        return item


def add_sticker(file_path=None, url=None, name=None) -> dict:
    """收藏一张群表情包图片（与内置默认表情包分离）"""
    with _lock:
        st = _load()
        base, d = _paths()
        saved = None
        if url:
            saved = _download(url, os.path.join(d, "stickers"), ".gif")
        elif file_path and os.path.exists(file_path):
            ext = os.path.splitext(file_path)[1] or ".gif"
            saved = os.path.join(d, "stickers", f"stk_{int(time.time() * 1000)}{ext}")
            try:
                shutil.copy2(file_path, saved)
            except Exception:
                saved = None
        if not saved:
            return None
        item = {"name": _uniq_name("stickers", name), "file": saved,
                "src": url or file_path or "", "t": time.time()}
        st["stickers"].append(item)
        _trim("stickers")
        _save()
        return item


def add_video(name, url, title="", cover_url=None, file=None) -> dict:
    """收藏一个视频条目（file 为本地视频文件时直接引用；否则存标题+链接+封面）"""
    with _lock:
        st = _load()
        base, d = _paths()
        cover = ""
        if file and os.path.exists(file):
            cover = file  # 本地视频文件
        else:
            if cover_url:
                cover = _download(cover_url, os.path.join(d, "covers"), ".jpg")
        item = {"name": _uniq_name("videos", name), "url": url,
                "title": title or name, "cover": cover, "file": file or "", "t": time.time()}
        st["videos"].append(item)
        _trim("videos")
        _save()
        return item


# ── 查询 / 删除 ─────────────────────────
def names(kind) -> list:
    with _lock:
        return [str(x.get("name")) for x in _load()[kind]]


def find(kind, keyword) -> dict:
    """按名字/标题模糊查找条目"""
    kw = (keyword or "").strip().lower()
    with _lock:
        items = _load()[kind]
        for x in items:
            if kw in str(x.get("name", "")).lower() or kw in str(x.get("title", "")).lower():
                return x
        return None


def remove(kind, keyword) -> bool:
    with _lock:
        st = _load()
        items = st[kind]
        kw = (keyword or "").strip().lower()
        for i, x in enumerate(items):
            if kw in str(x.get("name", "")).lower():
                _try_del_file(x.get("file"))
                del items[i]
                _save()
                return True
        return False


def summary() -> str:
    with _lock:
        st = _load()
        def fmt(kind, label):
            ns = [str(x.get("name")) for x in st[kind]]
            if not ns:
                return f"{label}：无"
            return f"{label}（{len(ns)}/{MAX_PER_KIND}）：{'、'.join(ns[:10])}"
        return "\n".join([fmt("images", "🖼 收藏图片"), fmt("videos", "🎬 收藏视频"),
                          fmt("stickers", "😊 收藏表情")])
