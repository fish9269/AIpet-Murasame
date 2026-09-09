# -*- coding: utf-8 -*-
"""
QQ 自主学习工具 — 联网搜索 / 链接理解 / 图片与视频检索。

- search_web(query): 必应网页搜索 → 若干条 (标题, 摘要, URL)
- read_link(url):    打开链接抓取标题+简介（含 bilibili / 快手等视频页 og 信息）
- search_images(query): 必应图片搜索 → [{url, title}]（murl 原图）
- search_videos(query): bilibili 搜索 → [{bvid, title, author, cover, url}]
全部带超时、失败返回空结构；调用方自行静默。
"""

import re
import os
import json
import time
import urllib.parse

import requests

_UA = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"),
    "Accept-Language": "zh-CN,zh;q=0.9",
}
_TIMEOUT = 8
_session = requests.Session()


def _get(url, timeout=_TIMEOUT, headers=None, referer=None):
    try:
        h = dict(_UA)
        if headers:
            h.update(headers)
        if referer:
            h["Referer"] = referer
        r = _session.get(url, headers=h, timeout=timeout)
        r.encoding = r.apparent_encoding or "utf-8"
        return r
    except Exception:
        return None


def _clean(s):
    s = re.sub(r"<[^>]+>", " ", s or "")
    return re.sub(r"\s+", " ", s).strip()


def search_web(query, num=3):
    """必应网页搜索 → [(标题, 摘要, url)]；失败返回 []"""
    import html as _html
    out = []
    try:
        url = "https://cn.bing.com/search?q=" + urllib.parse.quote(query) + "&mkt=zh-CN"
        r = _get(url, headers={"Referer": "https://cn.bing.com/"})
        if not r:
            return out
        h = r.text
        blocks = re.findall(r'<li class="b_algo".*?(?=<li class="b_algo"|</ol>)', h, re.S)
        for b in blocks[:num]:
            m = re.search(r'<h2[^>]*><a[^>]*href="([^"]+)"[^>]*>(.*?)</a>', b, re.S)
            m_p = re.search(r"<p[^>]*>(.*?)</p>", b, re.S)
            if m:
                t = re.sub(r"<[^>]+>", "", m.group(2))
                out.append((_html.unescape(_clean(t)),
                            _clean(m_p.group(1) if m_p else ""),
                            m.group(1)))
    except Exception:
        pass
    return out


def _page_summary(url):
    """抓链接正文摘要 → (标题, 描述/首段, 站点)"""
    try:
        host = urllib.parse.urlparse(url).netloc.lower()
        site = host.replace("www.", "").split(".")[0]
        r = _get(url, timeout=10)
        if not r:
            return "", "", site
        html = r.text
        title = ""
        mt = re.search(r"<title[^>]*>(.*?)</title>", html, re.S)
        if mt:
            title = _clean(mt.group(1))
        desc = ""
        for pat in (r'<meta[^>]+name="description"[^>]+content="([^"]*)"',
                    r'<meta[^>]+property="og:description"[^>]+content="([^"]*)"'):
            m = re.search(pat, html, re.I)
            if m:
                desc = _clean(m.group(1))
                break
        if not desc:
            # 取正文前几段文本
            body = re.sub(r"<script.*?</script>|<style.*?</style>", "", html, flags=re.S)
            body = _clean(body)
            # 去掉导航噪音:取 title 后一段
            idx = body.find(title[:20]) if title else -1
            desc = body[idx + len(title[:20]):idx + 400] if idx >= 0 else body[:300]
        return title, desc[:400], site
    except Exception:
        return "", "", ""


def read_link(url):
    """打开任意链接(网页/视频分享) → 一行可注入文本；失败返回 None"""
    try:
        u = url.strip()
        if not u.lower().startswith("http"):
            u = "https://" + u
        title, desc, site = _page_summary(u)
        if not title and not desc:
            return None
        parts = [f"【链接·{site}】{title}" if title else f"【链接·{site}】"]
        if desc:
            parts.append(desc)
        return "\n".join(parts)
    except Exception:
        return None


def is_link(text):
    """文本里是否含 http(s) 链接（含 b23.tv / v.kuaishou.com 等短链）"""
    return bool(re.search(r"https?://[^\s，。、；：！？（）<>一-鿿]+", text or ""))


def extract_link(text):
    m = re.search(r"https?://[^\s，。、；：！？（）<>一-鿿]+", text or "")
    return m.group(0) if m else None


def search_images(query, num=3):
    """必应图片搜索 → [{url, title}]（尽力取原图 murl）；失败返回 []"""
    out = []
    try:
        url = "https://cn.bing.com/images/search?q=" + urllib.parse.quote(query) + "&form=HDRSC2"
        r = _get(url, headers={"Referer": "https://cn.bing.com/"})
        if not r:
            return out
        html = r.text
        # murl 原图地址
        murls = re.findall(r'&quot;murl&quot;:&quot;(.*?)&quot;', html)
        if not murls:
            murls = re.findall(r'"murl":"(.*?)"', html)
        titles = re.findall(r'&quot;t&quot;:&quot;(.*?)&quot;', html) or \
            re.findall(r'"t":"(.*?)"', html)
        for i, murl in enumerate(murls[:num]):
            t = ""
            try:
                t = re.sub(r"\\u[0-9a-fA-F]{4}", "", titles[i]) if i < len(titles) else ""
            except Exception:
                pass
            out.append({"url": murl, "title": t[:80]})
    except Exception:
        pass
    return out


def search_videos(query, num=3):
    """bilibili 搜索视频 → [{bvid, title, author, play, cover, url}]；失败返回 []
    使用 wbi 搜索接口（无需登录签名），网页直链可收藏/发送。"""
    out = []
    try:
        import urllib.parse as _up2
        url = ("https://api.bilibili.com/x/web-interface/wbi/search/type"
               "?search_type=video&keyword=" + _up2.quote(query))
        r = _get(url, headers={"Referer": "https://www.bilibili.com/"}, timeout=8)
        if not r:
            return out
        j = r.json()
        results = ((j.get("data") or {}).get("result")) or []
        for v in results[:num]:
            if not isinstance(v, dict) or not v.get("bvid"):
                continue
            out.append({
                "bvid": v.get("bvid", ""),
                "title": (v.get("title") or "").replace('<em class="keyword">', "").replace("</em>", ""),
                "author": v.get("author", ""),
                "play": str(v.get("play", "0")),
                "cover": v.get("pic", ""),
                "url": f"https://www.bilibili.com/video/{v.get('bvid','')}",
            })
    except Exception:
        pass
    return out


def query_trigger(text) -> bool:
    """粗略判断是否在要求搜索/查资料/想搞懂什么（供对话注入）"""
    t = text or ""
    if is_link(t):
        return True
    for w in ("搜索", "搜一下", "查一下", "上网查", "百度", "谷歌", "不知道",
              "不认识", "不懂", "查查", "搜搜", "怎么搜", "帮我查", "帮我搜",
              "什么意思", "是什么", "如何", "怎么做", "怎么弄", "为什么"):
        if w in t:
            return True
    return False


def note_for_text(user_text, img_desc=""):
    """根据提问文本(可选附图片描述)返回联网参考附注；不适用返回 None"""
    try:
        if not query_trigger(user_text):
            return None
        link = extract_link(user_text)
        if link:
            info = read_link(link)
            if info:
                return f"【链接内容】{info}"
        # 图片不认识 → 用识别描述词去搜
        query = (img_desc or "").strip()
        q = query if (query and not user_text.strip()) else user_text.strip()
        if not q:
            return None
        q = q[:60]
        if len(q) < 2:
            return None
        # 若带明确问句但与事实无关则跳过(泛问题命中"是什么"但也可能闲聊)
        res = search_web(q, num=2)
        if not res:
            return None
        lines = []
        for t, d, u in res:
            lines.append(f"{t}：{d[:120]}" if d else t)
        return "【网络参考】" + "\n".join(lines)
    except Exception:
        return None
