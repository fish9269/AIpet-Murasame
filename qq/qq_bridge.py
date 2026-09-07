# -*- coding: utf-8 -*-
"""
NapCat WebSocket 桥 — 连接 OneBot11 协议，收发 QQ 消息。

- 正向 WS（ws://127.0.0.1:3001）：接收 QQ 事件上报（消息/群@等）
- HTTP API（http://127.0.0.1:6099）：发送消息/图片/语音
  注意：6099 是 NapCat WebUI 面板端口，OneBot API 与 WS 同端。
  实际发消息通过 WS 发送 API 调用（send_msg 等），HTTP 备用。

实际实现：
- 连接正向 WebSocket 3001（OneBot11 事件上报 + API 调用共用）
- 收到私聊消息 → chat_once → 发文字 + 可选表情包 + 可选语音
  - 私聊：按标点切句逐条发送（模拟真人打字节奏）
  - 群聊：一次性发送完整回复
- 收到图片消息（私聊）→ 视觉模型识别（vision_model_name 配置）→ 注入对话回复
"""

import json
import os
import re
import time
import uuid
import threading
import requests
import websocket  # pip install websocket-client

from qq.qq_config import get_qq_config, STICKER_DIR, check_port_open, F5TTS_PORT
from qq.qq_chat import chat_once
from qq.qq_scheduler import MessageScheduler


def send_text(ws, text: str, message_type: str, target_id: int, self_id: int):
    """通过 WS 发送消息（正向 WebSocket 调用 API）"""
    payload = {
        "action": "send_msg",
        "params": {
            "message_type": message_type,
            "user_id" if message_type == "private" else "group_id": target_id,
            "message": text,
            "auto_escape": False,
        },
        "echo": f"send_{uuid.uuid4().hex[:8]}",
    }
    ws.send(json.dumps(payload, ensure_ascii=False))
    print(f"[QQBridge] → 发送文本到 {message_type}:{target_id}: {text[:40]}...")


def send_image(ws, image_path: str, message_type: str, target_id: int, self_id: int):
    """通过 WS 发送图片（本地文件路径）"""
    try:
        # 本地上传：NapCat 需要文件路径（绝对路径）
        payload = {
            "action": "send_msg",
            "params": {
                "message_type": message_type,
                "user_id" if message_type == "private" else "group_id": target_id,
                "message": [{"type": "image", "data": {"file": image_path}}],
            },
            "echo": f"send_img_{uuid.uuid4().hex[:8]}",
        }
        ws.send(json.dumps(payload, ensure_ascii=False))
        print(f"[QQBridge] → 发送图片: {os.path.basename(image_path)}")
    except Exception as e:
        print(f"[QQBridge] ⚠ 发送图片失败: {e}")


def send_voice(ws, voice_path: str, message_type: str, target_id: int, self_id: int):
    """通过 WS 发送语音（需 silk 格式；若为 wav 会尝试 NapCat 自动转换）"""
    try:
        payload = {
            "action": "send_msg",
            "params": {
                "message_type": message_type,
                "user_id" if message_type == "private" else "group_id": target_id,
                "message": [{"type": "record", "data": {"file": voice_path}}],
            },
            "echo": f"send_voice_{uuid.uuid4().hex[:8]}",
        }
        ws.send(json.dumps(payload, ensure_ascii=False))
        print(f"[QQBridge] → 发送语音: {os.path.basename(voice_path)}")
    except Exception as e:
        print(f"[QQBridge] ⚠ 发送语音失败: {e}")


def get_sticker_path(sticker_name: str):
    """根据表情包名返回文件路径（支持 gif/png/jpg；NapCat 富媒体不支持 avif）"""
    if not sticker_name:
        return None
    for ext in (".gif", ".png", ".jpg", ".jpeg"):
        p = os.path.join(STICKER_DIR, f"{sticker_name}{ext}")
        if os.path.exists(p):
            return p
    return None


# ── 私聊切句（按标点逐条发送）─────────────────────────────
# 强断句：句号/问号/感叹号/省略号/分号/换行（无条件切断）
_PRIVATE_STRONG_BREAKS = "。！？…；;\n"
# 右引号/闭标点（断句符后紧跟这些 → 并入前句）
_PRIVATE_RIGHT_QUOTES = {
    "\u201d",  # ” 中文右双引号
    "\u2019",  # ’ 中文右单引号
    "\u300d",  # 」 右方引号
    "\u300f",  # 』 右角引号
    "\u0022",  # " ASCII 双引号
    "\u0027",  # ' ASCII 单引号
}
_PRIVATE_MAX_LEN = 30  # 兜底强制切

# 私聊逐条发送间隔（秒）
_PRIVATE_SEND_INTERVAL = (0.6, 1.2)


def cap_clauses_count(clauses, max_msgs):
    """单次回复最多发送条数（0=不限）：

    切句后条数超过 max_msgs 时，前 max_msgs-1 条原样逐条发送，
    其余句子全部合并进最后一条一起发——内容不丢失，且一次回复
    发出的消息条数不会超过配置的「每次对话最多回复次数」，
    避免"一次回复被拆成 5 句 = 用户看到回了好几次"。"""
    try:
        m = int(max_msgs or 0)
    except Exception:
        m = 0
    if m <= 0 or not clauses or len(clauses) <= m:
        return clauses
    return clauses[:m - 1] + ["".join(clauses[m - 1:])]


def split_private_reply(reply: str):
    """
    将 AI 完整回复按强断句符切分为多条短消息。
    规则：
    - 强断句：。！？…；; 换行
    - 右引号随断句符并入前句
    - 不足 4 字的残段并入最后一条
    - 兜底 30 字强制切
    返回: [str, str, ...]
    """
    reply = (reply or "").strip()
    if not reply:
        return []

    clauses = []
    buffer = ""

    i = 0
    while i < len(reply):
        ch = reply[i]
        buffer += ch

        # 检查强断句符
        if ch in _PRIVATE_STRONG_BREAKS:
            # 并入后续右引号
            j = i + 1
            while j < len(reply) and reply[j] in _PRIVATE_RIGHT_QUOTES:
                buffer += reply[j]
                j += 1
            # 连续省略号
            while j < len(reply) and reply[j] == "\u2026":
                buffer += reply[j]
                j += 1
            i = j - 1
            # 切句（去掉首尾空白）
            clause = buffer.strip()
            if len(clause) >= 4:
                clauses.append(clause)
                buffer = ""
        # 兜底：超长无断句
        elif len(buffer) >= _PRIVATE_MAX_LEN:
            # 找最后一个逗号切（避免硬切）
            last_comma = max(buffer.rfind("，"), buffer.rfind(","), buffer.rfind("、"))
            if last_comma >= 4:
                clause = buffer[:last_comma + 1].strip()
                if clause:
                    clauses.append(clause)
                buffer = buffer[last_comma + 1:]
            else:
                clause = buffer.strip()
                if clause:
                    clauses.append(clause)
                buffer = ""
        i += 1

    # 剩余残段
    tail = buffer.strip()
    if tail:
        # 清理纯符号残留
        while tail and tail[0] in _PRIVATE_RIGHT_QUOTES:
            tail = tail[1:]
        if not tail:
            tail = ""
        if tail:
            if clauses:
                # 残段很短（<4字）→ 并入最后一条
                if len(tail) < 4:
                    clauses[-1] = clauses[-1] + tail
                else:
                    clauses.append(tail)
            else:
                clauses.append(tail)

    return clauses


class QQBotBridge:
    """NapCat 正向 WebSocket 桥接器"""

    def __init__(self):
        self.cfg = get_qq_config()
        self.ws_url = self.cfg["ws_url"]
        self.ws = None
        self.running = False
        self.self_id = None  # 登录的 QQ 号（识别是否自己发的消息）
        self._lock = threading.Lock()
        self._send_fail_count = 0  # 断线窗口内发送失败计数（重连成功后清零）
        self._stt_warm_started = False  # 语音模型预热只做一次（重连循环避免反复下载/刷屏）

        # 消息调度器：FIFO 队列 + 串行处理 + 会话合并
        self.scheduler = MessageScheduler(handler=self._handle_queued_message)

        # ===== 空闲自动离线（PCL 设置开关，默认关 = 始终活跃）=====
        # 主人白名单（qq_owner_id + qq_master_ids，最多 5 个）；第一位 = 主主人
        self._master_ids = []
        try:
            from qq.qq_config import get_qq_config as _gqc
            self._master_ids = list(_gqc().get("master_ids") or [])
        except Exception:
            self._master_ids = []
        self._owner_id = self._master_ids[0] if self._master_ids else None
        self._activity_lock = threading.Lock()
        self._last_activity = time.time()  # 最近一次有效对话时间（收到消息/发出回复）
        self._offline_mode = False         # 当前是否处于自动离线（离开）状态
        self._watcher_started = False

        # ===== 活泼模式（群聊间歇接话，活跃群气氛）=====
        self._groups_lock = threading.Lock()
        self._group_buf = {}               # group_id -> {last_others, last_bot_talk, recent[]}
        self._lively_last_global = 0.0     # 全局最近一次活泼发言时间（防刷屏）

        # ===== 对话调节（设置 → QQ配置）=====
        self._convo = {}       # session_key -> [已回复次数, 上次回复时间]
        self._seen_msg = {}    # message_id -> 到达时间（活消息去重，防重复回复）

        # ===== 群名缓存（get_group_info 异步查询，供身份标识使用）=====
        self._group_names_lock = threading.Lock()
        self._group_names = {}             # str(group_id) -> group_name
        self._group_names_pending = set()  # 已请求过、等待响应的群（防止重复请求）

    def connect(self):
        """建立 WebSocket 连接并进入事件循环（阻塞）。

        关键健壮性（面向 A 卡/NapCat 未熟配置的用户）：
        - 连接前先等待 NapCat WS 端口就绪（不 ready 则提示并重试，而非直接 Connection refused）
        - 主循环因任何原因断开后自动重连（不再一断就静默退出）
        """
        self.running = True  # 先置 running，_reconnect_loop 的 while 才会进入
        # 后台守护线程：空闲自动离线 + 活泼模式（只启动一次，重连期间持续运行）
        if not self._watcher_started:
            self._watcher_started = True
            threading.Thread(target=self._background_watcher, daemon=True,
                             name="QQBackgroundWatcher").start()
        host, port = self._ws_url_parts()
        if port and not self._napcat_ready(port, host=host):
            print(f"[QQBridge] ⏳ 等待 NapCat 就绪（{host}:{port}）..."
                  "若一直卡在这里，说明 NapCat 未正常启动/扫码，请运行 start_napcat.bat 并扫码登录。")
        self._reconnect_loop()

    @staticmethod
    def _ws_url_parts():
        """从 ws_url 解析 (host, port)（默认 127.0.0.1:3001）"""
        try:
            import urllib.parse as up
            u = up.urlparse(get_qq_config()["ws_url"])
            return (u.hostname or "127.0.0.1"), (u.port or 3001)
        except Exception:
            return "127.0.0.1", 3001

    @staticmethod
    def _ws_port():
        """从 ws_url 提取端口（默认 3001）"""
        return QQBotBridge._ws_url_parts()[1]

    @staticmethod
    def _napcat_ready(port, host="127.0.0.1", timeout=2, max_wait=30):
        """等待 NapCat WS 端口可连接（最多 max_wait 秒；host 跟随 ws_url，支持局域网部署）"""
        import socket as _sock
        import time as _t
        t0 = _t.time()
        while _t.time() - t0 < max_wait:
            try:
                with _sock.socket(_sock.AF_INET, _sock.SOCK_STREAM) as s:
                    s.settimeout(timeout)
                    return s.connect_ex((host, port)) == 0
            except Exception:
                pass
            _t.sleep(1)
        return False

    def _ws_auth_headers(self):
        """NapCat 正向 WS 开启 token 鉴权时的握手头（config.json 的 qq_napcat_token；
        未配置则返回 None → 不带鉴权头，兼容未开鉴权的 NapCat）"""
        token = str(self.cfg.get("napcat_token", "") or "").strip()
        if token:
            return [f"Authorization: Bearer {token}"]
        return None

    def _reconnect_loop(self):
        """断开后自动重连（不退出），给用户 NapCat 就绪时间窗口"""
        delay = 5
        while self.running:
            try:
                print(f"[QQBridge] 连接 NapCat: {self.ws_url}")
                self.ws = websocket.create_connection(
                    self.ws_url, timeout=30, enable_multithread=True,
                    header=self._ws_auth_headers(),
                )
                self._on_connected()   # 内部处理 login_info + 离线补拉 + 进入事件循环（阻塞）
                # 正常走到这里说明事件循环因断开退出 → 重置 delay 后重连
                if not self.running:
                    break
                print("[QQBridge] 连接断开，5 秒后自动重连...")
                delay = 5
                time.sleep(delay)
            except Exception as e:
                print(f"[QQBridge] ⚠ 连接失败: {e}")
                if not self._sleep(delay):
                    break
                # 指数退避，最多 30 秒
                delay = min(30, delay * 2)

    def _sleep(self, secs):
        import time as _t
        t0 = _t.time()
        while self.running and _t.time() - t0 < secs:
            _t.sleep(0.5)
        return self.running

    # ===== 空闲自动离线 + 活泼模式 =====

    def _is_owner(self, user_id) -> bool:
        """该消息是否来自主人白名单成员（自动离线的唤醒入口、主人功能判定）"""
        return bool(self._master_ids) and user_id is not None and str(user_id) in self._master_ids

    def _touch_activity(self):
        """记录一次有效活动（收到主人消息/正常对话）——空闲计时据此重置"""
        with self._activity_lock:
            self._last_activity = time.time()

    # ================= 对话调节（设置 → QQ配置） =================
    @staticmethod
    def _convo_dim_key(session_key):
        """对话计数维度：私聊按人（private_<QQ号>）；群聊按整个群计数
        （group_<群号>_u<QQ号> 剥离按人分仓后缀；活泼 lively_<群号> 归并到该群），
        使「每次对话最多回复次数」真正限制同一对话/同一个群的总回复量。"""
        sk = str(session_key or "")
        if sk.startswith("lively_"):
            return "group_" + sk[len("lively_"):]
        if sk.startswith("group_"):
            return "group_" + sk[len("group_"):].split("_u", 1)[0]
        return sk

    @staticmethod
    def _reply_limits():
        """实时读取对话调节配置（运行中修改即时生效，不依赖启动快照）。
        返回 (max_replies_per_conversation, max_reply_chars)，0=不限。"""
        try:
            from qq.qq_config import get_qq_config as _gqc
            c = _gqc()
            return (int(c.get("max_replies_per_conversation") or 0),
                    int(c.get("max_reply_chars") or 0))
        except Exception:
            return 0, 0

    def _cap_reply(self, reply):
        """单次回复字数上限（qq_max_reply_chars；0=不限）"""
        try:
            m = self._reply_limits()[1]
        except Exception:
            m = 0
        if m > 0 and reply and len(reply) > m:
            reply = reply[:m]
        return reply

    def _convo_allowed(self, session_key) -> bool:
        """每次对话最多回复次数（qq_max_replies_per_conversation；0=不限）。

        同一对话（私聊=该 QQ；群聊=整个群，含活泼接话）10 分钟内为一段对话：
        bot 每成功回复一次计数 +1，达到上限后保持沉默（不再回复），
        直到 10 分钟无回复自动重置。"""
        try:
            m = self._reply_limits()[0]
        except Exception:
            m = 0
        if m <= 0:
            return True
        session_key = self._convo_dim_key(session_key)
        now = time.time()
        cnt, last = self._convo.get(session_key, (0, 0.0))
        if now - last > 600:
            cnt = 0
        if cnt >= m:
            return False
        return True

    def _convo_note(self, session_key):
        """一次回复发送成功后计数（用于回复次数上限）"""
        try:
            m = self._reply_limits()[0]
        except Exception:
            m = 0
        if m <= 0:
            return
        session_key = self._convo_dim_key(session_key)
        now = time.time()
        cnt, last = self._convo.get(session_key, (0, 0.0))
        if now - last > 600:
            cnt = 0
        self._convo[session_key] = (cnt + 1, now)

    def _dedupe_msg(self, message_id) -> bool:
        """活消息去重：NapCat 偶发同一条消息上报两次（或与补拉撞车）。
        返回 True = 该 message_id 近期已见过（应跳过，防止重复回复）。"""
        if message_id is None:
            return False
        now = time.time()
        prev = self._seen_msg.get(message_id)
        if prev is not None and now - prev < 120:
            return True
        self._seen_msg[message_id] = now
        if len(self._seen_msg) > 1000:
            for k in [k for k, v in self._seen_msg.items() if now - v > 300]:
                self._seen_msg.pop(k, None)
        return False

    def _set_qq_online_status(self, status) -> bool:
        """把 QQ 在线状态设为 status（10=在线 30=离开 40=隐身 60=Q我吧 等）。
        仅作展示状态切换，不改变 WS 连接。"""
        return self._safe_send({
            "action": "set_online_status",
            "params": {"status": int(status), "ext_status": 0, "battery_status": 0},
            "echo": f"status_{uuid.uuid4().hex[:8]}",
        }, label="状态设置 ")

    def _wake_from_offline(self):
        """主人来消息 → 恢复在线状态并清除离线标志"""
        if not self._offline_mode:
            return
        self._offline_mode = False
        print("[QQBridge] 🌞 主人来消息了，已恢复在线状态")
        self._set_qq_online_status(10)

    def _enter_auto_offline(self):
        """空闲超时 → 自动进入离线（离开）模式：暂停自动回复，主人消息可唤醒"""
        self._offline_mode = True
        minutes = self.cfg.get("auto_offline_minutes", 30)
        print(f"[QQBridge] 🌙 已空闲 {minutes} 分钟，自动进入离线模式"
              "（QQ 状态=离开，暂停自动回复；主人发消息立即恢复在线）")
        self._set_qq_online_status(30)

    def _note_group_chat(self, group_id, user_id, nickname, text):
        """记录某群的一条他人消息文本（活泼模式的发言素材）。

        说话人统一硬标识：白名单主人 →「（主人）@昵称(QQ号)」；
        非白名单 →「@昵称(QQ号)」——让模型明确知道说话人身份，不会乱喊主人。"""
        try:
            content = (text or "").strip()
            if not content:
                content = "[图片/表情]"
            if len(content) > 60:
                content = content[:60] + "…"
            with self._groups_lock:
                g = self._group_buf.setdefault(str(group_id), {
                    "last_others": 0.0,    # 群里最近一次他人消息时间
                    "last_bot_talk": 0.0,  # bot 最近一次主动活泼发言时间
                    "recent": [],          # 最近若干条他人文本（带 @QQ号 硬标识）
                    "name": self._group_display_name(group_id),
                })
                g["last_others"] = time.time()
                # 统一「@昵称(QQ号)」标识；白名单主人额外加「（主人）」前缀
                master_tag = "（主人）" if self._is_owner(user_id) else ""
                who = f"{master_tag}@{nickname or '群友'}({user_id})"
                g["recent"].append(f"{who}: {content}")
                g["recent"] = g["recent"][-6:]
        except Exception as e:
            print(f"[QQBridge] ⚠ 记录群聊内容失败: {e}")

    def _ensure_group_name(self, group_id):
        """群名缓存：未知名则向 NapCat 异步查询一次 get_group_info（不阻塞收包线程）"""
        gid = str(group_id)
        with self._group_names_lock:
            if gid in self._group_names or gid in self._group_names_pending:
                return
            self._group_names_pending.add(gid)
        self._safe_send({
            "action": "get_group_info",
            "params": {"group_id": int(group_id)},
            "echo": f"grpname_{gid}",
        }, label="群名查询 ")

    def _group_display_name(self, group_id) -> str:
        """群显示名：缓存群名优先，未知名用群号兜底"""
        gid = str(group_id)
        with self._group_names_lock:
            return self._group_names.get(gid) or f"群{gid}"

    def _lively_tick(self):
        """活泼模式判定：找「最近有人说话 + 冷却已过 + 上次发言后又有人说话」的群，
        把一条主动接话任务交给调度队列（与正常回复完全串行，防记忆并发）"""
        try:
            # 运行中改配置也即时生效（get_qq_config 实时读 config.json）
            from qq.qq_config import get_qq_config as _cfg
            c = _cfg()
            if not (c.get("lively_enabled") and c.get("allow_groups")):
                return
            now = time.time()
            interval = float(c.get("lively_interval", 15)) * 60
            with self._groups_lock:
                candidates = []
                for gid, g in self._group_buf.items():
                    if now - g.get("last_others", 0) > 600:
                        continue  # 该群最近 10 分钟没人说话，不打扰
                    if not g.get("recent"):
                        continue
                    if now - g.get("last_bot_talk", 0) < interval:
                        continue  # 冷却未到
                    if g.get("last_others", 0) <= g.get("last_bot_talk", 0):
                        continue  # bot 上次发言后群里没有新动静（防自言自语）
                    candidates.append((g.get("last_others", 0), gid, g["recent"]))
            if not candidates:
                return
            # 全局防刷屏：两次主动活泼发言至少间隔 3 分钟
            if now - self._lively_last_global < 180:
                return
            # 选最近最热的群
            candidates.sort(key=lambda x: x[0], reverse=True)
            _, gid, recent = candidates[0]
            self._lively_last_global = now
            with self._groups_lock:
                buf = self._group_buf.get(str(gid))
                if buf:
                    buf["last_bot_talk"] = now
            print(f"[QQBridge] 🎉 活泼模式：群 {gid} 有新话题，主动接话")
            self.scheduler.enqueue({
                # 独立会话前缀，不与同群正常 @ 回复合并
                "session_key": f"lively_{gid}",
                "text": "\n".join(recent),
                "group_id": gid,
                "lively": True,
            })
        except Exception as e:
            print(f"[QQBridge] ⚠ 活泼模式判定失败: {e}")

    def _background_watcher(self):
        """后台守护线程：空闲自动离线（+活泼模式），每 20 秒轮询一次"""
        print("[QQBridge] 🔧 后台守护已启动（空闲自动离线 / 活泼模式）")
        warned_no_owner = False
        while self.running:
            try:
                from qq.qq_config import get_qq_config as _cfg
                c = _cfg()
                if c.get("auto_offline_enabled"):
                    if not self._offline_mode:
                        if self._owner_id:
                            with self._activity_lock:
                                idle_secs = time.time() - self._last_activity
                            if idle_secs >= float(c.get("auto_offline_minutes", 30)) * 60:
                                self._enter_auto_offline()
                        elif not warned_no_owner:
                            # 未配置主人 QQ 号 → 无唤醒入口，宁可不自动离线
                            warned_no_owner = True
                            print("[QQBridge] ⚠ 未配置主人 QQ 号（qq_owner_id），空闲自动离线已跳过")
                else:
                    # 运行中关闭开关 → 立即恢复在线（不再离线）
                    if self._offline_mode:
                        self._offline_mode = False
                        print("[QQBridge] ☀️ 自动离线已关闭，恢复在线状态")
                        self._set_qq_online_status(10)
                # 活泼模式：离线期间不主动发言
                if not self._offline_mode:
                    self._lively_tick()
            except Exception as e:
                print(f"[QQBridge] ⚠ 后台守护异常: {e}")
            self._sleep(20)

    def _handle_lively(self, msg: dict):
        """活泼模式发言：把群话题交给角色生成一句自然的话，直接发到群里（不带 @）。
        已由调度队列串行执行，不会与正常回复/记忆并发。
        对话调节：活泼接话同样受「每次对话最多回复次数」与字数上限约束
        （与同群 @ 回复共用计数维度，达到上限后本轮活泼静默）。"""
        try:
            group_id = msg.get("group_id")
            topic = (msg.get("text") or "").strip()
            if not group_id or not topic:
                return
            gkey = f"group_{group_id}"  # 与 @ 回复同群共用「每次对话」计数
            # 对话调节：先判回复次数上限（达上限直接静默，不浪费模型请求）
            if not self._convo_allowed(gkey):
                print(f"[QQBridge] 群{group_id} 已达回复上限，本轮活泼接话静默")
                return
            # 输入 = 群里刚才的聊天记录（他人所说，带 @QQ号 硬标识）；
            # 「主动接话」情景与自我回顾由 chat_once 的 lively 语境注入（不进记忆）
            group_name = self._group_display_name(group_id)
            user_input = f"（{group_name}里刚才的聊天记录）\n" + topic
            reply, stickers = chat_once(
                user_input,
                use_sticker=self.cfg["send_sticker"],
                session_key=f"group_{group_id}",  # 与 @ 回复共用群记忆 → 上下文连贯
                lively=True,
                group_name=group_name,
            )
            if not reply:
                return
            reply = reply.strip()
            # 只过滤 API 兜底文案（"（AI 暂时开小差了...）"这类）与超长刷屏；
            # 正常以动作描写"（…）"开头的活泼发言不算异常，不能误杀
            _api_fluff = ("（AI", "（网络", "（未配置", "（什么都没说")
            if reply.startswith(_api_fluff) or len(reply) > 200 or "开小差" in reply or "网络开小差" in reply:
                print(f"[QQBridge] ⚠ 活泼发言异常文案，跳过: {reply[:30]}")
                return
            # 对话调节：单次回复字数上限（0=不限时上面 200 字防刷屏兜底仍然有效）
            reply = self._cap_reply(reply)
            ok = self._safe_send({
                "action": "send_msg",
                "params": {
                    "message_type": "group",
                    "group_id": int(group_id),
                    "message": reply,
                },
                "echo": f"lively_{uuid.uuid4().hex[:8]}",
            }, label=f"活泼群{group_id} ")
            if ok:
                self._convo_note(gkey)
                print(f"[QQBridge] 🎉 活泼群 {group_id} 发言: {reply[:40]}...")
            for sticker in (stickers or []):
                path = get_sticker_path(sticker)
                if path:
                    try:
                        send_image(self.ws, path, "group", int(group_id), self.self_id)
                    except Exception:
                        pass
        except Exception as e:
            print(f"[QQBridge] ⚠ 活泼发言失败: {e}")

    def _on_connected(self):
        """连接建立后的初始化 + 事件循环（原 connect 主体，改为可被重连循环调用）"""
        self.running = True
        # 重连成功：QQ 重新登录默认在线，清除离线模式标记
        if self._offline_mode:
            self._offline_mode = False
            print("[QQBridge] 🔗 重连成功，退出离线模式")
        # 重连成功：若此前有发送失败，提示一次并清零计数
        if self._send_fail_count > 0:
            print(f"[QQBridge] 🔗 已重新连接（此前断线期间 {self._send_fail_count} 次回复未送达，请对方重发）")
            self._send_fail_count = 0

        # 获取登录信息（确认 self_id）—— 与离线拉取同一线程串行 recv，避免竞争
        try:
            self.ws.send(json.dumps({"action": "get_login_info", "echo": "login_info"}))
        except Exception:
            pass
        # 处理后到达的响应（login_info）
        try:
            raw = self.ws.recv()
            if raw:
                self._handle(raw)
        except Exception:
            pass

        # 同步拉取离线消息（在 while 循环前，单线程 recv 无竞争）
        self._offline_stop = threading.Event()
        stray_events = []
        seen_ids = set()
        try:
            from qq.qq_offline import fetch_before_loop
            from qq.qq_config import load_config as _lc
            owner = str((_lc() or {}).get("qq_owner_id", ""))
            if owner:
                stray_events, seen_ids = fetch_before_loop(self.ws, owner, self.scheduler, self_id=self.self_id) or ([], set())
            else:
                print("[QQBridge] ⚠ 未配置 qq_owner_id，跳过离线拉取")
        except Exception as e:
            print(f"[QQBridge] ⚠ 离线拉取异常: {e}")

        # 离线拉取期间到达的实时消息事件 → 补处理（此前被丢弃导致漏回复/回错人）
        # 与历史里见过的 message_id 去重：同一消息已被离线路径回复过就不再重复处理
        for raw in stray_events:
            try:
                data = json.loads(raw)
                mid = data.get("message_id")
                if mid is not None and str(mid) in seen_ids:
                    print(f"[QQBridge] ↷ 跳过补处理（离线路径已处理）mid={mid}")
                    continue
                self._handle(raw)
            except Exception as e:
                print(f"[QQBridge] ⚠ 补处理离线期间事件失败: {e}")

        # 打印语音服务状态（实时检测，不依赖 __init__ 快照）
        if self.cfg["send_voice"]:
            if check_port_open(F5TTS_PORT):
                self.cfg["f5tts_ready"] = True
                print(f"[QQBridge] 🎙 F5-TTS 服务就绪（端口 {F5TTS_PORT}），语音消息已开启")
            else:
                self.cfg["f5tts_ready"] = False
                print(f"[QQBridge] ⚠ F5-TTS 服务未运行（端口 {F5TTS_PORT}），语音消息将自动跳过")

        if self.cfg["vision_enabled"]:
            print("[QQBridge] 👁 图片识别已开启（qq_vision_enabled=true）")

        # 语音识别开启 → 后台预加载 Whisper 模型（避免首条语音阻塞收包线程数十秒）。
        # 只预热一次：断线自动重连会反复进入本函数，重复预热会每 5 秒尝试一次模型下载
        # （HuggingFace 不可达时刷屏 + 线程堆积）
        if self.cfg.get("stt_enabled") and not self._stt_warm_started:
            self._stt_warm_started = True
            print("[QQBridge] 🎤 语音识别已开启（qq_stt_enabled=true），后台预加载模型...")
            threading.Thread(target=self._warm_stt, daemon=True).start()

        print("[QQBridge] ✅ WebSocket 已连接，等待消息...")
        while self.running:
            try:
                raw = self.ws.recv()
                if not raw:
                    continue
                self._handle(raw)
            except websocket.WebSocketTimeoutException:
                # 超时保活
                try:
                    self.ws.send(json.dumps({"action": "get_login_info", "echo": "ping"}))
                except Exception:
                    pass
            except Exception as e:
                print(f"[QQBridge] ⚠ 接收异常: {e}")
                break
        # 断开后不置 running=False——由 _reconnect_loop 判断并自动重连。
        # 仅当外部调用 stop()（running=False）时才真正退出。
        try:
            self.ws.close()
        except Exception:
            pass
        print("[QQBridge] 连接已断开（将由重连循环自动恢复）")

    def _warm_stt(self):
        """后台预热 faster-whisper 模型（进程级单例，只加载一次）"""
        try:
            # 模型已本地缓存（首次由镜像下载）：离线模式加载，避免每次联网探测
            # huggingface.co（本机不可达）导致预热失败
            os.environ.setdefault("HF_HUB_OFFLINE", "1")
            from tool.stt import warmup
            warmup()
        except Exception as e:
            print(f"[QQBridge] ⚠ 语音识别模型预热失败: {e}")

    def _handle(self, raw: str):
        """处理一条 WS 消息（JSON）"""
        try:
            data = json.loads(raw)
        except Exception:
            return

        # 响应（echo 匹配）→ 处理登录信息 / 群名查询结果
        if "echo" in data and "data" in data:
            echo = data.get("echo", "")
            if echo == "login_info" and data.get("data"):
                info = data.get("data") or {}
                self.self_id = info.get("user_id")
                print(f"[QQBridge] 当前登录账号: {self.self_id} ({info.get('nickname', '')})")
            elif echo.startswith("grpname_"):
                gid = echo[len("grpname_"):]
                info = data.get("data") or {}
                gname = (info or {}).get("group_name") or ""
                with self._group_names_lock:
                    self._group_names_pending.discard(gid)
                    if gname:
                        self._group_names[gid] = gname
                # 同步到活泼群缓冲（后续接话带群名）
                with self._groups_lock:
                    buf = self._group_buf.get(gid)
                    if buf and gname:
                        buf["name"] = gname
                if gname:
                    print(f"[QQBridge] 群名缓存: {gid} -> {gname}")
            return

        # 事件上报
        post_type = data.get("post_type")
        if post_type != "message":
            return

        message_type = data.get("message_type")
        self_id = data.get("self_id")
        user_id = data.get("user_id")
        message_id = data.get("message_id")
        sender = data.get("sender", {})
        nickname = sender.get("nickname", "未知")
        # 群聊中优先用「群名片/群昵称」（card），让 bot 称呼对方时用 ta 在群里的名字，不认错人
        if message_type == "group":
            _card = (sender.get("card") or "").strip()
            if _card:
                nickname = _card
        group_id = data.get("group_id")
        raw_message = data.get("raw_message", "") or ""
        message = data.get("message", [])

        # 忽略自己发的消息
        if user_id == self.self_id:
            return

        # 活消息去重：同一条 message_id 短时间内重复上报 → 跳过（防重复回复同一问题）
        if self._dedupe_msg(message_id):
            print(f"[QQBridge] 忽略重复上报 message_id={message_id}（{nickname}）")
            return

        # ===== 空闲自动离线：离线期间只被「主人私聊 / 主人在群里点名 @」唤醒 =====
        if self._offline_mode:
            if message_type == "private":
                if not self._is_owner(user_id):
                    print(f"[QQBridge] 🌙 离线模式中，忽略私聊 {nickname}({user_id})（主人发消息可唤醒）")
                    return
            else:
                # 离线时群聊一律不响应，仅主人 @ 可唤醒
                if not (self._is_owner(user_id) and self._is_at_me(message, self_id)):
                    return
            self._wake_from_offline()

        from qq.qq_config import get_qq_config as _get_cfg
        cfg = _get_cfg()

        if message_type == "private":
            # 提取纯文本
            text = self._extract_text(message, raw_message)
            # 图片消息检测（私聊）
            vision_desc = None
            if cfg["vision_enabled"]:
                vision_desc = self._extract_private_image(message)
            # 语音消息识别（私聊）
            if cfg.get("stt_enabled", False):
                try:
                    from qq.qq_stt import extract_voice_path, transcribe_voice
                    vd = extract_voice_path(message)
                    if vd:
                        stt = transcribe_voice(vd, self.ws)
                        if stt:
                            text = (text + " " + stt).strip() if text.strip() else stt
                except Exception as e:
                    print(f"[QQBridge] ⚠ 语音识别异常: {e}")
            if not text.strip() and not vision_desc:
                return
            self._touch_activity()  # 有效对话 → 重置空闲计时
            print(f"[QQBridge] 私聊 {nickname}({user_id}): {text[:40]}")
            # 入调度队列（session_key = private_<QQ号>，串行处理不乱）
            self.scheduler.enqueue({
                "session_key": f"private_{user_id}",
                "text": text,
                "user_id": user_id,
                "nickname": nickname,
                "group_id": None,
                "vision_desc": vision_desc,
                "message_id": message_id,  # 回复成功后记录，防离线补拉重复回复
            })
        elif message_type == "group":
            # 群名异步缓存（身份标识需要知道在哪个群）
            self._ensure_group_name(group_id)
            # 活泼模式：先记录本条群聊内容（无论是否 @；供冷却后主动接话）
            group_text = self._extract_text(message, raw_message)
            if cfg.get("lively_enabled") and cfg["allow_groups"]:
                self._note_group_chat(group_id, user_id, nickname, group_text)
            # 群聊：仅 @丛雨 时回复；但玩法口令（galgame 开关/查看好感度）在群里免 @ 也可触发
            if not cfg["allow_groups"]:
                return
            if not self._is_at_me(message, self_id):
                low_t = (group_text or "").lower()
                is_cmd_txt = ("galgame模式" in low_t) or ("好感度" in group_text)
                if not is_cmd_txt:
                    return
                print(f"[QQBridge] 玩法口令(免@): {nickname}({user_id}): {group_text[:30]}")
            # 提取纯文本并去掉 @ 前缀后回复
            clean = self._strip_at(group_text)
            if not clean.strip():
                return
            self._touch_activity()  # 被点名 → 有效对话，重置空闲计时
            # 立即占用该群活泼冷却位：被 @ 的话题即将由正常回复回答，
            # 防止活泼模式在回复前把同一话题又插嘴一次（重复回复同一问题）
            if cfg.get("lively_enabled") and cfg.get("allow_groups"):
                try:
                    with self._groups_lock:
                        _buf = self._group_buf.get(str(group_id))
                        if _buf:
                            _buf["last_bot_talk"] = time.time()
                except Exception:
                    pass
            print(f"[QQBridge] 群聊 {nickname}({user_id}) @丛雨: {clean[:40]}")
            # 入调度队列（session_key = group_<群号>_u<QQ号>：按人分仓记忆，
            # 跟谁聊就只带谁的上下文，防止群里不同人的对话互相串味/认错人）
            self.scheduler.enqueue({
                "session_key": f"group_{group_id}_u{user_id}",
                "text": clean,
                "user_id": user_id,
                "nickname": nickname,
                "group_id": group_id,
                "vision_desc": None,
                "message_id": message_id,  # 回复成功后记录，防离线补拉重复回复
            })

    def _extract_text(self, message, raw_message):
        """
        从 message 段提取纯文本。
        纯图片/表情消息没有 text 段 → 返回空串（过滤掉所有 [CQ:...] 垃圾码）
        """
        texts = []
        if isinstance(message, list):
            for seg in message:
                if isinstance(seg, dict) and seg.get("type") == "text":
                    texts.append(seg.get("data", {}).get("text", ""))
        if texts:
            return "".join(texts)
        # 无 text 段 → 过滤 CQ 码后返回（避免 [CQ:image,file=...] 被当作对话文本）
        return re.sub(r"\[CQ:[^\]]*\]", "", raw_message or "").strip()

    def _extract_private_image(self, message):
        """
        提取私聊图片并识别。
        NapCat 的 image 段 file 常是 file_id（非本地路径）→ 用 get_image API 解析；
        返回: 识别描述文本；无图片/未启用/失败 → None。
        """
        try:
            from qq.qq_vision import (
                extract_image_path, describe_image, clean_vision_tmp,
                napcat_get_image, find_image_file_id,
            )
            print("[QQBridge] 👁 私聊图片消息，开始识图...")
            img_path = extract_image_path(message)
            stray = []
            if not img_path and self.ws:
                fid = find_image_file_id(message)
                if fid:
                    print(f"[QQBridge] 👁 本地无此文件，改用 NapCat get_image: {fid}")
                    img_path, stray = napcat_get_image(self.ws, fid)
            if not img_path:
                print("[QQBridge] ⚠ 无法取得图片（本地路径/URL/get_image 均失败），跳过识图")
                self._replay_stray(stray)
                return None
            print(f"[QQBridge] 👁 图片文件: {img_path}")
            desc = describe_image(img_path)
            # 清理临时文件（不删本地已有文件，只清我们下载的）
            clean_vision_tmp()
            self._replay_stray(stray)
            return desc or None
        except Exception as e:
            print(f"[QQBridge] ⚠ 图片识别异常: {e}")
            return None

    def _replay_stray(self, stray):
        """get_image 期间收到的实时事件 → 重新交给 _handle，保证不丢消息"""
        for raw in stray or []:
            try:
                self._handle(raw)
            except Exception as e:
                print(f"[QQBridge] ⚠ 补处理实时事件失败: {e}")

    def _is_at_me(self, message, self_id):
        """检查消息中是否 @ 了丛雨（只认 @自己的 QQ 号，@all 不触发回复）"""
        if isinstance(message, list):
            for seg in message:
                if isinstance(seg, dict) and seg.get("type") == "at":
                    qq = seg.get("data", {}).get("qq", "")
                    # 仅当 @ 的是本 bot 时才命中；@all/@everyone 不算（否则全群消息都会触发回复）
                    if qq and str(qq) == str(self_id):
                        return True
        # 兜底：raw_message 里包含 CQ:at 且 qq=self_id
        return False

    def _strip_at(self, text):
        """去除 @ 标记，保留正文"""
        import re
        text = re.sub(r"\[CQ:at[^\]]*\]", "", text)
        return text.strip()

    def _handle_queued_message(self, msg: dict):
        """
        调度器回调：串行处理一条（或合并后的）消息。
        msg 含: session_key / text / user_id / nickname / group_id / vision_desc
        """
        # 活泼模式：主动接话（不带 @ 的独立路径；与正常回复串行，防记忆并发）
        if msg.get("lively"):
            self._handle_lively(msg)
            return
        session_key = msg["session_key"]
        text = msg.get("text", "")
        vision_desc = msg.get("vision_desc")
        group_id = msg.get("group_id")
        user_id = msg.get("user_id")

        try:
            # 特殊指令处理（如 /clear 仅大号可用）
            try:
                from qq.qq_commands import handle_qq_command
                cmd_reply = handle_qq_command(text, session_key, user_id)
                if cmd_reply:
                    # 指令回复：一次性整条发送 + 不合成语音
                    if session_key.startswith("private_"):
                        self._send_command_reply(cmd_reply, user_id)
                    else:
                        self._send_group_command_reply(cmd_reply, user_id, group_id)
                    return
            except Exception as e:
                print(f"[QQBridge] ⚠ 指令处理异常: {e}")

            if session_key.startswith("private_"):
                user_id = msg["user_id"]
                # 对话调节：先判回复次数上限（达上限直接静默，不浪费模型请求）
                if not self._convo_allowed(session_key):
                    print(f"[QQBridge] 私聊{user_id} 本轮对话已达回复次数上限，保持沉默")
                    try:
                        self._mark_replied_msgs(msg)  # 标记已处理，防重连补拉再答
                    except Exception:
                        pass
                    return
                print(f"[QQBridge] → 回复目标 private {user_id} (session={session_key})")
                reply, stickers = chat_once(
                    text,
                    use_sticker=self.cfg["send_sticker"],
                    vision_desc=vision_desc,
                    session_key=session_key,
                    speaker={"nick": msg.get("nickname") or "", "uin": user_id},
                    is_master=self._is_owner(user_id),
                )
                if not reply:
                    return
                # 对话调节：单次回复字数上限
                reply = self._cap_reply(reply)
                sent_ok = self._send_private_reply(reply, stickers, user_id)
                # 仅整条回复发送成功后，才把本组全部 message_id 记为已处理：
                # - 发送失败若标记 → 重连补拉不再回答（漏回）
                # - 只记 first 而漏掉合并的第 2/3 条 → 重连补拉对它们重复回答（重复回）
                if sent_ok:
                    self._convo_note(session_key)
                    self._mark_replied_msgs(msg)
            elif session_key.startswith("group_"):
                user_id = msg["user_id"]
                # 对话调节：先判回复次数上限（群维度：同一群 10 分钟内所有回复合计）
                if not self._convo_allowed(session_key):
                    print(f"[QQBridge] 群{group_id} 本轮对话已达回复次数上限，保持沉默")
                    try:
                        self._mark_replied_msgs(msg)
                    except Exception:
                        pass
                    return
                reply, stickers = chat_once(
                    text,
                    use_sticker=self.cfg["send_sticker"],
                    session_key=session_key,
                    speaker={"nick": msg.get("nickname") or "", "uin": user_id},
                    is_master=self._is_owner(user_id),
                    group_name=self._group_display_name(group_id),
                )
                if not reply:
                    return
                # 对话调节：单次回复字数上限
                reply = self._cap_reply(reply)
                sent_ok = self._send_group_reply(reply, stickers, user_id, group_id)
                if sent_ok:
                    self._convo_note(session_key)
                    self._mark_replied_msgs(msg)
                    # 已回应过当前话题 → 清空该群活泼话题缓冲并记冷却，
                    # 防止活泼模式对同一句话再插嘴一次（用户反馈"一句话被回两次"）
                    try:
                        with self._groups_lock:
                            buf = self._group_buf.get(str(group_id))
                            if buf:
                                buf["recent"].clear()
                                buf["last_bot_talk"] = time.time()
                    except Exception:
                        pass
        except Exception as e:
            print(f"[QQBridge] ⚠ 处理消息异常: {e}")

    def _mark_replied_msgs(self, msg):
        """把本条（含合并子消息）的全部 message_id 记为已回复（去重用）。

        合并会话：scheduler 把同一会话连发的消息合并成一组（merged_msgs 含全部），
        必须收集全部 id——只记 first 会导致第 2/3 条被重连后的离线补拉重复回复。
        """
        mids = []
        merged = msg.get("merged_msgs") or [msg]
        for sub in merged:
            mid = (sub or {}).get("message_id")
            if mid is not None and mid not in mids:
                mids.append(mid)
        if not mids:
            return
        try:
            from qq.qq_offline import mark_processed
            mark_processed(mids)
        except Exception as e:
            print(f"[QQBridge] ⚠ 记录已回复 ID 失败: {e}")

    def _send_command_reply(self, text, user_id):
        """指令回复：一次性整条发送（不分条、不语音）"""
        try:
            with self._lock:
                self.ws.send(json.dumps({
                    "action": "send_msg",
                    "params": {
                        "message_type": "private",
                        "user_id": user_id,
                        "message": text,
                    },
                    "echo": f"cmd_{uuid.uuid4().hex[:8]}",
                }, ensure_ascii=False))
                print(f"[QQBridge] → 指令回复 {user_id}: {text[:50]}...")
        except Exception as e:
            print(f"[QQBridge] ⚠ 指令回复失败: {e}")

    def _send_group_command_reply(self, text, user_id, group_id):
        """群聊指令回复：一次性整条发送（带 @）"""
        try:
            at_msg = f"[CQ:at,qq={user_id}] {text}"
            with self._lock:
                self.ws.send(json.dumps({
                    "action": "send_msg",
                    "params": {
                        "message_type": "group",
                        "group_id": group_id,
                        "message": at_msg,
                    },
                    "echo": f"cmd_{uuid.uuid4().hex[:8]}",
                }, ensure_ascii=False))
                print(f"[QQBridge] → 群指令回复 {user_id}: {text[:50]}...")
        except Exception as e:
            print(f"[QQBridge] ⚠ 群指令回复失败: {e}")

    # ===== 断线安全的发送 =====
    def _safe_send(self, payload: dict, label: str = "") -> bool:
        """
        向 NapCat 发送一条 API 调用。断线/重连窗口内 self.ws 可能已关闭或为 None：
        发送失败不抛异常冒泡（会被调度线程吞掉造成丢消息），而是提示并计数返回 False。
        """
        if self.ws is None:
            self._send_fail_count += 1
            print(f"[QQBridge] ⚠ {label}发送失败：连接尚未建立（累计 {self._send_fail_count} 次发送失败）")
            return False
        try:
            with self._lock:
                self.ws.send(json.dumps(payload, ensure_ascii=False))
            return True
        except Exception as e:
            self._send_fail_count += 1
            print(f"[QQBridge] ⚠ {label}发送失败（连接可能已断开）: {e}"
                  f"（累计 {self._send_fail_count} 次发送失败，重连后请对方重发）")
            return False

    def _send_private_reply(self, reply, stickers, user_id):
        """私聊回复：按标点切句逐条发送 + 可选表情包(0~2个)/语音。
        返回 True = 文字部分完整发送成功（分句全部送达）；
        False = 断线/失败（调用方不应标记为已回复，避免重连补拉漏回）。
        对话调节：一次回复切句后若超过「每次对话最多回复次数」，
        自动把多余句子合并进最后一条（内容不丢，消息条数不超上限）。"""
        clauses = split_private_reply(reply)
        if not clauses:
            return True
        try:
            clauses = cap_clauses_count(clauses, self._reply_limits()[0])
        except Exception:
            pass
        if not clauses:
            return True

        for idx, clause in enumerate(clauses):
            ok = self._safe_send({
                "action": "send_msg",
                "params": {
                    "message_type": "private",
                    "user_id": user_id,
                    "message": clause,
                },
                "echo": f"reply_{uuid.uuid4().hex[:8]}",
            }, label=f"私聊{user_id} ")
            if not ok:
                return False  # 断线：文字未完整送达，不标记已回复
            if idx < len(clauses) - 1:
                print(f"[QQBridge] → 私聊 {user_id} 第{idx+1}/{len(clauses)}句: {clause[:30]}...")
                time.sleep(_PRIVATE_SEND_INTERVAL[0] + (_PRIVATE_SEND_INTERVAL[1] - _PRIVATE_SEND_INTERVAL[0]) * 0.3)
            else:
                print(f"[QQBridge] → 私聊 {user_id} 第{idx+1}/{len(clauses)}句: {clause[:30]}...")

        # 表情包（最后一条文字后发送，0~2 个；失败不影响"已回复"判定）
        for sticker in (stickers or []):
            path = get_sticker_path(sticker)
            if path:
                try:
                    send_image(self.ws, path, "private", user_id, self.self_id)
                except Exception:
                    pass

        # 语音（可选）
        if self.cfg["send_voice"]:
            try:
                self._send_voice(reply, "private", user_id)
            except Exception:
                pass
        return True

    def _send_group_reply(self, reply, stickers, user_id, group_id):
        """群聊回复：一次性发送完整回复 + 可选表情包(0~2个)/语音。
        返回 True = 发送成功；False = 断线/失败。"""
        # 群聊回复时加 @ 提问者（一次性发送）
        at_msg = f"[CQ:at,qq={user_id}] {reply}"
        ok = self._safe_send({
            "action": "send_msg",
            "params": {
                "message_type": "group",
                "group_id": group_id,
                "message": at_msg,
            },
            "echo": f"reply_{uuid.uuid4().hex[:8]}",
        }, label=f"群{group_id} ")
        if ok:
            print(f"[QQBridge] → 群 {group_id} 回复: {reply[:40]}...")
        else:
            return False
        for sticker in (stickers or []):
            path = get_sticker_path(sticker)
            if path:
                try:
                    send_image(self.ws, path, "group", group_id, self.self_id)
                except Exception:
                    pass
        if self.cfg["send_voice"]:
            try:
                self._send_voice(reply, "group", group_id)
            except Exception:
                pass
        return True

    def _send_voice(self, text, message_type, target_id):
        """合成语音并发送（F5-TTS；服务未就绪自动跳过）"""
        # 实时检测 F5-TTS 服务（避免使用 __init__ 时的旧快照）
        if not check_port_open(F5TTS_PORT):
            self.cfg["f5tts_ready"] = False
            print(f"[QQBridge] ⚠ F5-TTS 服务未运行（端口 {F5TTS_PORT}），跳过语音发送")
            return
        self.cfg["f5tts_ready"] = True
        try:
            from longtext.longtext_tts import LongTextVoice
            import tempfile
            tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".wav", dir="tmp")
            path = tmp.name
            tmp.close()
            voice = LongTextVoice()
            # 完整合成整条回复（不截断，QQ 语音无字数限制）
            voice.say(text, save_path=path, callback=lambda: send_voice(
                self.ws, path, message_type, target_id, self.self_id
            ))
        except Exception as e:
            print(f"[QQBridge] ⚠ 语音合成失败（跳过）: {e}")

    def stop(self):
        """停止连接"""
        self.running = False
        try:
            if self.ws:
                self.ws.close()
        except Exception:
            pass
        try:
            self.scheduler.stop()
        except Exception:
            pass
        try:
            from qq.qq_offline import save_last_exit_time
            save_last_exit_time()
        except Exception:
            pass
