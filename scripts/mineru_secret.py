#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""共享密钥读取模块（不入库，统一密码入口）

优先级链（由高到低）：
  1. 环境变量 MINERU_PASS
  2. 本地文件 scripts/.mineru_secret（内容为一行密码；已被 .gitignore 忽略）
  3. 都没有 → RuntimeError，报错信息含设置指引

用途：
  - mineru_batch.py / mineru_bypass.py / mineru_tutorial/*.py 统一从此处取密码
  - mail.tm 临时邮箱密码由 get_mailtm_password() 提供（默认 = 账号密码 + "!"，
    与历史行为一致），也支持 MAILTM_PASS 环境变量覆盖
"""
import os

# 与脚本同目录下的本地密钥文件（gitignored，不会入库）
SECRET_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".mineru_secret")


def get_password():
    """读取统一账号密码：MINERU_PASS 环境变量 → scripts/.mineru_secret → 明确报错"""
    p = os.getenv("MINERU_PASS")
    if p:
        return p
    try:
        with open(SECRET_FILE, encoding="utf-8") as f:
            p = f.read().strip()
        if p:
            return p
    except OSError:
        pass
    raise RuntimeError(
        "未配置账号密码。请二选一设置：\n"
        "  1) 环境变量：set MINERU_PASS=<密码>（Windows）/ export MINERU_PASS=<密码>（Linux/macOS）\n"
        "  2) 本地密钥文件：在 scripts/.mineru_secret 中写入一行密码（该文件已被 .gitignore 忽略）")


def get_mailtm_password():
    """mail.tm 临时邮箱密码：MAILTM_PASS 环境变量 → 账号密码 + '!'（保持历史行为）"""
    p = os.getenv("MAILTM_PASS")
    if p:
        return p
    return get_password() + "!"
