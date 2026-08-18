#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
HR 法律法规月度检查脚本

功能：
1. 读取 data/laws.json 中列出的法规与监控 URL（优先使用 monitor_url，回退 url）
2. 按 URL 去重抓取页面内容并计算哈希
3. 与上次保存的哈希对比，判断是否有更新
4. 更新 laws.json 状态、检查时间、更新时间、变更摘要
5. 更新 data/state.json 和 data/history.json
6. 如有更新且 --notify 已开启，发送邮件通知到指定收件人
7. 将变更提交并推送到 GitHub Pages 仓库

用法：
    python scripts/check_updates.py [--notify] [--quiet] [--recipients a@x.com,b@x.com]
"""

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import difflib
from datetime import datetime, timezone
from pathlib import Path

import requests

REPO_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_DIR / "data"
LAWS_FILE = DATA_DIR / "laws.json"
HISTORY_FILE = DATA_DIR / "history.json"
STATE_FILE = DATA_DIR / "state.json"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}

DEFAULT_RECIPIENTS = ["sam.huo@te.com", "fiona.lu@te.com"]


def now_str() -> str:
    return datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S")


def date_str() -> str:
    return datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d")


def fetch_content(url: str) -> tuple[str, bool]:
    """抓取 URL 内容，返回（简化后文本，是否成功）"""
    try:
        resp = requests.get(url, headers=HEADERS, timeout=30, allow_redirects=True)
        resp.raise_for_status()
        # 修正编码：requests 可能将中文页面误识别为 ISO-8859-1，优先按 UTF-8 解码
        if resp.encoding and resp.encoding.lower() in ("iso-8859-1", "latin-1"):
            resp.encoding = "utf-8"
        text = resp.text
        # 去除 HTML 标签、脚本、样式、空白，保留可读文本
        text = re.sub(r"<script[^>]*>[\s\S]*?</script>", "", text, flags=re.IGNORECASE)
        text = re.sub(r"<style[^>]*>[\s\S]*?</style>", "", text, flags=re.IGNORECASE)
        text = re.sub(r"<[^>]+>", "", text)
        text = re.sub(r"\s+", "", text)
        return text, True
    except Exception as e:
        return str(e), False


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="ignore")).hexdigest()[:16]


def compute_change_summary(old_text: str, new_text: str, url: str = "") -> str:
    """基于两次抓取的文本差异生成简要更新说明。"""
    if not old_text:
        return "首次建立监控基准版本，后续将持续比对变化。"

    sm = difflib.SequenceMatcher(None, old_text, new_text)
    changed_snippets = []
    for tag, _i1, _i2, j1, j2 in sm.get_opcodes():
        if tag in ("replace", "insert", "delete"):
            snippet = new_text[j1:j2].strip()
            if len(snippet) >= 6 and not re.match(r"^[\s\W]+$", snippet):
                snippet = re.sub(r"[<>'&;]+", "", snippet)
                changed_snippets.append(snippet)

    if not changed_snippets:
        return "页面内容哈希发生变化，但未提取到可读文本差异。"

    seen = set()
    unique = []
    for s in changed_snippets:
        key = s[:24]
        if key not in seen:
            seen.add(key)
            unique.append(s)
            if len(unique) >= 3:
                break

    truncated = [s if len(s) <= 50 else s[:50] + "..." for s in unique]
    summary = "页面内容变化涉及：" + " / ".join(truncated)
    if "gov.cn/zhengce/index" in (url or ""):
        summary = "国务院政策库页面内容发生变化，可能新增或调整了政策条目。" + summary
    return summary


def load_json(path: Path) -> dict:
    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_json(path: Path, data: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def git_push_changes(message: str):
    """提交并推送变更"""
    try:
        subprocess.run(["git", "add", "-A"], cwd=REPO_DIR, check=True)
        subprocess.run(["git", "commit", "-m", message], cwd=REPO_DIR, check=False)
        result = None
        for attempt in range(3):
            result = subprocess.run(
                ["git", "push", "origin", "main"],
                cwd=REPO_DIR,
                capture_output=True,
                text=True,
                check=False,
                timeout=120,
            )
            if result.returncode == 0:
                if not QUIET:
                    print("已推送到 GitHub Pages")
                return
            if result.stderr and ("rejected" in result.stderr.lower() or "non-fast-forward" in result.stderr.lower()):
                print("远程有更新，先拉取合并...")
                subprocess.run(["git", "pull", "origin", "main", "--rebase"], cwd=REPO_DIR, check=False)
            time.sleep(2)
        if result and not QUIET:
            print("推送失败：", result.stderr)
    except Exception as e:
        if not QUIET:
            print("Git 操作失败：", e)


def send_email(subject: str, body: str, recipients: list[str]):
    """通过 Himalaya 发送邮件通知（支持多收件人）"""
    if not recipients:
        print("无收件人，跳过邮件通知")
        return

    to_args = []
    for addr in recipients:
        to_args.extend(["-t", addr])

    eml = f"""Subject: {subject}
From: hosamzj@163.com
To: {recipients[0]}
Cc: {", ".join(recipients[1:]) if len(recipients) > 1 else ""}
Content-Type: text/plain; charset=utf-8

{body}
"""
    try:
        result = subprocess.run(
            ["himalaya", "smtp", "send", "-f", "hosamzj@163.com"] + to_args,
            input=eml,
            cwd=REPO_DIR,
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
        if result.returncode == 0:
            print(f"邮件已发送至 {', '.join(recipients)}")
        else:
            print("邮件发送失败：", result.stderr)
    except FileNotFoundError:
        print("Himalaya 未安装或配置，跳过邮件通知")
    except Exception as e:
        print("邮件发送异常：", e)


# 全局安静模式标志，供 git_push 使用
QUIET = False


def main():
    global QUIET
    parser = argparse.ArgumentParser(description="HR 法律法规月度检查脚本")
    parser.add_argument("--notify", action="store_true", help="检测到更新时发送邮件通知")
    parser.add_argument(
        "--recipients",
        default=",".join(DEFAULT_RECIPIENTS),
        help="收件人邮箱，多个用逗号分隔",
    )
    parser.add_argument("--quiet", action="store_true", help="安静模式，只输出摘要")
    args = parser.parse_args()

    QUIET = args.quiet
    recipients = [r.strip() for r in args.recipients.split(",") if r.strip()]

    laws_data = load_json(LAWS_FILE)
    state = load_json(STATE_FILE)
    history = load_json(HISTORY_FILE)
    if "events" not in history:
        history["events"] = []

    today = date_str()
    updated_laws = []
    error_laws = []

    # 按 URL 去重：同一页面只抓取一次
    url_to_laws = {}
    for law in laws_data.get("laws", []):
        url = law.get("monitor_url") or law.get("url")
        if not url:
            continue
        url_to_laws.setdefault(url, []).append(law)

    url_results = {}
    for url, laws in url_to_laws.items():
        names = "、".join(l["name"] for l in laws)
        if QUIET:
            print(f"检查：{names}", end=" ")
        else:
            print(f"正在检查：{names} ...")
        text, ok = fetch_content(url)
        for law in laws:
            law["last_checked"] = today

        if not ok:
            for law in laws:
                law["status"] = "error"
                error_laws.append((law["name"], text[:200]))
            if QUIET:
                print("失败")
            else:
                print(f"  检查失败：{text[:100]}")
            continue

        current_hash = content_hash(text)
        url_results[url] = current_hash

        # 以该 URL 下任一 law_id 的上次快照作为基准
        previous_hashes = [state.get(law.get("id"), {}).get("hash") for law in laws if law.get("id")]
        has_previous = any(h is not None for h in previous_hashes)
        previous_hash = previous_hashes[0] if has_previous else None
        previous_texts = [state.get(law.get("id"), {}).get("text") for law in laws if law.get("id")]
        previous_text = previous_texts[0] if any(t is not None for t in previous_texts) else ""

        for law in laws:
            law_id = law.get("id")
            if law_id:
                state.setdefault(law_id, {})["hash"] = current_hash
                state.setdefault(law_id, {})["text"] = text

        if previous_hash and current_hash != previous_hash:
            change_summary = compute_change_summary(previous_text, text, url)
            affected_names = [law["name"] for law in laws]
            grouped_summary = change_summary
            if len(laws) > 1:
                grouped_summary = f"页面（{url}）内容发生变化，涉及 {len(laws)} 部法规：{'、'.join(affected_names)}。"
                if "gov.cn/zhengce/index" in url:
                    grouped_summary += " 该 URL 为政策索引首页，变化不代表所列法规本身修订。"

            for law in laws:
                law["status"] = "updated"
                law["last_changed"] = today
                law["change_summary"] = grouped_summary

            updated_laws.append({
                "name": "、".join(affected_names),
                "category": "多法规/共享索引页" if len(laws) > 1 else (laws[0].get("category") or ""),
                "url": url,
                "change_summary": grouped_summary,
            })
            history["events"].insert(0, {
                "date": today,
                "law_id": ",".join(law.get("id") for law in laws if law.get("id")),
                "law_name": "、".join(affected_names),
                "event": grouped_summary,
                "url": url,
                "change_summary": grouped_summary,
            })
            if QUIET:
                print("更新")
            else:
                print(f"  ⚠️ 检测到更新（影响 {len(laws)} 部法规）：{grouped_summary[:80]}...")
        else:
            for law in laws:
                if law.get("status") == "updated":
                    law["status"] = "ok"
                else:
                    law["status"] = law.get("status") or "ok"
            if QUIET:
                print("无更新")
            else:
                print(f"  无更新")

    laws_data["last_updated"] = today
    laws_data["check_schedule"] = "每月 1 日 09:00"
    save_json(LAWS_FILE, laws_data)
    save_json(STATE_FILE, state)
    save_json(HISTORY_FILE, history)

    summary_lines = [f"HR 法规月度检查完成：{today}"]
    if updated_laws:
        summary_lines.append(f"检测到 {len(updated_laws)} 个页面变化，涉及法规：")
        for law in updated_laws:
            summary_lines.append(f"• {law['name']}（{law['category']}）")
        subject = f"【HR 法规更新提醒】{today} 检测到 {len(updated_laws)} 个页面变化"
        body = "\n".join([
            f"本次检查日期：{today}",
            "检测到以下法规页面发生变化：",
            "",
        ] + [
            f"• {law['name']}（{law['category']}）\n  更新说明：{law.get('change_summary', '')}\n  官方来源：{law['url']}"
            for law in updated_laws
        ] + [
            "",
            "详细信息请查看：",
            "https://hosamzj.github.io/hr-compliance/",
        ])
        if args.notify:
            send_email(subject, body, recipients)
        else:
            summary_lines.append("检测到变化，因未开启 --notify，不发送邮件。")
    else:
        summary_lines.append("未检测到法规更新。")
        if error_laws:
            summary_lines.append(f"但有 {len(error_laws)} 个页面检查失败。")

    print("\n" + "\n".join(summary_lines))

    git_push_changes(f"hr-compliance: 月度法规检查 {today}")


if __name__ == "__main__":
    main()
