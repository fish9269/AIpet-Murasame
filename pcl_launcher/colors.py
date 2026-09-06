# -*- coding: utf-8 -*-
"""PCL 颜色系统 + 主题加载 + 图标路径

主题机制：
- themes/<id>/theme.json 定义一套完整色板（含全部 Color*/Gray*/Red*/Green*/preview_bg/accent）
- 启动时按 config.json 的 ui_theme（默认 classic）加载色板，缺省字段回退内置经典值
- THEME_COLORS 提供 6 套强调色（blue/red/green/gold/dark/crimson），可随时切换
- 控件在构建时读取本模块颜色对象 → 切换主题后需重启启动器生效（应用主题会自动重启）
"""

from PyQt5.QtGui import QColor
import os
import sys
import json

# ===== 内置经典色板（回退基准 = 旧版主题）=====
_DEFAULT = {
    "Color1": "#343d4a", "Color2": "#0b5bcb", "Color3": "#1370f3",
    "Color4": "#4890f5", "Color5": "#96c0f9", "Color6": "#d5e6fd",
    "Color7": "#e0eafd", "Color8": "#eaf2fe",
    "Gray1": "#404040", "Gray2": "#606060", "Gray3": "#808080",
    "Gray4": "#a0a0a0", "Gray5": "#c0c0c0", "Gray6": "#e0e0e0",
    "Gray7": "#f0f0f0", "Gray8": "#f5f5f5",
    "RedBack": "#80fbdddd", "RedLight": "#ff4c4c", "RedDark": "#ce2111",
    "GreenLight": "#4cdd4c", "GreenDark": "#21aa11",
    "preview_bg": "#eaf2fe",
    "accent": "blue",
}


def _app_base_dir() -> str:
    """程序根目录：frozen 用 exe 目录；源码用项目根"""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _config() -> dict:
    try:
        with open(os.path.join(_app_base_dir(), "config.json"), "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _resolve_theme_dir() -> str:
    """主题目录：
    - 源码模式：pcl_launcher/themes
    - frozen：优先 exe 旁 pcl_launcher/themes（绿色版带源码，可持久导入/导出自定义主题）；
      不存在则回退打包进 _internal 的 themes（只读兜底）"""
    if getattr(sys, "frozen", False):
        side = os.path.join(os.path.dirname(sys.executable), "pcl_launcher", "themes")
        if os.path.isdir(side):
            return side
        return os.path.join(os.path.dirname(os.path.abspath(__file__)), "themes")
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "themes")


THEME_DIR = _resolve_theme_dir()


def _list_themes():
    """扫描 themes/ 目录返回 [{"id","name","builtin","desc","accent"}...]（按目录名排序）"""
    out = []
    if not os.path.isdir(THEME_DIR):
        return out
    for tid in sorted(os.listdir(THEME_DIR)):
        pj = os.path.join(THEME_DIR, tid, "theme.json")
        if not os.path.isfile(pj):
            continue
        try:
            with open(pj, "r", encoding="utf-8") as f:
                meta = json.load(f)
            out.append({
                "id": meta.get("id", tid),
                "name": meta.get("name", tid),
                "desc": meta.get("desc", ""),
                "accent": meta.get("accent", "blue"),
                "builtin": bool(meta.get("builtin", True)),
                "_dir": os.path.join(THEME_DIR, tid),
            })
        except Exception:
            continue
    return out


def _load_theme_palette(theme_id: str) -> dict:
    """加载主题色板（未命中字段回退经典色）"""
    pal = dict(_DEFAULT)
    if theme_id:
        pj = os.path.join(THEME_DIR, str(theme_id), "theme.json")
        try:
            if os.path.isfile(pj):
                with open(pj, "r", encoding="utf-8") as f:
                    data = json.load(f)
                for k, v in (data.get("colors") or {}).items():
                    pal[k] = v
                pal["accent"] = data.get("accent", pal.get("accent", "blue"))
        except Exception:
            pass
    return pal


def current_theme_id() -> str:
    cfg = _config()
    tid = str(cfg.get("ui_theme", "classic") or "classic").strip()
    # 主题目录不存在时回退经典
    if not os.path.isdir(os.path.join(THEME_DIR, tid)):
        return "classic"
    return tid


def _q(value):
    """hex 或 rgba() 文本 → QColor（失败回退灰）"""
    try:
        s = str(value).strip()
        if s.startswith("rgba("):
            s = s[5:-1]
            parts = [int(x.strip()) for x in s.split(",")]
            if len(parts) == 4:
                return QColor(parts[0], parts[1], parts[2], parts[3])
            if len(parts) == 3:
                return QColor(parts[0], parts[1], parts[2])
        return QColor(s)
    except Exception:
        return QColor("#808080")


# ===== 当前主题色板（启动时加载一次）=====
_PAL = _load_theme_palette(current_theme_id())
ACCENT_ID = str(_PAL.get("accent", "blue"))

# ===== 基础色板 =====
Color1 = _q(_PAL["Color1"])   # 文字主色
Color2 = _q(_PAL["Color2"])
Color3 = _q(_PAL["Color3"])   # 选中/激活色
Color4 = _q(_PAL["Color4"])   # hover
Color5 = _q(_PAL["Color5"])
Color6 = _q(_PAL["Color6"])
Color7 = _q(_PAL["Color7"])
Color8 = _q(_PAL["Color8"])   # 背景色

# ===== 灰色系 =====
Gray1 = _q(_PAL["Gray1"])
Gray2 = _q(_PAL["Gray2"])
Gray3 = _q(_PAL["Gray3"])
Gray4 = _q(_PAL["Gray4"])
Gray5 = _q(_PAL["Gray5"])
Gray6 = _q(_PAL["Gray6"])
Gray7 = _q(_PAL["Gray7"])
Gray8 = _q(_PAL["Gray8"])

# ===== 红色系 =====
RedBack = _q(_PAL["RedBack"])
RedLight = _q(_PAL["RedLight"])
RedDark = _q(_PAL["RedDark"])

# ===== 绿色 =====
GreenLight = _q(_PAL["GreenLight"])
GreenDark = _q(_PAL["GreenDark"])

# ===== 预览区背景（Live2D 清屏 / 立绘底）=====
PREVIEW_BG = _q(_PAL.get("preview_bg", "#eaf2fe"))

# ===== 6 套强调色（accent）=====
THEME_COLORS = {
    "blue": {
        "title_start": "#1370f3", "title_end": "#4890f5",
        "btn_start": "#4890f5", "btn_end": "#1370f3",
        "sidebar_bg": QColor(241, 255, 255, 242),
    },
    "red": {
        "title_start": "#e03030", "title_end": "#f06060",
        "btn_start": "#f06060", "btn_end": "#e03030",
        "sidebar_bg": QColor(255, 241, 241, 242),
    },
    "green": {
        "title_start": "#30a030", "title_end": "#60c060",
        "btn_start": "#60c060", "btn_end": "#30a030",
        "sidebar_bg": QColor(241, 255, 241, 242),
    },
    "gold": {
        "title_start": "#d4a020", "title_end": "#e8c040",
        "btn_start": "#e8c040", "btn_end": "#d4a020",
        "sidebar_bg": QColor(255, 251, 240, 242),
    },
    "dark": {
        "title_start": "#404040", "title_end": "#606060",
        "btn_start": "#606060", "btn_end": "#404040",
        "sidebar_bg": QColor(240, 240, 240, 242),
    },
    "crimson": {
        "title_start": "#8e2b3a", "title_end": "#c05a68",
        "btn_start": "#c05a68", "btn_end": "#8e2b3a",
        "sidebar_bg": QColor(253, 240, 235, 242),
    },
}

# ===== 图标路径（相对于 pcl_launcher 目录） =====
_RESOURCES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "resources", "blocks")


def block_icon(name: str) -> str:
    return os.path.join(_RESOURCES, f"{name}.png")


# ===== 主题装饰资源与控件风格（供标题栏/页面/按钮渲染） =====
def _current_theme_dir() -> str:
    return os.path.join(THEME_DIR, current_theme_id())


def _theme_json() -> dict:
    try:
        with open(os.path.join(_current_theme_dir(), "theme.json"), "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def theme_asset(rel: str) -> str:
    """返回当前主题目录内资源文件路径；不存在返回空串（调用方自行跳过）"""
    if not rel:
        return ""
    p = os.path.join(_current_theme_dir(), str(rel).replace("/", os.sep))
    return p if os.path.isfile(p) else ""


def title_decor_path() -> str:
    """标题栏装饰图（如樱花簇）；无则空串"""
    return theme_asset((_theme_json().get("decor") or {}).get("titlebar", ""))


def corner_decor_path() -> str:
    """窗口角落装饰图（如散落花瓣）；无则空串"""
    return theme_asset((_theme_json().get("decor") or {}).get("corner", ""))


def nav_icon_path(key: str) -> str:
    """导航按钮图（主题 nav_icons.<key>，如 model/settings）；无则空串"""
    return theme_asset((_theme_json().get("nav_icons") or {}).get(key, ""))


def background_info():
    """主题背景声明：返回 (type, path, opacity)。type 为 'image'/'video'/''"""
    try:
        bg = (_theme_json().get("background") or {}) or {}
        btype = str(bg.get("type", "")).strip().lower()
        src = theme_asset(str(bg.get("source", "") or ""))
        try:
            opacity = float(bg.get("opacity", 1.0))
        except Exception:
            opacity = 1.0
        if src and btype in ("image", "video"):
            return btype, src, max(0.0, min(1.0, opacity))
    except Exception:
        pass
    return "", "", 1.0


def btn_radius() -> int:
    """主题按钮圆角（卡通化用）"""
    try:
        return int((_theme_json().get("widgets") or {}).get("btn_radius", 8))
    except Exception:
        return 8


def primary_btn_qss(pad_v: int = 8, pad_h: int = 18, font_size: int = 13,
                    radius: int = None, gradient: bool = True) -> str:
    """统一卡通化主按钮 QSS：主题色渐变 + 高光 + 大圆角 + 白描边"""
    r = radius if radius is not None else btn_radius()
    if gradient:
        bg = (f"qlineargradient(x1:0,y1:0,x2:0,y2:1,"
              f"stop:0 {Color4.name()},stop:1 {Color3.name()})")
    else:
        bg = Color3.name()
    return f"""
        QPushButton {{ background: {bg}; color: white; border: 2px solid rgba(255,255,255,0.45);
            padding: {pad_v}px {pad_h}px; font-size: {font_size}px; font-weight: bold;
            font-family: 'Microsoft YaHei'; border-radius: {r}px; }}
        QPushButton:hover {{ border: 2px solid rgba(255,255,255,0.9);
            background: {Color4.name()}; }}
        QPushButton:pressed {{ background: {Color2.name()}; }}
        QPushButton:disabled {{ color: rgba(255,255,255,0.6); border-color: rgba(255,255,255,0.15); }}
    """


def nav_img_btn_qss() -> str:
    """导航图按钮（千恋万花素材按钮）——透明底，选中/悬停加亮框"""
    return f"""
        QPushButton {{ background: transparent; border: none; border-radius: {int(10*S)}px; }}
        QPushButton:hover {{ background: rgba(255,255,255,0.18); }}
        QPushButton:checked {{ background: rgba(255,255,255,0.30);
            border: 2px solid rgba(255,255,255,0.7); }}
    """


def nav_btn_qss() -> str:
    """导航按钮卡通化：未选中透明，选中/悬停呈白色半透明胶囊"""
    return f"""
        QPushButton {{ background: transparent; color: white; border: none;
            padding: {int(6*S)}px {int(16*S)}px; font-size: {int(13*S)}px;
            font-family: 'Microsoft YaHei'; border-radius: {int(16*S)}px; }}
        QPushButton:hover {{ background: rgba(255,255,255,0.20); }}
        QPushButton:checked {{ background: rgba(255,255,255,0.32);
            border: 1px solid rgba(255,255,255,0.6); }}
    """


# ===== 缩放系数 =====
S = 1.0  # 基础缩放（可根据屏幕调整）
