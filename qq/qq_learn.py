# -*- coding: utf-8 -*-
"""
QQ 自主学习 & 媒体收藏指令处理（在调度线程内执行）。

口语指令（无需 @ 也识别，但只在 bot 正要处理的消息上）：
- 保存图片 / 保存这张图 / 存图         → 收藏本条/群最近图片
- 保存表情 / 存表情                    → 收藏为自定义表情
- 搜图 <词> / 搜索图片 <词>            → 搜索并保存图片
- 搜视频 <词>                          → 搜索视频并保存（标题+链接）
- 发图 <名> / 发表情 <名> / 发视频 <名> → 发送收藏（返回发送动作）
- 删图 <名> / 删表情 <名> / 删视频 <名> → 删除收藏
- 图列表 / 表情列表 / 视频列表 / 我的收藏 → 查看收藏清单

返回: (回复文本或 None, [发送动作])；发送动作 dict:
  {"type": "image"/"sticker"/"video", "file": 路径, "extra": 附文文本 或 None}
"""

import re

from qq import qq_saved as saved


def bilibili_download(bvid, max_bytes=70 * 1024 * 1024, max_dur=600):
    """下载 B站视频(默认 480P, 超限降 360P)。返回 (本地路径, 标题)；失败返回 (None, 原因)"""
    import os as _os, requests as _req, time as _t
    _UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0",
           "Referer": "https://www.bilibili.com/"}
    try:
        r = _req.get("https://api.bilibili.com/x/web-interface/view?bvid=" + bvid,
                     headers=_UA, timeout=10)
        j = r.json()
        d = j.get("data") or {}
        cid = d.get("cid")
        title = d.get("title") or bvid
        if not cid:
            return None, "视频信息获取失败"
        if int(d.get("duration") or 0) > max_dur:
            return None, "视频时长过长"
        path = None
        for qn in (64, 32):
            r2 = _req.get(
                "https://api.bilibili.com/x/player/playurl?bvid=%s&cid=%s&qn=%d&fnval=1" % (bvid, cid, qn),
                headers=_UA, timeout=10)
            du = ((r2.json().get("data") or {}).get("durl") or [])
            if not du:
                continue
            u = du[0].get("url", "")
            try:
                size = int(du[0].get("size") or 0)
            except Exception:
                size = 0
            if size > max_bytes:
                continue
            with _req.get(u, headers=_UA, timeout=20, stream=True) as rr:
                if rr.status_code != 200:
                    continue
                base = os.path.dirname(os.path.abspath(__file__))
                d_dir = os.path.join(base, "..", "data", "qq_saved_media", "videos")
                os.makedirs(d_dir, exist_ok=True)
                path = os.path.join(d_dir, "bili_%s_%d.mp4" % (bvid, _t.time()))
                with open(path, "wb") as f:
                    for chunk in rr.iter_content(65536):
                        f.write(chunk)
                break
        if not path or not os.path.exists(path):
            return None, "视频下载失败（可能需登录或清晰度被限制）"
        return path, title
    except Exception as e:
        return None, "下载出错: " + str(e)


def _strip(text):
    return (text or "").strip()


def _has_any(text, words):
    t = _strip(text).lower().replace(" ", "").replace("　", "")
    return any(w in t for w in words)


def handle(text, msg_ctx=None):
    """处理媒体指令。msg_ctx: {"cur_image_file": 本条图路径或None,
    "last_image_file": 群最近图路径或None}；返回 (回复, 动作列表)"""
    t = _strip(text)
    if not t:
        return None, []
    acts = []

    # ── 收藏图片/表情（用本条或群最近图）──
    want_sticker = _has_any(t, ("保存表情", "存表情", "收藏表情"))
    want_image = _has_any(t, ("保存图片", "保存这张图", "存图", "收藏图片", "保存这张图片"))
    if want_image or want_sticker:
        kind = "stickers" if want_sticker else "images"
        fp = None
        if msg_ctx:
            fp = msg_ctx.get("cur_image_file") or msg_ctx.get("last_image_file")
        if not fp:
            return ("没有可保存的图片哦～ 请先发一张图片，或对我说「搜图 关键词」"
                    "让我上网找～（收藏上限各 10 个，满了会自动替换最旧的）"), []
        item = (saved.add_sticker if want_sticker else saved.add_image)(fp)
        if not item:
            return "图片保存失败（文件读取/下载出错）", []
        label = "表情" if want_sticker else "图片"
        return (f"已收藏{label}「{item['name']}」（{len(saved.names(kind))}/10）。"
                f"需要时说「{'发表情' if want_sticker else '发图'} {item['name']}」，"
                f"删除说「{'删表情' if want_sticker else '删图'} {item['name']}」"), []

    # ── 搜图并保存 ──
    m = re.search(r"(?:搜图|搜索图片|搜图片|找图片)\s*[:：]?\s*(.+)", t)
    if m:
        kw = _strip(m.group(1)).strip("？?。")
        if not kw:
            return "想搜什么图？对我说「搜图 猫咪」试试", []
        from qq.qq_search import search_images
        imgs = search_images(kw, 3)
        if not imgs:
            return f"没搜到「{kw}」的图片，换个关键词试试？", []
        item = None
        for im in imgs:
            item = saved.add_image(url=im["url"], name=kw[:12])
            if item:
                break
        if not item:
            return "图片下载失败，稍后再试？", []
        acts.append({"type": "image", "file": item["file"], "extra": None})
        return (f"已按「{kw}」搜到并保存图片「{item['name']}」"
                f"（{len(saved.names('images'))}/10），这就发给你看～"), acts

    # ── 搜视频并直接下载发送 ──
    m = re.search(r"(?:搜视频|搜索视频|找视频)\s*[:：]?\s*(.+)", t)
    if m:
        kw = _strip(m.group(1)).strip("？?。")
        if not kw:
            return "想搜什么视频？对我说「搜视频 猫咪」试试", []
        from qq.qq_search import search_videos
        vids = search_videos(kw, 3)
        if not vids:
            return "没搜到「" + kw + "」的视频，试试直接发 B站/快手链接给我", []
        v = vids[0]
        path, title = bilibili_download(v["bvid"])
        if not path:
            return "视频「" + (v.get("title") or "")[:30] + "」" + title + "（可换关键词或直接发链接）", []
        item = saved.add_video((v.get("title") or "")[:16], v["url"],
                               title=title or v.get("title"), file=path)
        if not item:
            return "视频保存失败", []
        acts.append({"type": "video_file", "file": path, "extra": None})
        return "🎬 已找到并下载视频「" + item["name"] + "」，正在发给你～", acts

    # ── 发送收藏 ──
    m = re.search(r"(?:发图|发图片|发照片)\s*[:：]?\s*(.+)", t)
    if m:
        item = saved.find("images", _strip(m.group(1)))
        if not item:
            return f"没有找到图片「{_strip(m.group(1))}」。说「图列表」查看全部收藏", []
        acts.append({"type": "image", "file": item["file"], "extra": None})
        return f"🖼 给你～（{item['name']}）", acts
    m = re.search(r"(?:发表情|发自定义表情)\s*[:：]?\s*(.+)", t)
    if m:
        item = saved.find("stickers", _strip(m.group(1)))
        if not item:
            return f"没有找到表情「{_strip(m.group(1))}」。说「表情列表」查看收藏", []
        acts.append({"type": "sticker", "file": item["file"], "extra": None})
        return f"😊 来啦～（{item['name']}）", acts
    m = re.search(r"(?:发视频)\s*[:：]?\s*(.+)", t)
    if m:
        item = saved.find("videos", _strip(m.group(1)))
        if not item:
            return "没有找到视频「" + _strip(m.group(1)) + "」。说「视频列表」查看收藏", []
        vf = item.get("file") or ""
        if vf and os.path.exists(vf):
            acts.append({"type": "video_file", "file": vf, "extra": None})
            return "🎬 视频「" + item["name"] + "」正在发送～", acts
        extra = "🎬 " + (item.get("title") or item["name"]) + chr(10) + item["url"]
        cover = item.get("cover") or ""
        if cover and os.path.exists(cover):
            acts.append({"type": "video", "file": cover, "extra": extra})
            return "🎬 视频「" + item["name"] + "」封面+链接来啦～", acts
        return "🎬 " + extra, acts

    # ── 删除收藏 ──
    m = re.search(r"(?:删图|删除图片)\s*[:：]?\s*(.+)", t)
    if m:
        ok = saved.remove("images", _strip(m.group(1)))
        return ("已删除图片收藏" if ok else f"没有找到「{_strip(m.group(1))}」"), []
    m = re.search(r"(?:删表情|删除表情)\s*[:：]?\s*(.+)", t)
    if m:
        ok = saved.remove("stickers", _strip(m.group(1)))
        return ("已删除表情收藏" if ok else f"没有找到「{_strip(m.group(1))}」"), []
    m = re.search(r"(?:删视频|删除视频)\s*[:：]?\s*(.+)", t)
    if m:
        ok = saved.remove("videos", _strip(m.group(1)))
        return ("已删除视频收藏" if ok else f"没有找到「{_strip(m.group(1))}」"), []

    # ── 查看收藏 ──
    if _has_any(t, ("图列表", "图片列表", "表情列表", "视频列表", "我的收藏", "收藏列表")):
        sep = chr(10)
        tips = (sep + sep +
                "【用法示例】搜图：搜图 猫咪（自动保存并发给你）；搜视频：搜视频 猫 搞笑；"
                "保存这张图：先收到图后说「保存这张图」（收藏本条/群最近一张图）；"
                "发图：发图 猫咪；发表情：发表情 xx；删图：删图 猫咪。"
                "群里说这些话可以不用 @我，直接说即可。")
        return saved.summary() + tips, []

    return None, []


def is_media_cmd(text) -> bool:
    """快速判断是否为媒体/收藏指令（供对话链路拦截）"""
    t = _strip(text or "")
    if not t:
        return False
    if _has_any(t, ("保存图片", "保存这张图", "存图", "收藏图片", "保存表情", "存表情", "收藏表情",
                    "搜图", "搜视频", "搜索图片", "搜索视频", "发图", "发图片", "发表情",
                    "发视频", "删图", "删表情", "删视频", "删除图片", "删除表情", "删除视频",
                    "图列表", "图片列表", "表情列表", "视频列表", "我的收藏", "收藏列表")):
        return True
    return False


import os  # noqa: E402  (模块尾 import 避免循环)
