# -*- coding: utf-8 -*-
"""
QQ 对话封装 — 复用长文本记忆 + 丛雨人设 + 流式 AI。

与 longtext/longtext_manager.py 的区别：
- 不走 TTS 播放（QQ 端文字/图片/可选语音）
- 一次性收集完整回复（不用切句）
- 使用独立线程锁，避免与桌宠同时写记忆冲突

记忆分仓（V1.7）：
- 大号私聊 → long_history.json + 同步 history.json（与桌宠共享）
- 其他私聊 → data/qq_memory/<QQ号>.json（独立）
- 群聊     → data/qq_memory/group_<群号>.json（按群分仓）
"""

import os
import re
import time
import json
import threading

import requests

from longtext.longtext_history import load_long_history, save_long_history, sync_to_short_history
from pets.pet_registry import get_prompt_path, get_sticker_dir, get_pet_config


BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 记忆线程锁：桌宠和 QQ 共用 long_history.json，必须互斥写
HISTORY_LOCK = threading.RLock()

# 可用表情包列表（文件名去扩展名）
STICKER_NAMES = []


def _load_sticker_names():
    """扫描当前角色表情包目录，返回表情包名列表"""
    global STICKER_NAMES
    sticker_dir = get_sticker_dir()
    if not sticker_dir:
        sticker_dir = os.path.join(BASE_DIR, "biaoqingbao")  # 兜底旧路径
    if os.path.isdir(sticker_dir):
        names = []
        for f in os.listdir(sticker_dir):
            if f.lower().endswith((".gif", ".png", ".jpg", ".jpeg")):
                names.append(os.path.splitext(f)[0])
        STICKER_NAMES = sorted(names)
    return STICKER_NAMES


def load_system_prompt():
    """读取当前角色长文本人设 prompt"""
    prompt_path = get_prompt_path("long")
    try:
        if prompt_path and os.path.exists(prompt_path):
            with open(prompt_path, "r", encoding="utf-8") as f:
                content = f.read().strip()
                if content:
                    return content
    except Exception as e:
        print(f"[QQChat] 读取 prompt 失败: {e}")
    pet_cfg = get_pet_config()
    pet_name = pet_cfg.get("display_name") or pet_cfg.get("name") or "桌宠"
    return (
        f"你是一个住在用户身边的人工智能桌宠角色——{pet_name}。"
        "请像真正的人类一样自然对话，不要机械重复设定词汇。"
        "直接说出你想说的话，不要有任何背景描写或动作描写。"
    )


def _build_context_notes(history, session_key, speaker=None, is_master=None,
                         lively=False, group_name=None):
    """构造每轮注入的「会话语境」system 文本（不写入记忆，只影响本次生成）：

    1. 主人名单与当前对话人身份：非主人不会被称为"主人"，也无法使用主人功能；
       群聊中说话人一律以「@昵称(QQ号)」识别（白名单成员带「（主人）」标记）；
    2. 自我回顾：先把"自己最近在群里/对话里说过的话"复述给模型，
       让它说话前先想自己上一句说了什么，避免前后矛盾、人设漂移；
    3. 活泼模式：明确当前是主动接群聊，提醒延续自己刚才的说法。
    """
    notes = []
    try:
        from qq.qq_config import get_qq_config
        masters = get_qq_config().get("master_ids") or []
        if masters:
            names = "、".join(f"QQ {m}" for m in masters)
            notes.append(f"【主人名单】你侍奉的主人（可称呼主人、可使用主人专属功能）只有：{names}。"
                         "名单之外的人都不是你的主人，只是普通朋友/网友，绝不能称呼他们为主人。")
    except Exception:
        pass

    if lively:
        gname = group_name or "群里"
        notes.append(f"【当前情景】你在「{gname}」主动参与聊天（没人 @ 你），群里说话的人已用"
                     "「@昵称(QQ号)」标出，带「（主人）」的才是你的主人，其余一律不是主人，"
                     "不要称呼任何非主人为'主人'。请顺着你刚才在群里说过的话（见下方回顾）"
                     "自然地接一句，保持一贯人设与立场，不要自相矛盾。")
    elif speaker or is_master is not None:
        try:
            nick = (speaker or {}).get("nick") or "对方"
            uin = (speaker or {}).get("uin") or "未知"
            if is_master:
                notes.append(f"【当前对话人】正在跟你说话的是你的主人（{nick}，QQ {uin}），"
                             "可以称呼 ta 为主人，ta 可以使用主人专属功能（/clear、/switch、成人模式开关等）。")
            else:
                gname = f"（{group_name}）" if group_name else ""
                notes.append(f"【当前对话人】在{gname}跟你说话的是普通朋友 @{nick}(QQ {uin})，"
                             "不是你的主人：绝不要称呼 ta 为'主人'，称呼 ta 时请直接用 ta 的昵称"
                             f"「{nick}」（或按上下文自然称呼），ta 不能使用主人专属功能，"
                             "但你可以正常友好地聊天。")
        except Exception:
            pass

    # 自我回顾：取会话记忆里自己（assistant）最近说过的 1~2 句
    try:
        mine = []
        for h in (history or []):
            if isinstance(h, dict) and h.get("role") == "assistant" and h.get("content"):
                mine.append(str(h.get("content", "")))
        if mine:
            last = " / ".join(mine[-2:]).replace("\n", " ")[:400]
            notes.append(f"【你刚才说过的话（自我回顾）】{last}"
                         "——说话前先回想这些，保持人设和说法前后一致，绝对不要自相矛盾。"
                         "注意：回顾里若曾把名单外的人误称为'主人'，那只是口误，现在请纠正，"
                         "只称呼主人名单内的人为主人。")
    except Exception:
        pass

    if notes:
        return "\n".join(notes)
    return None


def _build_messages(history):
    """
    构建消息列表（与 longtext_manager._chat_stream 同逻辑）：
    - priority=high → 提取为 system 级「最近的观察」
    - priority=low  → 完全过滤
    """
    messages = [{"role": "system", "content": load_system_prompt()}]

    high_observations = []
    for msg in (history or []):
        if not isinstance(msg, dict):
            messages.append(msg)
            continue
        pri = msg.get("priority")
        if pri == "high" and msg.get("content"):
            high_observations.append(msg.get("content", "").strip())
            continue
        if pri == "low":
            continue
        messages.append(msg)

    if high_observations:
        obs_text = "\n".join(f"- {obs}" for obs in high_observations[-5:])
        messages.append({
            "role": "system",
            "content": (
                "【最近的观察】你刚刚通过摄像头或屏幕看到了以下内容，"
                "这是你亲眼所见的事实，请自然地融入接下来的对话：\n"
                f"{obs_text}"
            ),
        })

    return messages


def _get_api_key():
    """读取 config 中的 qwen API Key"""
    import json as _json
    try:
        with open(os.path.join(BASE_DIR, "config.json"), "r", encoding="utf-8") as f:
            cfg = _json.load(f)
        return cfg.get("APIKEY", {}).get("qwen", "")
    except Exception:
        return ""


def _get_history_turns():
    """读取 config 的 longtext_max_history_turns（默认 20），统一桌宠与 QQ 记忆轮数"""
    import json as _json
    try:
        with open(os.path.join(BASE_DIR, "config.json"), "r", encoding="utf-8") as f:
            cfg = _json.load(f)
        val = int(cfg.get("longtext_max_history_turns", 20))
        return max(1, val)  # 至少 1 轮
    except Exception:
        return 20


def _load_session_history(session_key: str):
    """
    读取会话记忆：
    - session_key 为空 或 大号私聊 → long_history.json（共享）
    - 其他私聊/群聊 → 分仓记忆
    """
    turns = _get_history_turns()

    if not session_key:
        with HISTORY_LOCK:
            return load_long_history(max_turns=turns)

    try:
        from qq.qq_memory import resolve_memory_path
        path = resolve_memory_path(session_key)
        if path is None:
            # 大号 → 共享记忆
            with HISTORY_LOCK:
                return load_long_history(max_turns=turns)
        # 分仓
        from qq.qq_memory import load_memory
        return load_memory(session_key, max_turns=turns)
    except Exception as e:
        print(f"[QQChat] 读取分仓记忆失败: {e}")
        with HISTORY_LOCK:
            return load_long_history(max_turns=turns)


def _save_session_history(session_key: str, new_msgs: list):
    """
    保存会话记忆：
    - 大号私聊 → long_history.json + 同步 history.json
    - 其他 → 分仓文件（不写短文本）
    """
    turns = _get_history_turns()

    if not session_key:
        with HISTORY_LOCK:
            save_long_history(new_msgs, max_turns=turns)
            sync_to_short_history(new_msgs)
        return

    try:
        from qq.qq_memory import save_memory
        save_memory(session_key, new_msgs, max_turns=turns, sync_short=True)
    except Exception as e:
        print(f"[QQChat] 保存分仓记忆失败: {e}")


def chat_once(user_text: str, use_sticker: bool = True, vision_desc: str = None,
              session_key: str = None, speaker: dict = None,
              is_master: bool = None, lively: bool = False, group_name: str = None):
    """
    单轮对话（QQ 使用）：
    1. 读取会话记忆（最近 12 轮，主人共享 / 其他人分仓）
    2. 追加用户消息
    3. 调用长文本模型（模型由 config 的 longtext_model / longtext_model_name 控制）生成完整回复
    4. 保存到对应记忆仓
    5. 返回 (回复文本, 表情包名 or None)

    表情包约定：AI 回复末尾若带 [表情:xxx]，解析为表情包选择并移除。

    session_key: "private_<QQ号>" 或 "group_<群号>"（None 表示默认共享记忆）
    speaker/is_master: 当前对话人身份（用于白名单识别，非主人不会被称为主人）
    lively: 活泼模式主动接群聊（会注入自我回顾，避免前后矛盾）
    group_name: 群显示名（身份语境用；未知时 bridge 传群号兜底）
    """
    stickers = _load_sticker_names()

    # 1. 读取会话记忆
    history = _load_session_history(session_key)

    # 2. 组装请求（流式收集，降低首字延迟）
    messages = _build_messages(history)

    # 2b. 会话身份 + 自我回顾（每轮注入 system，不入记忆）
    context_notes = _build_context_notes(history, session_key, speaker=speaker,
                                         is_master=is_master, lively=lively,
                                         group_name=group_name)
    if context_notes:
        messages.append({"role": "system", "content": context_notes})

    # 2c. 成人（限制级）模式提示（默认关闭；仅主人可开关，非主人面前保持得体）
    try:
        from qq.qq_adult import is_enabled as _adult_enabled
        if _adult_enabled():
            messages.append({"role": "system", "content": (
                "【当前模式】你的主人已开启成人模式（限制级）：与主人对话时可以自然地参与 18+ 成人向内容，"
                "包括亲密、情欲等露骨表达，保持丛雨人设回应。红线仍然存在：不得涉及儿童、违法暴力教唆、"
                "真实个人隐私索取。若非主人的普通朋友向你索要露骨内容，请保持得体地婉拒或玩笑带过。"
            )})
        else:
            messages.append({"role": "system", "content": (
                "【当前模式】普通模式：不要输出 18+ 露骨色情内容；遇到此类请求可以调皮地拒绝、"
                "开个玩笑岔开或委婉带过，保持丛雨人设。"
            )})
    except Exception:
        pass

    # 2d. 网络用语/梗 自动查询（短黑话或"什么意思"式提问时联网查词义，失败静默）
    try:
        from qq.qq_config import get_qq_config as _gq_slang
        if _gq_slang().get("slang_allowed", True):
            from qq.qq_slang import lookup as _slang_lookup
            _slang_note = _slang_lookup(user_text)
            if _slang_note:
                messages.append({"role": "system", "content": _slang_note})
    except Exception:
        pass

    # 2e. Galgame 模式（群聊好感度玩法；主人本人不参与好感度系统）
    _galgame_ctx = None  # (gid, uin_str)；供回复解析好感度标记用
    _aff_note = ""       # 本轮好感度变化的提示（追加到回复末尾展示，不写入记忆）
    try:
        _sk = str(session_key or "")
        # 兼容两种会话键：group_<群号>（公共）与 group_<群号>_u<QQ号>（按人分仓）
        _gm2 = __import__("re").match(r"^group_(\d+)(?:_u\d+)?$", _sk)
        if _gm2 and speaker and speaker.get("uin"):
            _gid = _gm2.group(1)
            _uin = str(speaker.get("uin"))
            _is_master = bool(is_master)
            from qq.qq_galgame import group_enabled, get_affection, is_date_intent
            # 插件总开关：启动器「插件」页可停用整个 Galgame 玩法
            try:
                from qq.qq_config import get_qq_config as _gq_g
                _gal_allowed = _gq_g().get("galgame_allowed", True)
            except Exception:
                _gal_allowed = True
            _galgame_on = group_enabled(_gid) and _gal_allowed
            if not _galgame_on and _gal_allowed:
                # 群未开启 Galgame：若对方执行玩法动作（约会等）→ 明确提示还没进模式
                try:
                    if is_date_intent(user_text):
                        messages.append({"role": "system", "content": (
                            f"【Galgame 提示】正在跟你说话的 {speaker.get('nick') or 'ta'}"
                            f"(QQ {_uin}) 想约你出去玩，但你们**还没有开启 Galgame 模式**。"
                            "请以丛雨人设俏皮地婉拒，并提醒 ta：要先在这个群里说「开启galgame模式」"
                            "才能开始好感度养成和约会玩法（不要真的答应约会，也不要结算好感度）。"
                        )})
                except Exception:
                    pass
            if _galgame_on:
                _galgame_ctx = (_gid, _uin)
                _aff = get_affection(_gid, _uin)
                if _aff >= 90:
                    _tier = "亲密恋人档（90+）：可答应较亲密的互动、满足较过分的要求（每满足一次扣 10~20 好感，用标记表达）"
                elif _aff >= 70:
                    _tier = "亲近档（70~89）：可适当亲密互动（牵手、撒娇、抱抱等），偶尔满足不太过分的要求"
                elif _aff >= 50:
                    _tier = "普通朋友档（50~69）：正常友好聊天，可轻微暧昧但不过线，拒绝过分要求"
                elif _aff >= 30:
                    _tier = "冷淡档（30~49）：保持距离，拒绝亲密互动与过分要求"
                else:
                    _tier = "讨厌档（0~29）：爱答不理，明确拒绝一切亲密与过分要求"
                if _is_master:
                    # 主人：好感恒 100 不增减，但同样显示状态提示（让主人知道在模式里）
                    messages.append({"role": "system", "content": (
                        f"【Galgame 模式·好感度】正在和你说话的是你的主人 @{speaker.get('nick') or '主人'}"
                        f"(QQ {_uin})。主人对本座的好感恒定为 100/100，不会增减。"
                        "你可以尽情撒娇甜蜜、亲密互动。若 ta 这句话让你心动，可在回复末尾附"
                        " [好感+数字]（仅作状态显示，不会真的变化）；中性内容可不写。保持丛雨人设。"
                    )})
                else:
                    messages.append({"role": "system", "content": (
                        f"【Galgame 模式·好感度养成】正在和你对话的是普通群友 @{speaker.get('nick') or 'ta'}"
                        f"(QQ {_uin})，不是你的主人，用 ta 的昵称称呼即可。\n"
                        f"- ta 当前对你的好感度：{_aff} / 100（初始 50）。\n"
                        f"- 关系档位：{_tier}。\n"
                        "【好感度判定——每一轮对话都必须执行】根据 ta 说的这句话，在回复的**末尾**附上标记：\n"
                        "· 夸奖、关心、有趣、体贴、哄你开心 → [好感+N]，N 取 2~8（越讨你喜欢越高）\n"
                        "· 冒犯、没礼貌、命令、粗鲁、贬低 → [好感-N]，N 取 2~8\n"
                        "· 完全中性（问规则、聊天气等）→ 不写标记\n"
                        "· 若你答应了 ta 的过分要求/亲密互动，可用大额扣分标记如 [好感-15]（-10~-20 都支持）\n"
                        "标记会被代码自动生效并从对话中移除，正文里不要自己提加减数字。"
                    )})

                # ---- 约会事件：成员提出约会 → 按好感度 roll 成败（大幅加减好感）----
                try:
                    from qq.qq_galgame import (
                        is_date_intent, date_available, mark_date_used,
                        roll_date, apply_change as _aff_apply,
                    )
                    if is_date_intent(user_text) and _is_master:
                        # 主人约会：好感恒 100，无需 roll，直接甜蜜应允
                        messages.append({"role": "system", "content": (
                            "【约会事件】你的主人约你出去玩——当然要开心地答应啦！"
                            "以丛雨人设甜蜜自然地回应这次邀约（主人好感恒 100，不结算变化），2~4 句。"
                        )})
                    elif is_date_intent(user_text):
                        if date_available(_gid, _uin):
                            mark_date_used(_gid, _uin)
                            _ok, _delta = roll_date(_aff)
                            _new_aff = _aff_apply(_gid, _uin, _delta, big=True)
                            _galgame_ctx = None  # 约会已大额结算，本轮不再解析小标记
                            _aff_note = f"（好感度 {_delta:+d}，当前 {_new_aff}）"
                            if _ok:
                                messages.append({"role": "system", "content": (
                                    f"【约会事件·成功】ta 鼓起勇气约你出去玩，你答应了！"
                                    f"（好感 {_delta:+d} → {_new_aff}，已自动结算）"
                                    "请以丛雨人设自然演绎：按当前好感档位决定亲密度与语气"
                                    "（档位高可更甜蜜亲密），回应这次约会，语气活泼，2~4 句即可。"
                                    "本轮不要写 [好感] 标记。"
                                )})
                            else:
                                messages.append({"role": "system", "content": (
                                    f"【约会事件·失败】ta 约你出去玩，但你婉拒了。"
                                    f"（好感 {_delta:+d} → {_new_aff}，已自动结算）"
                                    "请以丛雨人设自然演绎拒绝的理由与态度（符合当前好感档位，"
                                    "比如'今天要陪主人/没心情/改天吧'，不要太伤人但也不要答应），"
                                    "2~4 句即可。本轮不要写 [好感] 标记。"
                                )})
                        else:
                            # 冷却中：提醒模型不要接受约会（正常聊天）
                            messages.append({"role": "system", "content": (
                                "【约会事件·冷却】ta 向你提出约会，但你刚结束上一次约会不久"
                                "（约会有冷却时间）。本轮正常聊天，可以俏皮地表示'改天再说'，"
                                "但不要真的答应，也不要用 [好感] 标记结算。"
                            )})
                except Exception:
                    pass
    except Exception:
        pass

    # 当前时间 + 实时天气事实注入（只影响发给模型的内容，不写入记忆）：
    # 模型据此如实回答"现在几点/今天天气"，不再靠猜或含糊其辞。
    # 时间每轮都带（精确到分钟）；天气仅在命中提问关键词时联网查询一次（带缓存）。
    _fact_prefix = ""
    try:
        from tool.time_utils import build_time_context as _btc2
        from tool.weather_utils import weather_note_if_asked as _wn2
        _fact_prefix = f"[{_btc2()}]"
        _wx2 = _wn2(user_text)
        if _wx2:
            _fact_prefix += "\n" + _wx2
        _fact_prefix += "\n"
    except Exception:
        _fact_prefix = ""

    # 图片消息处理：text 为空但有 vision_desc → 用图片描述作为真实用户输入
    # （避免 [CQ:image...] 垃圾文本被当作对话内容，导致 AI 依赖历史记忆误判）
    if vision_desc:
        if user_text and user_text.strip():
            messages.append({
                "role": "user",
                "content": f"{_fact_prefix}主人发来了一张图片，图片内容：{vision_desc}\n主人的话：{user_text}",
            })
        else:
            messages.append({
                "role": "user",
                "content": f"{_fact_prefix}主人发来了一张图片，图片内容：{vision_desc}",
            })
    else:
        messages.append({"role": "user", "content": f"{_fact_prefix}{user_text}"})

    # 表情包指令（仅当启用且存在表情包时）
    # 允许 0~2 个：AI 根据语境自主决定发不发、发几张
    if use_sticker and stickers:
        sticker_hint = (
            "\n\n【表情包】回复的最后（换行后）可以根据语境附带 0~2 个表情包标记，"
            f"从以下列表中选择最贴合语境的一个或多个：{'、'.join(stickers)}。"
            '格式为 [表情:名称]，例如 [表情:撒娇] 或 [表情:思考][表情:肯定]。'
            '如果不需要表情包就不发，不要为了发而发。'
        )
        # 把表情包指令附加到最后一条 user 消息上
        if messages and messages[-1]["role"] == "user":
            messages[-1]["content"] += sticker_hint
        else:
            messages.append({"role": "user", "content": sticker_hint})

    # Galgame 好感标记提示：紧贴用户消息（提升模型遵从率；仅日常轮，约会轮不加）
    if _galgame_ctx:
        try:
            _g_hint = ("\n\n（当前是好感度玩法对话：如果上面这句话值得加分或扣分，"
                       "请在你回复的最后单独写一行 [好感+数字] 或 [好感-数字]，"
                       "数字 2~8；完全中性的内容不用写。这一行不会被对方看到，会自动结算）")
            if messages and messages[-1]["role"] == "user":
                messages[-1]["content"] += _g_hint
            else:
                messages.append({"role": "user", "content": _g_hint})
        except Exception:
            pass

    # 3. 从 model_config 获取长文本模型（qwen / deepseek）
    from longtext.model_config import get_longtext_model_config
    mcfg = get_longtext_model_config()
    if not mcfg:
        return "（未配置对话模型 API Key）", None

    url = mcfg["url"]
    model_name = mcfg["model"]
    api_key = mcfg["api_key"]

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model_name,
        "messages": messages,
        "max_tokens": 512,  # QQ 场景短回复更自然
        "stream": True,
    }
    # 推理等级附加参数（off 时可能为空 dict）
    payload.update(mcfg.get("reasoning", {}) or {})

    # 3. 流式收集完整回复
    full_reply = ""
    try:
        with requests.post(url, json=payload, headers=headers, stream=True, timeout=(15, 120)) as resp:
            if resp.status_code != 200:
                err_text = resp.text[:300]
                print(f"[QQChat] ⚠ API 错误 {resp.status_code}: {err_text}")
                return "（AI 暂时开小差了...）", None
            for line in resp.iter_lines(decode_unicode=True):
                if not line or not line.startswith("data:"):
                    continue
                data_str = line[5:].strip()
                if data_str == "[DONE]":
                    break
                try:
                    chunk = json.loads(data_str)
                    delta = chunk.get("choices", [{}])[0].get("delta", {})
                    if delta.get("reasoning_content"):
                        continue
                    content = delta.get("content")
                    if content:
                        full_reply += content
                except Exception:
                    continue
    except Exception as e:
        print(f"[QQChat] ⚠ 请求异常: {e}")
        return "（网络开小差了，等下再试试~）", None

    if not full_reply.strip():
        return "（什么都没说出来...）", None

    full_reply = full_reply.strip()

    # 4. 解析表情包标记（支持 0~2 个，去重）
    sticker_names = []
    if use_sticker and stickers:
        matches = re.findall(r"\[表情\s*[:：]\s*([^\]]+)\]", full_reply)
        for name in matches:
            name = name.strip()
            if name in stickers and name not in sticker_names:
                sticker_names.append(name)
        if matches:
            full_reply = re.sub(r"\[表情\s*[:：]\s*[^\]]+\]", "", full_reply).strip()

    # 4b. Galgame 好感度标记 [好感+N] / [好感-N] → 应用到对应群/人并从文本移除
    if _galgame_ctx:
        try:
            _gid, _uin = _galgame_ctx
            _delta = 0
            for _m in re.findall(r"\[好感\s*[:：]?\s*([+-]?\d{1,3})\]", full_reply):
                try:
                    _delta += int(_m)
                except Exception:
                    pass
            if _delta:
                full_reply = re.sub(r"\[好感\s*[:：]?\s*[+-]?\d{1,3}\]", "", full_reply).strip()
                from qq.qq_galgame import apply_change
                _new = apply_change(_gid, _uin, _delta)
                _aff_note = f"（好感度 {_delta:+d}，当前 {_new}）"
                print(f"[QQChat] 💗 Galgame 好感度 QQ{_uin}: {_delta:+d} → {_new}")
        except Exception:
            pass

    # 4c. 好感度变化提示：追加到发给对方看的回复末尾（不进记忆，避免污染自我回顾）
    _reply_show = full_reply
    if _aff_note and full_reply and full_reply.strip():
        _reply_show = full_reply.rstrip() + "\n" + _aff_note

    # 5. 保存记忆（user + assistant 追加到对应会话仓）
    #    user 消息可读化：纯图片时用图片描述替代，避免记忆里存空白/垃圾文本
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    memory_user_text = user_text
    if vision_desc and (not user_text or not user_text.strip()):
        memory_user_text = f"[图片] {vision_desc}"
    elif vision_desc and user_text.strip():
        memory_user_text = f"{user_text}（附图：{vision_desc}）"
    new_msgs = [
        {"role": "user", "content": memory_user_text, "timestamp": timestamp},
        {"role": "assistant", "content": full_reply, "timestamp": timestamp},
    ]
    try:
        _save_session_history(session_key, new_msgs)
    except Exception as e:
        print(f"[QQChat] 保存记忆失败: {e}")

    return _reply_show, sticker_names
