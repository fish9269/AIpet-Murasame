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

    # ── 搜视频并保存 ──
    m = re.search(r"(?:搜视频|搜索视频|找视频)\s*[:：]?\s*(.+)", t)
    if m:
        kw = _strip(m.group(1)).strip("？?。")
        if not kw:
            return "想搜什么视频？对我说「搜视频 猫咪」试试", []
        from qq.qq_search import search_videos
        vids = search_videos(kw, 3)
        if not vids:
            return f"没搜到「{kw}」的视频，试试发一个 B站/快手链接给我，我帮你看内容～", []
        v = vids[0]
        item = saved.add_video(v["title"][:16], v["url"], title=v["title"])
        if not item:
            return "视频保存失败", []
        return (f"已保存视频「{item['name']}」：{v['title'][:60]}"
                f"{('（' + v['dur'] + '）') if v.get('dur') else ''}"
                f"\n链接：{v['url']}\n需要时说「发视频 {item['name']}」"), []

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
            return f"没有找到视频「{_strip(m.group(1))}」。说「视频列表」查看收藏", []
        extra = f"🎬 {item.get('title') or item['name']}\n{item['url']}"
        cover = item.get("cover") or ""
        if cover and os.path.exists(cover):
            acts.append({"type": "video", "file": cover, "extra": extra})
            return f"🎬 视频「{item['name']}」的封面+链接来啦～", acts
        return f"🎬 {extra}", acts

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
