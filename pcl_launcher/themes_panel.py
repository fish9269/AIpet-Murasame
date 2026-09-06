# -*- coding: utf-8 -*-
"""PCL 主题管理面板 — 切换 / 导入 / 导出 / 删除主题。

- themes/<id>/theme.json：id/name/builtin/accent/colors
- classic 与 senrenbanka 为内置主题（不可删除、可导出）
- 应用主题：写 config.json 的 ui_theme → 通知主窗口重启启动器生效
"""
import os
import io
import sys
import json
import shutil
import zipfile

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QWidget, QLabel, QPushButton, QVBoxLayout, QHBoxLayout, QScrollArea,
    QFrame, QMessageBox, QFileDialog, QDialog, QInputDialog
)
from PyQt5.QtGui import QFont

from .colors import *
from .colors import _list_themes  # 下划线名不随 * 导出，需显式导入


class PCLThemeBgDialog(QDialog):
    """主题背景设置：选择图片或视频作为启动器背景（写入当前主题并重启生效）"""

    def __init__(self, meta, parent=None):
        super().__init__(parent)
        self._meta = meta
        self.setWindowTitle(f"🖼 {meta.get('name', meta.get('id'))} 背景设置")
        self.setMinimumWidth(int(470 * S))
        lay = QVBoxLayout(self)
        lay.setContentsMargins(int(18 * S), int(14 * S), int(18 * S), int(14 * S))
        lay.setSpacing(int(10 * S))

        try:
            if current_theme_id() == meta["id"]:
                btype, _bpath, _op = background_info()
            else:
                btype = ""
        except Exception:
            btype = ""
        cur = QLabel("当前背景：" + ("图片" if btype == "image" else
                                     ("视频" if btype == "video" else "无（纯色底）")))
        cur.setStyleSheet(f"color: {Color1.name()}; font-size: {int(13*S)}px;"
                          f"font-family: 'Microsoft YaHei';")
        lay.addWidget(cur)

        info = QLabel("选择后将复制到当前主题并自动重启生效。\n"
                      "图片支持 jpg/png；视频建议 H.264 编码的 mp4（wmv/avi 视系统解码器而定）。")
        info.setWordWrap(True)
        info.setStyleSheet(f"color: {Gray2.name()}; font-size: {int(12*S)}px;"
                           f"background: {Color6.name()}; border-radius: {int(4*S)}px;"
                           f"padding: {int(8*S)}px;")
        lay.addWidget(info)

        row = QHBoxLayout()
        btn_img = QPushButton("  🖼 选择背景图片")
        btn_vid = QPushButton("  🎞 选择背景视频")
        btn_rm = QPushButton("  移除背景")
        for b in (btn_img, btn_vid, btn_rm):
            b.setStyleSheet(primary_btn_qss(pad_h=12, font_size=12))
        btn_img.clicked.connect(lambda: self._pick("image"))
        btn_vid.clicked.connect(lambda: self._pick("video"))
        btn_rm.clicked.connect(self._remove_bg)
        row.addWidget(btn_img)
        row.addWidget(btn_vid)
        row.addWidget(btn_rm)
        lay.addLayout(row)

        btns = QHBoxLayout()
        btn_cancel = QPushButton("  取消")
        btn_cancel.setStyleSheet(primary_btn_qss(pad_h=18, font_size=13))
        btn_cancel.clicked.connect(self.reject)
        btns.addStretch()
        btns.addWidget(btn_cancel)
        lay.addLayout(btns)

    def _copy_into_theme(self, src, ext, kind):
        tdir = self._meta["_dir"]
        assets = os.path.join(tdir, "assets")
        os.makedirs(assets, exist_ok=True)
        for old in os.listdir(assets):
            if old.startswith("bg_custom"):
                try:
                    os.remove(os.path.join(assets, old))
                except Exception:
                    pass
        dst = os.path.join(assets, f"bg_custom.{ext}")
        shutil.copy2(src, dst)
        pj = os.path.join(tdir, "theme.json")
        tj = json.load(io.open(pj, encoding="utf-8"))
        tj["background"] = {"type": kind, "source": f"assets/bg_custom.{ext}", "opacity": 1.0}
        with io.open(pj, "w", encoding="utf-8") as f:
            json.dump(tj, f, ensure_ascii=False, indent=2)
        self.accept()

    def _pick(self, kind):
        if kind == "image":
            filt = "图片 (*.jpg *.jpeg *.png *.bmp)"
        else:
            filt = "视频 (*.mp4 *.mov *.wmv *.avi *.mkv)"
        path, _ = QFileDialog.getOpenFileName(self, "选择背景文件", "", filt)
        if not path:
            return
        ext = os.path.splitext(path)[1].lstrip(".").lower() or ("jpg" if kind == "image" else "mp4")
        self._copy_into_theme(path, ext, kind)

    def _remove_bg(self):
        tdir = self._meta["_dir"]
        pj = os.path.join(tdir, "theme.json")
        tj = json.load(io.open(pj, encoding="utf-8"))
        tj.pop("background", None)
        with io.open(pj, "w", encoding="utf-8") as f:
            json.dump(tj, f, ensure_ascii=False, indent=2)
        assets = os.path.join(tdir, "assets")
        if os.path.isdir(assets):
            for old in os.listdir(assets):
                if old.startswith("bg_custom"):
                    try:
                        os.remove(os.path.join(assets, old))
                    except Exception:
                        pass
        self.accept()

S = 1.0


def _app_base_dir() -> str:
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _config_path() -> str:
    return os.path.join(_app_base_dir(), "config.json")


def _load_config():
    try:
        with open(_config_path(), "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_config(cfg):
    try:
        p = _config_path()
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        os.replace(tmp, p)
    except Exception as e:
        print(f"[Themes] 保存配置失败: {e}")


class PCLThemesPanel(QScrollArea):
    """主题目录页：内置两套 + 自定义主题的导入/导出/应用/删除"""

    # 应用主题后由主窗口负责重启（theme_id）
    theme_applied = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setFrameShape(QScrollArea.NoFrame)
        self.setStyleSheet("QScrollArea { background: transparent; border: none; }")

        self._container = QWidget()
        self._layout = QVBoxLayout(self._container)
        self._layout.setContentsMargins(int(30 * S), int(30 * S), int(30 * S), int(30 * S))
        self._layout.setSpacing(int(14 * S))
        self.setWidget(self._container)

        title = QLabel("  🎨 主题目录")
        title.setFont(QFont("Microsoft YaHei", int(16 * S), QFont.Bold))
        title.setStyleSheet(f"color: {Color1.name()};")
        self._layout.addWidget(title)

        hint = QLabel("自由切换启动器样式：官方主题随程序自带、不可删除；所有主题均可重命名与打开目录查看文件。"
                      "想改官方主题的配色/壁纸等细节，可先「复制为我的主题」再自由修改。"
                      "应用主题后启动器会自动重启生效。")
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color: {Gray2.name()}; font-size: {int(12*S)}px;")
        self._layout.addWidget(hint)

        top = QHBoxLayout()
        btn_import = QPushButton("  📦 导入主题 (zip)")
        btn_refresh = QPushButton("  🔄 刷新")
        for b in (btn_import, btn_refresh):
            b.setStyleSheet(f"""
                QPushButton {{ background: {Color3.name()}; color: white; border: none;
                    padding: {int(8*S)}px {int(16*S)}px; font-size: {int(13*S)}px;
                    border-radius: {btn_radius()}px; font-family: 'Microsoft YaHei'; }}
                QPushButton:hover {{ background: {Color4.name()}; }}
            """)
        btn_import.clicked.connect(self._import_theme)
        btn_refresh.clicked.connect(self._reload)
        top.addWidget(btn_import)
        top.addWidget(btn_refresh)
        top.addStretch()
        self._layout.addLayout(top)

        self._list_widget = QWidget()
        self._list_layout = QVBoxLayout(self._list_widget)
        self._list_layout.setContentsMargins(0, 0, 0, 0)
        self._list_layout.setSpacing(int(10 * S))
        self._layout.addWidget(self._list_widget)

        path_lbl = QLabel(f"主题目录：{THEME_DIR}")
        path_lbl.setStyleSheet(f"color: {Gray3.name()}; font-size: {int(11*S)}px;")
        self._layout.addWidget(path_lbl)
        self._layout.addStretch()

        self._reload()

    # ---------- 列表 ----------
    def _section_label(self, text, count):
        lbl = QLabel(f"  {text}  <span style='color:{Gray3.name()};font-size:{int(11*S)}px;'>"
                     f"共 {count} 个</span>")
        lbl.setFont(QFont("Microsoft YaHei", int(13 * S), QFont.Bold))
        lbl.setStyleSheet(f"color: {Color1.name()}; margin-top: {int(8*S)}px;"
                          f"background: transparent; border: none;")
        return lbl

    def _reload(self):
        while self._list_layout.count():
            item = self._list_layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
        themes = _list_themes()
        if not themes:
            empty = QLabel("  暂无主题。可点击上方「导入主题」添加。")
            empty.setStyleSheet(f"color: {Gray2.name()}; font-size: {int(13*S)}px; padding: {int(16*S)}px;")
            self._list_layout.addWidget(empty)
            return
        cur = current_theme_id()
        official = [t for t in themes if t.get("builtin", True)]
        mine = [t for t in themes if not t.get("builtin", True)]

        # —— 官方主题（不可删除/修改）——
        self._list_layout.addWidget(self._section_label("🏛 官方主题", len(official)))
        for meta in official:
            self._list_layout.addWidget(self._make_card(meta, cur))
        if not official:
            self._list_layout.addWidget(QLabel("  暂无官方主题。"))

        # —— 我的主题（导入/复制，可删除/修改/重命名）——
        self._list_layout.addWidget(self._section_label("📁 我的主题", len(mine)))
        for meta in mine:
            self._list_layout.addWidget(self._make_card(meta, cur))
        if not mine:
            empty2 = QLabel("  暂无我的主题：可点击上方「导入主题 (zip)」导入，"
                            "或在官方主题卡片上点「复制为我的主题」。")
            empty2.setStyleSheet(f"color: {Gray2.name()}; font-size: {int(13*S)}px; padding: {int(12*S)}px;")
            self._list_layout.addWidget(empty2)

    def _make_card(self, meta, current):
        frame = QFrame()
        frame.setStyleSheet(f"""
            QFrame {{ background: {Color7.name()}; border: 1px solid {Color5.name()};
                border-radius: {int(8*S)}px; }}
        """)
        row = QHBoxLayout(frame)
        row.setContentsMargins(int(12*S), int(10*S), int(12*S), int(10*S))
        row.setSpacing(int(10*S))

        left = QVBoxLayout()
        left.setSpacing(int(2*S))
        is_cur = meta["id"] == current
        name = meta["name"]
        if is_cur:
            name += "   ✅ 当前使用"
        name_lbl = QLabel(name)
        name_lbl.setFont(QFont("Microsoft YaHei", int(14*S), QFont.Bold))
        name_lbl.setStyleSheet(f"color: {Color3.name() if is_cur else Color1.name()}; "
                               f"background: transparent; border: none;")
        left.addWidget(name_lbl)
        desc = QLabel(meta["desc"] + ("" if meta["builtin"] else "（我的主题）"))
        desc.setWordWrap(True)
        desc.setStyleSheet(f"color: {Gray2.name()}; font-size: {int(12*S)}px; background: transparent; border: none;")
        left.addWidget(desc)
        # 配色预览条
        sw = QHBoxLayout()
        sw.setSpacing(int(4*S))
        try:
            pj = os.path.join(meta["_dir"], "theme.json")
            if os.path.isfile(pj):
                with io.open(pj, encoding="utf-8") as f:
                    tjson = json.load(f)
                cols = tjson.get("colors") or {}
                for key in ("Color8", "Color6", "Color3", "Color2"):
                    if key in cols:
                        c = QLabel()
                        c.setFixedSize(int(26*S), int(14*S))
                        c.setStyleSheet(f"background: {cols[key]}; border-radius: {int(3*S)}px;")
                        sw.addWidget(c)
        except Exception:
            pass
        sw.addStretch()
        left.addLayout(sw)
        row.addLayout(left, 1)

        right = QHBoxLayout()
        right.setSpacing(int(6*S))
        _official = bool(meta["builtin"])
        small = f"""
            QPushButton {{ background: {Color6.name()}; color: {Color1.name()};
                border: 1px solid {Color5.name()}; padding: {int(4*S)}px {int(8*S)}px;
                font-size: {int(11*S)}px; border-radius: {int(4*S)}px;
                font-family: 'Microsoft YaHei'; }}
            QPushButton:hover {{ background: {Color4.name()}; color: white; }}
            QPushButton:disabled {{ color: {Gray4.name()}; }}
        """

        def _mk(text, tip=""):
            b = QPushButton(text)
            b.setStyleSheet(small)
            if tip:
                b.setToolTip(tip)
            return b

        # 按钮分两列排布，避免单列过长
        col_a = QVBoxLayout(); col_a.setSpacing(int(6*S))
        col_b = QVBoxLayout(); col_b.setSpacing(int(6*S))

        btn_apply = _mk("✅ 应用" if not is_cur else "使用中")
        btn_apply.setEnabled(not is_cur)
        btn_apply.clicked.connect(lambda: self._apply(meta))
        col_a.addWidget(btn_apply)

        btn_export = _mk("📤 导出")
        btn_export.clicked.connect(lambda: self._export(meta))
        col_a.addWidget(btn_export)

        if not _official:
            btn_bg = _mk("🖼 背景", "设置该主题的启动器背景图片/视频")
            btn_bg.clicked.connect(lambda: self._bg_setting(meta))
            col_a.addWidget(btn_bg)
        else:
            # 官方主题：改细节先复制为我的主题
            btn_copy = _mk("📑 复制为我的", "复制一份到「我的主题」，之后可自由修改背景、重命名或删除")
            btn_copy.clicked.connect(lambda: self._duplicate(meta))
            col_b.addWidget(btn_copy)

        # 所有主题都支持重命名与打开目录
        btn_rename = _mk("✏️ 重命名")
        btn_rename.clicked.connect(lambda: self._rename(meta))
        col_b.addWidget(btn_rename)
        btn_open = _mk("📂 打开目录", "打开该主题所在文件夹")
        btn_open.clicked.connect(lambda: self._open_dir(meta))
        col_b.addWidget(btn_open)
        if not _official:
            btn_del = _mk("🗑 删除")
            btn_del.clicked.connect(lambda: self._delete(meta))
            col_b.addWidget(btn_del)

        right.addLayout(col_a)
        right.addLayout(col_b)
        row.addLayout(right)
        return frame

    def _bg_setting(self, meta):
        """打开背景设置：选择图片/视频后提示重启生效"""
        dlg = PCLThemeBgDialog(meta, self)
        dlg.exec_()
        ret = QMessageBox.question(
            self, "背景已更新",
            "背景设置已写入该主题。\n立即重启启动器查看效果吗？\n（正在运行的 QQ AIpet 会被关闭，请稍后重新启动它）",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
        if ret == QMessageBox.Yes:
            cfg = _load_config()
            cfg["ui_theme"] = meta["id"]
            _save_config(cfg)
            self.theme_applied.emit(meta["id"])
        self._reload()

    # ---------- 动作 ----------
    def _apply(self, meta):
        if meta["id"] == current_theme_id():
            return
        ret = QMessageBox.question(
            self, "应用主题",
            f"确定应用主题「{meta['name']}」吗？\n应用后启动器将自动重启（正在运行的 QQ AIpet 会被关闭，请稍后重新启动它）。",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if ret != QMessageBox.Yes:
            return
        cfg = _load_config()
        cfg["ui_theme"] = meta["id"]
        _save_config(cfg)
        self.theme_applied.emit(meta["id"])

    def _export(self, meta):
        path, _ = QFileDialog.getSaveFileName(self, "导出主题", f"{meta['id']}_theme.zip",
                                              "主题包 (*.zip)")
        if not path:
            return
        try:
            with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
                base = meta["_dir"]
                for root, dirs, files in os.walk(base):
                    for fn in files:
                        full = os.path.join(root, fn)
                        rel = os.path.relpath(full, base)
                        zf.write(full, os.path.join(meta["id"], rel))
            QMessageBox.information(self, "导出成功", f"主题已导出到：\n{path}")
        except Exception as e:
            QMessageBox.warning(self, "导出失败", str(e))

    def _delete(self, meta):
        if meta["builtin"]:
            QMessageBox.information(self, "删除主题", "官方主题不可删除。")
            return
        ret = QMessageBox.question(self, "确认删除",
                                   f"确定删除自定义主题「{meta['name']}」吗？",
                                   QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if ret != QMessageBox.Yes:
            return
        try:
            shutil.rmtree(meta["_dir"], ignore_errors=True)
            # 若删除的是当前主题 → 回退经典
            cfg = _load_config()
            if cfg.get("ui_theme") == meta["id"]:
                cfg["ui_theme"] = "classic"
                _save_config(cfg)
                self.theme_applied.emit("classic")
            self._reload()
        except Exception as e:
            QMessageBox.warning(self, "删除失败", str(e))

    def _rename(self, meta):
        """主题重命名（官方/我的均可；仅改显示名 name，主题目录 id 不变）"""
        new_name, ok = QInputDialog.getText(
            self, "重命名主题", "输入新的主题名称：", text=meta["name"])
        if not ok:
            return
        new_name = (new_name or "").strip()
        if not new_name:
            QMessageBox.warning(self, "重命名主题", "名称不能为空。")
            return
        if len(new_name) > 40:
            QMessageBox.warning(self, "重命名主题", "名称过长（最多 40 字）。")
            return
        try:
            pj = os.path.join(meta["_dir"], "theme.json")
            tj = json.load(io.open(pj, encoding="utf-8"))
            tj["name"] = new_name
            with io.open(pj, "w", encoding="utf-8") as f:
                json.dump(tj, f, ensure_ascii=False, indent=2)
            print(f"[Themes] 主题 {meta['id']} 已重命名为: {new_name}")
            self._reload()
        except Exception as e:
            QMessageBox.warning(self, "重命名失败", str(e))

    def _open_dir(self, meta):
        """打开主题所在目录（资源文件浏览器）"""
        try:
            d = meta.get("_dir")
            if d and os.path.isdir(d):
                os.startfile(d)  # noqa
        except Exception as e:
            print(f"[Themes] 打开主题目录失败: {e}")

    def _duplicate(self, meta):
        """把官方主题复制一份到「我的主题」（id 加 _copy 后缀，builtin=False）"""
        try:
            base = str(meta["id"])
            cand = base + "_copy"
            i = 1
            while os.path.isdir(os.path.join(THEME_DIR, cand)):
                i += 1
                cand = f"{base}_copy{i}"
            dst = os.path.join(THEME_DIR, cand)
            shutil.copytree(meta["_dir"], dst)
            pj = os.path.join(dst, "theme.json")
            tj = json.load(io.open(pj, encoding="utf-8"))
            tj["id"] = cand
            tj["name"] = f"{meta['name']}（我的副本）" if i == 1 else f"{meta['name']}（我的副本{i}）"
            tj["builtin"] = False
            with io.open(pj, "w", encoding="utf-8") as f:
                json.dump(tj, f, ensure_ascii=False, indent=2)
            print(f"[Themes] 已复制为我的主题: {cand}")
            self._reload()
        except Exception as e:
            QMessageBox.warning(self, "复制失败", str(e))

    def _import_theme(self):
        path, _ = QFileDialog.getOpenFileName(self, "选择主题包 (zip)", "",
                                              "主题包 (*.zip);;所有文件 (*.*)")
        if not path:
            return
        try:
            with zipfile.ZipFile(path) as zf:
                names = zf.namelist()
                target = None
                for n in names:
                    if n.replace("\\", "/").endswith("theme.json"):
                        target = n
                        break
                if target is None:
                    QMessageBox.warning(self, "导入失败", "压缩包内未找到 theme.json")
                    return
                base_dir = target.replace("\\", "/").rsplit("/", 1)[0]
                meta = json.loads(zf.read(target).decode("utf-8"))
                tid = str(meta.get("id", "")).strip()
                if not tid or not str(tid).replace("_", "").isalnum():
                    QMessageBox.warning(self, "导入失败", "theme.json 缺少合法 id")
                    return
                dst = os.path.join(THEME_DIR, tid)
                if os.path.exists(dst):
                    QMessageBox.warning(self, "导入失败", f"主题 {tid} 已存在，请先删除旧版本")
                    return
                os.makedirs(dst, exist_ok=True)
                for n in names:
                    if n.endswith("/"):
                        continue
                    rel = os.path.relpath(n.replace("\\", "/"), base_dir) if base_dir else n
                    if rel.startswith(".."):
                        continue
                    out = os.path.join(dst, rel)
                    os.makedirs(os.path.dirname(out), exist_ok=True)
                    with open(out, "wb") as f:
                        f.write(zf.read(n))
            # 导入的主题一律标记为「我的主题」（非官方，即使原包是官方导出的）
            _tpj = os.path.join(dst, "theme.json")
            if os.path.isfile(_tpj):
                try:
                    with io.open(_tpj, encoding="utf-8") as f:
                        _m2 = json.load(f)
                    if isinstance(_m2, dict):
                        _m2["builtin"] = False
                        with io.open(_tpj, "w", encoding="utf-8") as f:
                            json.dump(_m2, f, ensure_ascii=False, indent=2)
                except Exception:
                    pass
            QMessageBox.information(self, "导入成功", f"主题「{meta.get('name', tid)}」已导入（我的主题）")
            self._reload()
        except Exception as e:
            QMessageBox.warning(self, "导入失败", f"导入出错：{e}")
