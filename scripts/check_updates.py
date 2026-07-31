#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
HR 法律法规月度检查脚本

功能：
1. 读取 data/laws.json 中列出的法规与官方 URL
2. 抓取每个 URL 的页面内容并计算哈希
3. 与上次保存的哈希对比，判断是否有更新
4. 更新 laws.json 状态、检查时间、更新时间
5. 更新 history.json 变更记录
6. 如有更新，生成邮件/微信通知摘要
7. 将变更提交并推送到 GitHub Pages 仓库
"""

import json
import hashlib
import os
import re
import subprocess
import sys
import time
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


def now_str() -> str:
    return datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S")


def date_str() -> str:
    return datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d")


def fetch_content(url: str) -> tuple[str, bool]:
    """抓取 URL 内容，返回（简化后文本，是否成功）"""
    try:
        resp = requests.get(url, headers=HEADERS, timeout=30, allow_redirects=True)
        resp.raise_for_status()
        text = resp.text
        # 去除 HTML 标签、空白、JS 变量等不稳定内容，只保留可读的文本和关键元数据
        text = re.sub(r"<script[^>]*>[\s\S]*?</script>", "", text, flags=re.IGNORECASE)
        text = re.sub(r"<style[^>]*>[\s\S]*?</style>", "", text, flags=re.IGNORECASE)
        text = re.sub(r"<[^>]+>", "", text)
        text = re.sub(r"\s+", "", text)
        return text, True
    except Exception as e:
        return str(e), False


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="ignore")).hexdigest()[:16]


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
                print("已推送到 GitHub Pages")
                return
            if result.stderr and ("rejected" in result.stderr.lower() or "non-fast-forward" in result.stderr.lower()):
                print("远程有更新，先拉取合并...")
                subprocess.run(["git", "pull", "origin", "main", "--rebase"], cwd=REPO_DIR, check=False)
            time.sleep(2)
        if result:
            print("推送失败：", result.stderr)
    except Exception as e:
        print("Git 操作失败：", e)


def send_email(subject: str, body: str, to: str = "sam.huo@te.com"):
    """通过 Himalaya 发送邮件通知"""
    try:
        result = subprocess.run(
            ["himalaya", "message", "write", "-H", f"To:{to}", "-H", f"Subject:{subject}", body],
            cwd=REPO_DIR,
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
        if result.returncode == 0:
            print(f"邮件已发送至 {to}")
        else:
            print("邮件发送失败：", result.stderr)
    except FileNotFoundError:
        print("Himalaya 未安装或配置，跳过邮件通知")
    except Exception as e:
        print("邮件发送异常：", e)


def main():
    laws_data = load_json(LAWS_FILE)
    state = load_json(STATE_FILE)
    history = load_json(HISTORY_FILE)
    if "events" not in history:
        history["events"] = []

    today = date_str()
    updated_laws = []
    error_laws = []

    for law in laws_data.get("laws", []):
        url = law.get("url")
        law_id = law.get("id")
        if not url:
            continue

        print(f"正在检查：{law['name']} ...")
        text, ok = fetch_content(url)
        law["last_checked"] = today

        if not ok:
            law["status"] = "error"
            error_laws.append((law["name"], text[:200]))
            print(f"  检查失败：{text[:100]}")
            continue

        current_hash = content_hash(text)
        previous_hash = state.get(law_id, {}).get("hash")
        state.setdefault(law_id, {})["hash"] = current_hash

        if previous_hash and current_hash != previous_hash:
            law["status"] = "updated"
            law["last_changed"] = today
            updated_laws.append(law)
            history["events"].insert(0, {
                "date": today,
                "law_id": law_id,
                "law_name": law["name"],
                "event": "页面内容发生变化，可能有法规更新或修订",
                "url": url,
            })
            print(f"  ⚠️ 检测到更新")
        else:
            if law.get("status") == "updated":
                # 如果上次标记为 updated，且本次没有新变化，则恢复 ok
                law["status"] = "ok"
            else:
                law["status"] = law.get("status") or "ok"
            print(f"  无更新")

    laws_data["last_updated"] = today
    save_json(LAWS_FILE, laws_data)
    save_json(STATE_FILE, state)
    save_json(HISTORY_FILE, history)

    # 生成通知消息
    if updated_laws:
        subject = f"【HR 法规更新提醒】{today} 检测到 {len(updated_laws)} 部法规变化"
        lines = [f"本次检查日期：{today}", "检测到以下法规页面发生变化：", ""]
        for law in updated_laws:
            lines.append(f"• {law['name']}（{law['category']}）")
            lines.append(f"  官方来源：{law['url']}")
            lines.append("")
        lines.append("详细信息请查看：")
        lines.append("https://hosamzj.github.io/hr-compliance/")
        body = "\n".join(lines)
        send_email(subject, body)
    else:
        print(f"{today} 检查完成，未检测到法规更新")
        if error_laws:
            subject = f"【HR 法规检查异常】{today} 部分网站访问失败"
            lines = [f"本次检查日期：{today}", "以下法规网站访问失败：", ""]
            for name, err in error_laws:
                lines.append(f"• {name}：{err}")
            body = "\n".join(lines)
            send_email(subject, body)

    # 更新 index.html 中的历史记录（可选：也可以由前端动态读取 history.json）
    # 这里保持 index.html 静态不变，历史记录通过 data/history.json 提供给前端

    # 提交并推送
    git_push_changes(f"chore: monthly HR law check on {today}")

    # 输出摘要给 cron 任务
    print(f"\n检查摘要：总计 {len(laws_data['laws'])} 部，更新 {len(updated_laws)} 部，异常 {len(error_laws)} 部")


if __name__ == "__main__":
    main()
