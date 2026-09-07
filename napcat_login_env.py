# -*- coding: utf-8 -*-
"""NapCat 自动登录辅助：从根目录 config.json 读取设置，
输出 set-able 环境变量行（NAPCAT_QUICK_ACCOUNT / _PASSWORD_MD5），
供 start_napcat.bat 用 for /f 导入当前控制台。"""
import os
import hashlib
import json
import sys

BASE = os.path.dirname(os.path.abspath(__file__))  # 项目根目录


def main():
    cfg_path = os.path.join(BASE, "config.json")
    if not os.path.isfile(cfg_path):
        return
    try:
        with open(cfg_path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
    except Exception:
        return
    if str(cfg.get("qq_auto_login_enable", "false")).lower() != "true":
        return
    uin = str(cfg.get("qq_login_uin", "") or "").strip()
    pwd = str(cfg.get("qq_login_password", "") or "")
    if not uin:
        return
    print("NAPCAT_QUICK_ACCOUNT=%s" % uin)
    if pwd:
        md5 = hashlib.md5(pwd.encode("utf-8")).hexdigest()
        print("NAPCAT_QUICK_PASSWORD_MD5=%s" % md5)


if __name__ == "__main__":
    main()
