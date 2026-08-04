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


def compute_change_summary(old_text: str, new_text: str, url: str = "") -> str:
    """基于两次抓取的文本差异生成简要更新说明。"""
    if not old_text:
        return "首次建立基准版本，后续将持续比对变化。"

    # 使用 difflib 找出新增或修改的文本片段
    sm = difflib.SequenceMatcher(None, old_text, new_text)
    changed_snippets = []
    for tag, _i1, _i2, j1, j2 in sm.get_opcodes():
        if tag in ("replace", "insert", "delete"):
            snippet = new_text[j1:j2].strip()
            # 过滤过短或纯标点的片段
            if len(snippet) >= 6 and not re.match(r"^[\s\W]+$", snippet):
                # 去除可能的 HTML 实体残留和过长片段
                snippet = re.sub(r"[<>'&;]+", "", snippet)
                changed_snippets.append(snippet)

    if not changed_snippets:
        return "页面内容哈希发生变化，但未提取到可读文本差异。"

    # 去重并取前几条
    seen = set()
    unique = []
    for s in changed_snippets:
        key = s[:24]
        if key not in seen:
            seen.add(key)
            unique.append(s)
            if len(unique) >= 3:
                break

    truncated = []
    for s in unique:
        if len(s) > 50:
            s = s[:50] + "..."
        truncated.append(s)

    summary = "页面内容变化涉及：" + " / ".join(truncated)
    # 对共享的政策索引页面做更友好的说明
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
        eml = f"""Subject: {subject}
From: hosamzj@163.com
To: {to}
Content-Type: text/plain; charset=utf-8

{body}
"""
        result = subprocess.run(
            ["himalaya", "smtp", "send", "-f", "hosamzj@163.com", "-t", to],
            input=eml,
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

    # 按 URL 去重，避免相同 URL 被重复抓取和重复报警
    url_to_laws = {}
    for law in laws_data.get("laws", []):
        url = law.get("url")
        if not url:
            continue
        url_to_laws.setdefault(url, []).append(law)

    url_results = {}
    for url, laws in url_to_laws.items():
        names = "、".join(l["name"] for l in laws)
        print(f"正在检查：{names} ...")
        text, ok = fetch_content(url)
        for law in laws:
            law["last_checked"] = today

        if not ok:
            for law in laws:
                law["status"] = "error"
                error_laws.append((law["name"], text[:200]))
            print(f"  检查失败：{text[:100]}")
            continue

        current_hash = content_hash(text)
        url_results[url] = current_hash

        # 只要有一个 law 之前检查过这个 URL，就以该 URL 的上次 hash / 文本快照为准
        previous_hashes = [state.get(law.get("id"), {}).get("hash") for law in laws if law.get("id")]
        previous_hash = previous_hashes[0] if previous_hashes else None
        previous_texts = [state.get(law.get("id"), {}).get("text") for law in laws if law.get("id")]
        previous_text = previous_texts[0] if previous_texts else ""

        # 保存当前文本快照（按 law_id，便于后续按法规维度比对差异）
        for law in laws:
            law_id = law.get("id")
            if law_id:
                state.setdefault(law_id, {})["hash"] = current_hash
                state.setdefault(law_id, {})["text"] = text

        if previous_hash and current_hash != previous_hash:
            change_summary = compute_change_summary(previous_text, text, url)
            # 如果多个法规共享同一 URL，合并为一条变更事件，避免索引页微调导致重复刷屏
            affected_names = [law["name"] for law in laws]
            grouped_summary = change_summary
            if len(laws) > 1:
                grouped_summary = f"页面（{url}）内容发生变化，涉及 {len(laws)} 部法规：{'、'.join(affected_names)}。"
                # 对 gov.cn/zhengce/index 这类共享索引页给出更明确的说明
                if "gov.cn/zhengce/index" in url:
                    grouped_summary += " 该 URL 为政策索引首页，变化不代表所列法规本身修订。"

            for law in laws:
                law["status"] = "updated"
                law["last_changed"] = today
                law["change_summary"] = grouped_summary

            updated_laws.append({
                "name": "、".join(affected_names),
                "category": "多法规/共享索引页" if len(laws) > 1 else laws[0]["category"],
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
            print(f"  ⚠️ 检测到更新（影响 {len(laws)} 部法规）：{grouped_summary[:80]}...")
        else:
            for law in laws:
                if law.get("status") == "updated":
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
            summary = law.get("change_summary", "").strip()
            if summary:
                lines.append(f"  更新说明：{summary}")
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

    # 输出简短摘要给 cron 任务，避免微信长消息被限流
    report_url = "https://hosamzj.github.io/hr-compliance/"
    if updated_laws:
        names_preview = "、".join(u["name"] for u in updated_laws)
        if len(names_preview) > 80:
            names_preview = names_preview[:80] + "..."
        print(f"\n本次检测到 {len(updated_laws)} 处法规页面变化：{names_preview}")
        print(f"详细内容已发邮件，知识库：{report_url}")
    elif error_laws:
        print(f"\n本次检查完成，{len(error_laws)} 个网站访问异常，请查看邮件或日志。")
    else:
        print(f"\n{today} HR 法规检查完成，未检测到更新。")


if __name__ == "__main__":
    main()
