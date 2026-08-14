#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""失败告警：企业微信机器人 webhook + 邮件(SMTP)。

两种用法：
  1) 被 main.py 直接 import： from alert import send_alert
  2) 被 monitor.sh 作为 CLI 调用： python alert.py "<标题>" "<正文>"

设计要点：
  - 告警冷却：同一 key 的告警 6 小时内只发一次，避免每 5 分钟刷屏（尤其采集源抖动时）。
  - 优雅降级：未配置 webhook 或 SMTP 时静默跳过，不阻断主流程。
  - 凭据来自同目录 .env（WXWORK_WEBHOOK / ALERT_EMAIL / SMTP_*），不落库、不打印。
"""
import os
import sys
import json
import time
import smtplib
from email.mime.text import MIMEText


def load_dotenv(path=".env"):
    """极简 .env 解析（与 main.py 同款逻辑，自包含以避免循环 import）。"""
    if not os.path.exists(path):
        return
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k, v = k.strip(), v.strip().strip('"').strip("'")
                if k and k not in os.environ:
                    os.environ[k] = v
    except Exception:
        pass


# 读取凭据（服务器上从 .env，GitHub Actions 上从 Secrets 环境变量）
load_dotenv()

WXWORK_WEBHOOK = os.environ.get("WXWORK_WEBHOOK", "").strip()
ALERT_EMAIL = os.environ.get("ALERT_EMAIL", "").strip()
SMTP_HOST = os.environ.get("SMTP_HOST", "").strip()
SMTP_PORT = int(os.environ.get("SMTP_PORT", "465"))
SMTP_USER = os.environ.get("SMTP_USER", "").strip()
SMTP_PASS = os.environ.get("SMTP_PASS", "").strip()
SMTP_TLS = os.environ.get("SMTP_TLS", "1").strip() in ("1", "true", "yes", "on")

ALERT_STATE_FILE = ".alert_state.json"
COOLDOWN = 6 * 3600  # 同类型告警 6 小时冷却


def _load_state():
    try:
        with open(ALERT_STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_state(state):
    try:
        with open(ALERT_STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f)
    except Exception:
        pass


def _in_cooldown(key):
    if not key:
        return False
    state = _load_state()
    last = state.get(key, 0)
    return (time.time() - last) < COOLDOWN


def _mark(key):
    if not key:
        return
    state = _load_state()
    state[key] = int(time.time())
    _save_state(state)


def send_webhook(subject, message):
    if not WXWORK_WEBHOOK:
        return False
    try:
        import requests
    except Exception:
        return False
    payload = {
        "msgtype": "markdown",
        "markdown": {"content": f"### ⚠️ {subject}\n{message}"},
    }
    try:
        r = requests.post(WXWORK_WEBHOOK, json=payload, timeout=10)
        return r.status_code == 200
    except Exception as e:
        print(f"[WARN] webhook alert failed: {e}")
        return False


def send_email(subject, message):
    if not (ALERT_EMAIL and SMTP_HOST and SMTP_USER and SMTP_PASS):
        return False
    msg = MIMEText(message, "plain", "utf-8")
    msg["Subject"] = f"[xianbao] {subject}"
    msg["From"] = SMTP_USER
    msg["To"] = ALERT_EMAIL
    try:
        if SMTP_TLS:
            with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=15) as s:
                s.login(SMTP_USER, SMTP_PASS)
                s.sendmail(SMTP_USER, [ALERT_EMAIL], msg.as_string())
        else:
            with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15) as s:
                s.starttls()
                s.login(SMTP_USER, SMTP_PASS)
                s.sendmail(SMTP_USER, [ALERT_EMAIL], msg.as_string())
        return True
    except Exception as e:
        print(f"[WARN] email alert failed: {e}")
        return False


def send_alert(subject, message, key=None):
    """发送告警（webhook + 邮件）。key 用于冷却去重；不带 key 则不冷却。"""
    if _in_cooldown(key):
        print(f"[INFO] alert suppressed (cooldown): {subject}")
        return False
    ok_w = send_webhook(subject, message)
    ok_e = send_email(subject, message)
    _mark(key)
    print(f"[ALERT] {subject} | webhook={ok_w} email={ok_e}")
    return ok_w or ok_e


if __name__ == "__main__":
    if len(sys.argv) >= 3:
        send_alert(sys.argv[1], sys.argv[2])
    elif len(sys.argv) == 2:
        send_alert(sys.argv[1], "")
    else:
        print("usage: python alert.py <subject> <message>")
