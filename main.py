#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
线报酷 -> WordPress 自动采集发布（运行时无关版）

适用运行时：
  - 你的 1H1G 国内服务器（宝塔"计划任务" / crontab / systemd 每 5 分钟跑一次）
  - 也可继续在 GitHub Actions 手动触发（schedule 已默认关闭，避免双发）

合规：仅调用线报酷官方 JSON 接口（push.json），发布时强制回链源站。
安全：密钥来自环境变量（GitHub Secrets 或服务器本地 .env），绝不落库。
落地约束：
  - 直接发布(PUBLISH_STATUS="publish")，不经草稿
  - 类目10「优惠活动」覆盖：银行活动 + 通信运营商活动(移动/电信/联通/广电) + 移动支付平台活动(微信/支付宝/云闪付/抖音等满减立减) + 生活缴费优惠
  - 类目1「好价线报」严格只收京东链接(u.jd.com/jd.com/3.cn 等京东域)；淘宝/拼多多/其他电商/垃圾短链一律丢弃
  - 分类优先级：京东链接 -> 类目1；无京东链接时银行/运营商/支付平台/生活缴费关键词命中 -> 类目10；其余丢弃
  - 仅发布成功才写入去重状态，异常绝不标记已发
  - 失败告警：企业微信机器人 + 邮件（alert.py），含 6 小时冷却

服务器版新增：
  - 自动读取同目录 .env（服务器部署用，不污染环境）
  - 落盘心跳文件 last_run.json（方便监控"是否还活着"）
  - 支持 --loop（常驻循环，配 systemd）/ --once（默认，配计划任务）/ --dry-run（只验不发）
"""
import os
import sys
import json
import re
import time
import base64
import hashlib
import argparse
from html import escape
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup


def load_dotenv(path=".env"):
    """极简 .env 解析（不引入 python-dotenv 依赖）。仅填充尚未存在的变量。
    必须在读取 WP_SITE/WP_USER/WP_APP_PASSWORD 之前执行（见下方调用）。"""
    # 若传入相对路径，基于脚本所在目录解析为绝对路径（防 cron/cwd 漂移）
    if not os.path.isabs(path):
        script_dir = os.path.dirname(os.path.abspath(__file__))
        path = os.path.join(script_dir, path)
    if not os.path.exists(path):
        print(f"[WARN] .env not found at {path}")
        return
    count = 0
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
                    count += 1
        print(f"[INFO] loaded {count} vars from {path}")
    except Exception as e:
        print(f"[WARN] load .env failed: {e}")


# 服务器部署：优先从同目录 .env 注入密钥（GitHub Actions 下无 .env，走 Secrets 环境变量）
load_dotenv()

# 失败告警（可选，alert.py 缺失时优雅跳过）
try:
    from alert import send_alert
except ImportError:
    def send_alert(title, content, key=None): return False

# ---------------- 配置 ----------------
IXBK_BASE = "https://news.ixbk.net"
PUSH_URL = IXBK_BASE + "/plus/json/push.json"

WP_SITE = os.environ.get("WP_SITE", "").strip().rstrip("/")
WP_USER = os.environ.get("WP_USER", "")
WP_APP_PASSWORD = os.environ.get("WP_APP_PASSWORD", "")

# 调试：确认凭据是否加载（仅打印脱敏信息，不泄露密码）
_pwd_mask = "SET" if WP_APP_PASSWORD else "MISSING"
print(f"[INFO] config: site={WP_SITE} user={WP_USER} app_password={_pwd_mask}")

STATE_FILE = "posted.json"
HEARTBEAT_FILE = "last_run.json"
MAX_AGE_DAYS = 30
PUBLISH_STATUS = "publish"    # 用户要求：直接发布，不经草稿
FETCH_TIMEOUT = 15
WP_TIMEOUT = 20
JD_RESOLVE_TIMEOUT = 10
LOOP_INTERVAL = int(os.environ.get("XIANBAO_INTERVAL", "300"))  # 默认 5 分钟一轮

UA = {
    "User-Agent": "Mozilla/5.0 (compatible; XianbaoBot/1.1; "
    "+https://github.com/17678319606/xianbao)"
}


# 京东相关域名白名单（含短链 u.jd.com / 3.cn）
def is_jd_host(host):
    if not host:
        return False
    host = host.lower()
    return host == "jd.com" or host.endswith(".jd.com") or host.endswith("3.cn")


# 银行活动关键词（命中即归「活动信息」类目 id=10）。
# 设计原则：宁可稍宽不可漏 —— 用户要求"不要错漏任何银行相关活动信息"。
# 分为两类：
#   A. 强银行信号（几乎不会误判）：银行卡/信用卡/支付/信贷/银行权益类词
#   B. 主要银行名称（含常用简称），覆盖大行与常见股份行，避免"招行/工行"类简称漏判
BANK_KEYWORDS = [
    # A. 强信号
    "银行", "银行卡", "信用卡", "借记卡", "储蓄卡", "准贷记卡", "银联", "visa",
    "万事达", "mastercard", "运通", "amex", "ae卡", "刷卡金", "首刷", "绑卡",
    "提额", "账单", "分期", "还款", "最低还款", "新户礼", "新户", "开卡", "办卡",
    "积分", "白条", "金条", "闪电贷", "信用贷", "消费贷", "银行活动", "银行优惠",
    "银行立减", "银行满减", "刷卡", "挥卡", "闪付", "云闪付", "数字人民币", "数币",
    "公积金", "工资卡", "代发", "理财", "存款", "大额存单", "结构性存款", "活期+",
    "朝朝宝", "日日欣", "零钱", "二类户", "三类户",
    # B. 主要银行名称（全称 + 常用简称）
    "招商银行", "招行", "工商银行", "工行", "建设银行", "建行", "中国银行", "中行",
    "农业银行", "农行", "交通银行", "交行", "邮储银行", "邮政储蓄", "邮储",
    "中信银行", "中信", "光大银行", "光大", "民生银行", "民生", "平安银行", "平安",
    "兴业银行", "兴业", "浦发银行", "浦发", "华夏银行", "华夏", "广发银行", "广发",
    "北京银行", "上海银行", "江苏银行", "浙商银行", "浙商", "渤海银行", "恒丰银行",
    "网商银行", "网商", "微众银行", "微众", "新网银行", "百信银行", "众邦银行",
]

# 通信运营商活动关键词（命中即归「优惠活动」类目 id=10）。
# 覆盖：中国移动 / 中国电信 / 中国联通 / 中国广电 的话费、流量、套餐、宽带、权益类活动。
# 注意：避免裸词"移动"（易误命中"移动电源/移动硬盘"），统一用"中国移动/移动话费/移动流量"等组合。
TELECOM_KEYWORDS = [
    "中国移动", "中国电信", "中国联通", "中国广电",
    "移动话费", "移动流量", "移动权益", "移动宽带", "移动套餐", "移动号卡",
    "电信", "联通", "广电",
    "话费充值", "充话费", "充值话费", "流量充值", "运营商",
    "宽带套餐", "合约机", "办宽带", "5G套餐", "携号转网",
]

# 移动支付平台活动关键词（命中即归「优惠活动」类目 id=10）。
# 覆盖：微信支付 / 支付宝 / 云闪付(银联) / 抖音支付 / 美团支付 / 京东支付 / 翼支付 / 数字人民币
# 以及其"满减、立减、红包、立减金、消费券"等典型支付优惠活动。
# 设计：平台名 + 活动信号词组合，避免裸词"微信/红包"等过宽误判。
PAYMENT_KEYWORDS = [
    "支付宝", "支付宝红包", "支付宝立减", "支付宝优惠",
    "微信支付", "微信红包", "微信立减", "微信支付优惠", "微信支付有优惠",
    "云闪付", "银联云闪付", "银联", "银联红包", "银联优惠",
    "翼支付", "抖音支付", "抖音红包", "抖音支付优惠",
    "美团支付", "美团红包", "美团支付优惠", "京东支付", "京东支付优惠",
    "度小满", "数字人民币", "数币", "数字人民币红包",
    "立减金", "支付立减", "支付满减", "满减金", "支付红包", "付款红包",
    "消费券", "支付优惠", "扫码立减", "扫码红包",
]

# 生活缴费优惠关键词（命中即归「优惠活动」类目 id=10）。
# 覆盖：水费 / 电费 / 燃气费 / 暖气费 / 物业费 / 宽带费等缴费场景的
# 「缴费优惠、缴费立减、缴费满减、缴费红包、充值缴费」等活动。
# 设计：以"生活缴费"作为强信号，辅以具体缴费品类词 + 缴费信号词组，
# 既避免漏抓，也尽量降低裸词过宽误判（如"费"字不会单独命中）。
UTILITY_KEYWORDS = [
    "生活缴费", "水电缴费", "缴费优惠", "缴费立减", "缴费满减", "缴费红包",
    "充值缴费", "代缴", "代缴费", "缴费活动",
    "水费", "电费", "燃气费", "煤气费", "暖气费", "供暖费", "暖费",
    "物业费", "水电燃气", "水电煤", "燃气",
]


# ---------------- 状态（去重 + 30天清理） ----------------
def load_state():
    if not os.path.exists(STATE_FILE):
        return {}
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_state(state):
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    os.replace(tmp, STATE_FILE)   # 原子写入，避免半截文件


def cleanup_state(state, now):
    cutoff = now - MAX_AGE_DAYS * 86400
    return {k: v for k, v in state.items() if v and int(v) > cutoff}


def write_heartbeat(result):
    """落盘心跳：监控脚本/你都能一眼看出"上次成功跑是什么时候"。"""
    try:
        payload = {"ts": int(time.time()), **result}
        with open(HEARTBEAT_FILE, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[WARN] write heartbeat failed: {e}")


# ---------------- 采集（合规：仅官方 JSON） ----------------
def fetch_push():
    """拉取线报酷官方 push.json。
    抗断流/抖动：最多 3 次指数退避重试。返回 (items, ok)：
      ok=True  表示本次确实成功拿到数据（即便列表为空也是源站正常返回）；
      ok=False 表示连续 3 次都失败（源站/网络中断），调用方据此告警。
    """
    last_err = None
    for attempt in range(3):
        try:
            r = requests.get(PUSH_URL, timeout=FETCH_TIMEOUT, headers=UA)
            r.raise_for_status()
            data = r.json()
            items = data if isinstance(data, list) else []
            return items, True
        except Exception as e:
            last_err = str(e)
            print(f"[WARN] fetch push.json attempt {attempt + 1}/3 failed: {e}")
            if attempt < 2:
                time.sleep(3 * (attempt + 1))     # 3s, 6s 退避
    print(f"[ERROR] fetch push.json failed after 3 attempts: {last_err}")
    return [], False


# ---------------- 京东链接校验（follow 跳转） ----------------
def extract_links(html):
    """仅从超链接(a href)与可见文本提取 URL，先 decompose 掉 <img>/<video> 等
    媒体与脚本标签，避免把京东配图域名(img.jd.com)误判为京东商品链接。"""
    soup = BeautifulSoup(html or "", "html.parser")
    for tag in soup(["img", "video", "iframe", "script", "style",
                     "source", "embed", "object", "noscript", "figure"]):
        tag.decompose()
    links = [a.get("href") for a in soup.find_all("a", href=True)]
    text = soup.get_text()
    links += re.findall(r"https?://[^\s<>\"]+", text)
    return [u for u in links if u and u.startswith("http")]


def resolve_jd(url):
    """返回 (is_jd, final_url)。仅当最终落地 host 属于 jd 域才认作京东真链。
    内部带 1 次重试，缓解京东短链瞬时网络抖动导致的误判。"""
    last_err = None
    for attempt in range(2):
        try:
            r = requests.get(url, allow_redirects=True, timeout=JD_RESOLVE_TIMEOUT,
                             headers=UA, stream=True)
            final = r.url
            r.close()
            host = urlparse(final).hostname or ""
            return is_jd_host(host), final
        except Exception as e:
            last_err = str(e)
            if attempt == 0:
                time.sleep(1)
    return False, url


def find_jd_link(html):
    """从正文提取首个京东链接（u.jd.com / jd.com / 3.cn / item.jd.com 等京东域）。
    仅京东，不要淘宝/拼多多/其他电商或垃圾短链。
    u.jd.com 本身就是 jd.com 子域，host 判断即可，无需额外网络跳转。
    返回 (jd_url, jd_url) 或 (None, None)。"""
    if not html:
        return None, None
    for u in extract_links(html):
        host = (urlparse(u).hostname or "").lower()
        if is_jd_host(host):
            return u, u
    return None, None


def fetch_detail_link(item):
    """兜底：正文无京东链接时，抓取源站详情页(IXBK_BASE + item.url)提取首个京东链接。
    仅京东，绝不把淘宝/拼多多/垃圾短链当作好价链。
    失败或无京东链接一律返回 None（宁可丢弃，绝不误发）。"""
    url = item.get("url", "") or ""
    if not url:
        return None
    full = url if url.startswith("http") else (IXBK_BASE + url)
    try:
        r = requests.get(full, timeout=FETCH_TIMEOUT, headers=UA)
        r.raise_for_status()
        for u in extract_links(r.text):
            host = (urlparse(u).hostname or "").lower()
            if is_jd_host(host):
                return u
    except Exception as e:
        print(f"[WARN] fetch detail page failed ({url}): {e}")
    return None


# ---------------- 过滤：剥离媒体 ----------------
def strip_media(html):
    soup = BeautifulSoup(html or "", "html.parser")
    for tag in soup(["img", "video", "iframe", "script", "style",
                     "figure", "noscript", "source", "embed", "object"]):
        tag.decompose()
    for tag in soup.find_all(True):
        for attr in ("style", "class", "onerror", "width", "height", "align"):
            tag.attrs.pop(attr, None)
    return str(soup).strip()


# ---------------- 分类判定（优先级） ----------------
def is_bank(text):
    return any(kw in text for kw in BANK_KEYWORDS)


def is_telecom(text):
    return any(kw in text for kw in TELECOM_KEYWORDS)


def is_payment(text):
    return any(kw in text for kw in PAYMENT_KEYWORDS)


def is_utility(text):
    return any(kw in text for kw in UTILITY_KEYWORDS)


def classify(item):
    """返回 (category_id, jd_link) 或 (None, None) 表示丢弃。
    分类优先级（用户要求：类目1 只要京东；类目10 收银行/运营商/支付/缴费活动）：
      1) 正文含京东链接(u.jd.com/jd.com/3.cn 等) -> 类目1（好价线报，仅京东）
         （正文无京东链接时，自动探源站详情页补一个京东链接）
      2) 无京东链接时，银行/运营商/支付平台/生活缴费关键词命中 -> 类目10（优惠活动）
      3) 其余（淘宝/拼多多/无链接非活动/垃圾短链）-> 丢弃
    """
    html = item.get("content_html", "") or ""
    # 1) 京东链接优先（仅京东，不要淘宝/拼多多等其他平台）
    jd_orig, _ = find_jd_link(html)
    if not jd_orig:
        # 兜底：正文没京东链接，探源站详情页补一个京东链接
        jd_orig = fetch_detail_link(item)
    if jd_orig:
        return 1, jd_orig                     # 类目1：好价线报（仅京东）
    # 2) 无京东链接 -> 走银行/运营商/支付平台/生活缴费关键词
    text = " ".join([
        item.get("title", ""),
        item.get("content", "") or "",
        item.get("catename", ""),
    ])
    if is_bank(text) or is_telecom(text) or is_payment(text) or is_utility(text):
        return 10, None                        # 类目10：优惠活动（银行+运营商+支付平台+生活缴费）
    return None, None


# ---------------- WP 侧兜底去重（抗状态文件丢失/漂移/混合触发双发） ----------------

def wp_post_exists(iid, title, cat_id):
    """WP 侧兜底去重，作为 posted.json 的二次保险。

    重要：本站的 WP REST API 对 posts 集合的 meta_key/meta_value 过滤**不生效**
    （传入不存在的 meta_value 仍返回最新文章），若用它判断会误判“已存在”而把
    所有条目全部跳过、导致永远发不出去。因此这里**只做「同标题 + 同分类」精确匹配**
    （覆盖升级前已发布的旧文章 / 状态文件丢失场景），绝不用 meta 查询。
    查询失败一律返回 False（宁可发、不误杀）。"""
    if not (WP_SITE and WP_USER and WP_APP_PASSWORD):
        return False
    auth = base64.b64encode(f"{WP_USER}:{WP_APP_PASSWORD}".encode("utf-8")).decode()
    headers = {"Authorization": f"Basic {auth}", "Accept": "application/json"}
    # 同标题 + 同分类 精确匹配（WP 的 search/categories 过滤可靠，不会误返全量）
    try:
        r = requests.get(f"{WP_SITE}/wp-json/wp/v2/posts",
                         params={"search": title[:50], "categories": cat_id,
                                 "per_page": 5, "status": "publish"},
                         headers=headers, timeout=WP_TIMEOUT)
        if r.status_code != 200:
            return False
        norm = title.strip()
        return any(
            (p.get("title", {}).get("rendered", "") or "").strip() == norm
            for p in r.json()
        )
    except Exception as e:
        print(f"[WARN] wp title dedup check failed (proceed to publish): {e}")
        return False


# ---------------- 发布到 WP ----------------
def publish_to_wp(title, content_html, cat_id, iid):
    if not (WP_SITE and WP_USER and WP_APP_PASSWORD):
        print("[ERROR] WP credentials missing, skip publish")
        return None
    body = {
        "title": title,
        "content": content_html,
        "categories": [cat_id],
        "status": PUBLISH_STATUS,
    }
    auth = base64.b64encode(
        f"{WP_USER}:{WP_APP_PASSWORD}".encode("utf-8")
    ).decode()
    headers = {
        "Authorization": f"Basic {auth}",
        "Content-Type": "application/json",
    }
    url = f"{WP_SITE}/wp-json/wp/v2/posts"
    last_err = None
    for attempt in range(3):                   # 指数退避重试 <=3
        try:
            r = requests.post(url, json=body, headers=headers, timeout=WP_TIMEOUT)
            if r.status_code in (200, 201):
                pid = r.json().get("id")
                print(f"[OK] published wp id={pid} cat={cat_id} title={title[:30]}")
                return pid
            last_err = f"status={r.status_code} {r.text[:200]}"
        except Exception as e:
            last_err = str(e)
        time.sleep(2 ** attempt)
    print(f"[ERROR] publish failed: {last_err}")
    return None


# ---------------- 主流程 ----------------
def run_once(dry_run=False):
    now = int(time.time())
    state = load_state()
    state = cleanup_state(state, now)

    items, fetch_ok = fetch_push()
    print(f"[INFO] fetched {len(items)} items (source_ok={fetch_ok})")

    new_count = 0
    skip_dup = 0
    publish_failed = 0
    for item in items:
        try:
            title = (item.get("title", "") or "").strip()
            # 稳定的去重键：优先用源站 id；缺失时退回标题哈希，避免不同文章共享 "None"
            iid_raw = item.get("id")
            iid = ("h" + hashlib.md5(title.encode("utf-8")).hexdigest()[:16]) \
                if iid_raw is None else str(iid_raw)
            if iid in state:                       # 去重（状态文件）
                continue
            cat_id, _ = classify(item)
            if cat_id is None:                     # 非京东/非活动 -> 丢弃
                continue
            content_html = item.get("content_html", "") or ""
            clean = strip_media(content_html)
            if not clean.strip():                  # content_html 为空时降级用纯文本
                raw_text = (item.get("content", "") or "").strip()
                if raw_text:
                    clean = "<p>" + escape(raw_text) + "</p>"
            if not clean.strip():                  # 纯图片且无文本 -> 跳过
                continue
            # 回链合规，固化进模板
            src_url = IXBK_BASE + (item.get("url") or "")
            source_html = (
                f'<p>来源：线报酷 ｜ '
                f'<a href="{src_url}" target="_blank" rel="nofollow">查看原文</a></p>'
            )
            content = clean + source_html
            # WP 侧兜底去重：状态丢失/混合触发时也不重复发
            if wp_post_exists(iid, title, cat_id):
                print(f"[SKIP] already exists on WP (state lost?) title={title[:30]}")
                state[iid] = now
                skip_dup += 1
                continue
            if dry_run:
                print(f"[DRY] would publish cat={cat_id} title={title[:30]}")
                continue                            # dry-run 不真正发、不改状态
            pid = publish_to_wp(title, content, cat_id, iid)
            if pid:                                # 仅成功才标记，避免重复发
                state[iid] = now
                new_count += 1
            else:
                publish_failed += 1
        except Exception as e:
            print(f"[WARN] item processing error, skipped: {e}")
            continue

    # 失败告警（含 6 小时冷却，避免每 5 分钟刷屏）
    if not fetch_ok:
        send_alert(
            "线报采集源故障",
            f"push.json 连续 3 次拉取失败（{time.strftime('%Y-%m-%d %H:%M')}）。"
            f"可能是线报酷源站抖动或服务器出网异常，请关注。",
            key="source_down",
        )
    if publish_failed > 0:
        send_alert(
            "WP 文章发布失败",
            f"本轮有 {publish_failed} 篇未能发布成功（可能 WP 应用密码失效或接口异常），"
            f"请检查 WP 凭据与站点状态。",
            key="publish_fail",
        )

    if not dry_run:
        save_state(state)
    status = "OK" if fetch_ok else "WARN_SOURCE_DOWN"
    print(f"[{status}] new published={new_count}, skip_dup={skip_dup}, "
          f"tracked={len(state)}")
    return {
        "status": status,
        "new": new_count,
        "skip": skip_dup,
        "source_ok": fetch_ok,
        "tracked": len(state),
    }


def main():
    parser = argparse.ArgumentParser(description="线报酷 -> WP 自动采集发布")
    parser.add_argument("--loop", action="store_true",
                        help="常驻循环模式（配合 systemd，每 XIANBAO_INTERVAL 秒一轮）")
    parser.add_argument("--once", action="store_true", help="单次运行后退出（默认，配合计划任务/cron）")
    parser.add_argument("--dry-run", action="store_true",
                        help="只校验采集/分类/连通性，不真正发布、不改状态")
    args = parser.parse_args()

    if args.loop:
        print(f"[INFO] loop mode, interval={LOOP_INTERVAL}s")
        while True:
            try:
                result = run_once(dry_run=args.dry_run)
            except Exception as e:
                result = {"status": "ERROR", "new": 0, "skip": 0,
                          "source_ok": False, "tracked": 0}
                print(f"[ERROR] iteration crashed: {e}")
                send_alert("采集进程异常崩溃",
                           f"--loop 常驻模式发生未捕获异常：{e}", key="crash")
            write_heartbeat(result)
            time.sleep(LOOP_INTERVAL)
    else:
        result = run_once(dry_run=args.dry_run)
        write_heartbeat(result)
        return 0


if __name__ == "__main__":
    sys.exit(main())
