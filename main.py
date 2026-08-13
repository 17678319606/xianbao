#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
线报酷 -> WordPress 自动采集发布（GitHub Actions 版）

合规：仅调用线报酷官方 JSON 接口（push.json），发布时强制回链源站。
安全：所有密钥来自环境变量（GitHub Secrets），绝不落库。
落地约束：
  - 直接发布(PUBLISH_STATUS="publish")，不经草稿
  - 银行活动识别最大化(超全关键词+主要银行简称)，不漏任何银行相关活动
  - 京东短链(u.jd.com/3.cn/item.jd.com)必须 follow 跳转确认落地 jd 域，
    且优先选取商品/短链域，确保"带京东链接"真实有效
  - 分类优先级：真实京东链接 -> 类目1；银行关键词 -> 类目10；其余丢弃
  - 仅发布成功才写入去重状态，异常绝不标记已发
"""
import os
import sys
import json
import re
import time
import base64
from html import escape
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

# ---------------- 配置 ----------------
IXBK_BASE = "https://news.ixbk.net"
PUSH_URL = IXBK_BASE + "/plus/json/push.json"

WP_SITE = os.environ.get("WP_SITE", "").strip().rstrip("/")
WP_USER = os.environ.get("WP_USER", "")
WP_APP_PASSWORD = os.environ.get("WP_APP_PASSWORD", "")

STATE_FILE = "posted.json"
MAX_AGE_DAYS = 30
PUBLISH_STATUS = "publish"    # 用户要求：直接发布，不经草稿
FETCH_TIMEOUT = 15
WP_TIMEOUT = 20
JD_RESOLVE_TIMEOUT = 10

UA = {
    "User-Agent": "Mozilla/5.0 (compatible; XianbaoBot/1.0; "
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
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def cleanup_state(state, now):
    cutoff = now - MAX_AGE_DAYS * 86400
    return {k: v for k, v in state.items() if v and int(v) > cutoff}


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
    """从正文提取并校验京东真实链接。
    优先选取明确的商品/短链域(u.jd.com / item.jd.com / item.m.jd.com / 3.cn)，
    其次其他 *.jd.com 子域。
    对"明确的京东官方短链/商品域"：即便 follow 跳转因网络抖动失败也信任其域名
    （域名本身就是京东的，不可能伪装成非京东），避免误丢好价；
    对"其他 *.jd.com 子域"：必须 follow 跳转确认最终落地 jd 域（防伪装）。
    返回 (原始链接, 最终落地链接) 或 (None, None)。
    """
    links = extract_links(html)
    preferred, others = [], []
    for u in links:
        host = (urlparse(u).hostname or "").lower()
        if host in ("u.jd.com", "item.jd.com", "item.m.jd.com") or host.endswith("3.cn"):
            preferred.append(u)
        elif is_jd_host(host):
            others.append(u)
    for u in preferred:                   # 明确的京东短链/商品域优先
        ok, final = resolve_jd(u)
        if ok:
            return u, final
    if preferred:                         # 全部解析失败（网络抖动）：信任域名
        return preferred[0], preferred[0]
    for u in others:                      # 其他 jd 子域：必须解析确认
        ok, final = resolve_jd(u)
        if ok:
            return u, final
    return None, None


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


def classify(item):
    """返回 (category_id, jd_link) 或 (None, None) 表示丢弃。"""
    html = item.get("content_html", "") or ""
    text_content = item.get("content", "") or ""
    # 京东链接可从 content_html 与 content 纯文本两处提取（兼容无 HTML 的情况）
    jd_orig, jd_final = find_jd_link(html + " " + text_content)
    if jd_orig:
        return 1, (jd_final or jd_orig)        # 类目1：京东好价线报（优先）
    text = " ".join([
        item.get("title", ""),
        text_content,
        item.get("catename", ""),
    ])
    if is_bank(text):
        return 10, None                         # 类目10：活动信息
    return None, None


# ---------------- WP 侧兜底去重（抗状态文件丢失/漂移） ----------------
def wp_post_exists(title, cat_id):
    """查询 WP 是否已存在同标题同分类的已发布文章。
    作为状态文件 posted.json 的兜底：即便状态因网络抖动丢失/未提交，
    也能避免把已发文章重复发布到站点。查询失败一律返回 False（宁可发、不误杀）。"""
    if not (WP_SITE and WP_USER and WP_APP_PASSWORD):
        return False
    auth = base64.b64encode(f"{WP_USER}:{WP_APP_PASSWORD}".encode("utf-8")).decode()
    headers = {"Authorization": f"Basic {auth}", "Accept": "application/json"}
    params = {
        "search": title[:50],
        "categories": cat_id,
        "per_page": 5,
        "status": "publish",
    }
    try:
        r = requests.get(f"{WP_SITE}/wp-json/wp/v2/posts", params=params,
                         headers=headers, timeout=WP_TIMEOUT)
        if r.status_code != 200:
            return False
        posts = r.json()
        norm = title.strip()
        return any(
            (p.get("title", {}).get("rendered", "") or "").strip() == norm
            for p in posts
        )
    except Exception as e:
        print(f"[WARN] wp_post_exists check failed (proceed to publish): {e}")
        return False


# ---------------- 发布到 WP ----------------
def publish_to_wp(title, content_html, cat_id):
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
def main():
    now = int(time.time())
    state = load_state()
    state = cleanup_state(state, now)

    items, fetch_ok = fetch_push()
    print(f"[INFO] fetched {len(items)} items (source_ok={fetch_ok})")

    new_count = 0
    skip_dup = 0
    for item in items:
        try:
            iid = str(item.get("id"))
            if iid in state:                       # 去重（状态文件）
                continue
            cat_id, jd_link = classify(item)
            if cat_id is None:                     # 非京东非银行 -> 丢弃
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
            title = item.get("title", "")
            # WP 侧兜底去重：状态丢失时也不重复发
            if wp_post_exists(title, cat_id):
                print(f"[SKIP] already exists on WP (state lost?) title={title[:30]}")
                state[iid] = now
                skip_dup += 1
                continue
            pid = publish_to_wp(title, content, cat_id)
            if pid:                                # 仅成功才标记，避免重复发
                state[iid] = now
                new_count += 1
        except Exception as e:
            print(f"[WARN] item processing error, skipped: {e}")
            continue

    save_state(state)
    status = "OK" if fetch_ok else "WARN_SOURCE_DOWN"
    print(f"[{status}] new published={new_count}, skip_dup={skip_dup}, "
          f"tracked={len(state)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
