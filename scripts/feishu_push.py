#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""飞书主动推送。

支持两种通道（按优先级）：
  1. FEISHU_WEBHOOK          群自定义机器人 Webhook（可选签名）
  2. FEISHU_CHAT_ID          应用机器人 + lark-cli 发到指定群（CLI 配置路径）

环境变量（不要写入仓库）：
  FEISHU_WEBHOOK            群机器人 Webhook URL
  FEISHU_WEBHOOK_SECRET     可选签名密钥（机器人开启「签名校验」时必填）
  FEISHU_CHAT_ID            推送群 chat_id（oc_xxx），走 lark-cli --as bot
  FEISHU_SITE_URL           可选，卡片/正文底部链接，默认 GitHub Pages
  LARK_CLI                  可选，lark-cli 可执行文件路径

用法：
  python3 scripts/feishu_push.py --text "测试"
  python3 scripts/feishu_push.py --title "动向速递" --text "摘要..." --card
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import os
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

DEFAULT_SITE = "https://minmengxhw-cpu.github.io/minmeng-canzheng-agent/"


def _sign(secret: str, timestamp: str) -> str:
    string_to_sign = f"{timestamp}\n{secret}"
    digest = hmac.new(
        string_to_sign.encode("utf-8"), digestmod=hashlib.sha256
    ).digest()
    return base64.b64encode(digest).decode("utf-8")


def build_text_payload(text: str, *, secret: str = "") -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "msg_type": "text",
        "content": {"text": text},
    }
    if secret:
        ts = str(int(time.time()))
        payload["timestamp"] = ts
        payload["sign"] = _sign(secret, ts)
    return payload


def build_card_payload(
    title: str,
    summary: str,
    *,
    lines: Optional[list] = None,
    site_url: str = "",
    secret: str = "",
) -> Dict[str, Any]:
    """飞书交互卡片，手机通知更清晰。"""
    elements: list = [
        {
            "tag": "div",
            "text": {"tag": "lark_md", "content": summary},
        }
    ]
    if lines:
        body = "\n".join(f"• {x}" for x in lines[:8])
        elements.append(
            {
                "tag": "div",
                "text": {"tag": "lark_md", "content": body},
            }
        )
    if site_url:
        elements.append(
            {
                "tag": "action",
                "actions": [
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "打开网页"},
                        "type": "primary",
                        "url": site_url,
                    }
                ],
            }
        )
    card = {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": title[:50] or "CZ Agent"},
            "template": "blue",
        },
        "elements": elements,
    }
    payload: Dict[str, Any] = {"msg_type": "interactive", "card": card}
    if secret:
        ts = str(int(time.time()))
        payload["timestamp"] = ts
        payload["sign"] = _sign(secret, ts)
    return payload


def post_webhook(url: str, payload: Dict[str, Any], timeout: int = 15) -> Dict[str, Any]:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            code = resp.getcode()
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace")
        return {"ok": False, "http": e.code, "body": raw, "channel": "webhook"}
    except Exception as e:
        return {"ok": False, "http": 0, "body": str(e), "channel": "webhook"}
    try:
        body = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        body = {"raw": raw}
    # 飞书成功一般 StatusCode=0 或 code=0
    ok = True
    if isinstance(body, dict):
        if "StatusCode" in body:
            ok = body.get("StatusCode") == 0
        elif "code" in body:
            ok = body.get("code") == 0
    return {"ok": ok, "http": code, "body": body, "channel": "webhook"}


def _find_lark_cli() -> Optional[str]:
    override = os.environ.get("LARK_CLI", "").strip()
    if override and os.path.isfile(override) and os.access(override, os.X_OK):
        return override
    found = shutil.which("lark-cli")
    if found:
        return found
    home = os.path.expanduser("~")
    for p in (
        os.path.join(home, ".local/bin/lark-cli"),
        "/opt/homebrew/bin/lark-cli",
        "/usr/local/bin/lark-cli",
    ):
        if os.path.isfile(p) and os.access(p, os.X_OK):
            return p
    return None


def _format_markdown(
    title: str,
    summary: str,
    *,
    lines: Optional[list] = None,
    site_url: str = "",
) -> str:
    parts: List[str] = [f"**{title}**", "", summary]
    if lines:
        parts.append("")
        for x in lines[:8]:
            parts.append(f"- {x}")
    if site_url:
        parts.append("")
        parts.append(f"[打开网页]({site_url})")
    return "\n".join(parts)


def post_lark_cli_chat(
    chat_id: str,
    *,
    text: str = "",
    markdown: str = "",
    timeout: int = 60,
) -> Dict[str, Any]:
    """通过 lark-cli 应用机器人向群发消息。"""
    cli = _find_lark_cli()
    if not cli:
        return {
            "ok": False,
            "channel": "lark-cli",
            "reason": "lark-cli not found (install or set LARK_CLI)",
        }
    cmd = [
        cli,
        "im",
        "+messages-send",
        "--as",
        "bot",
        "--chat-id",
        chat_id,
        "--json",
    ]
    if markdown:
        cmd.extend(["--markdown", markdown])
    elif text:
        cmd.extend(["--text", text])
    else:
        return {"ok": False, "channel": "lark-cli", "reason": "empty message"}

    env = os.environ.copy()
    env.setdefault("LARKSUITE_CLI_NO_UPDATE_NOTIFIER", "1")
    env.setdefault("LARKSUITE_CLI_NO_SKILLS_NOTIFIER", "1")
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "channel": "lark-cli", "reason": "timeout"}
    except Exception as e:
        return {"ok": False, "channel": "lark-cli", "reason": str(e)}

    raw = (proc.stdout or "").strip() or (proc.stderr or "").strip()
    try:
        body = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        body = {"raw": raw, "stderr": (proc.stderr or "").strip()}

    ok = False
    if isinstance(body, dict):
        if body.get("ok") is True:
            ok = True
        elif body.get("code") == 0:
            ok = True
        elif proc.returncode == 0 and body.get("data"):
            ok = True
    return {
        "ok": ok,
        "channel": "lark-cli",
        "exit": proc.returncode,
        "body": body,
        "chat_id": chat_id,
    }


def configured_channels() -> Dict[str, bool]:
    return {
        "webhook": bool(os.environ.get("FEISHU_WEBHOOK", "").strip()),
        "chat_id": bool(os.environ.get("FEISHU_CHAT_ID", "").strip()),
    }


def push_text(text: str) -> Dict[str, Any]:
    url = os.environ.get("FEISHU_WEBHOOK", "").strip()
    if url:
        secret = os.environ.get("FEISHU_WEBHOOK_SECRET", "").strip()
        return post_webhook(url, build_text_payload(text, secret=secret))

    chat_id = os.environ.get("FEISHU_CHAT_ID", "").strip()
    if chat_id:
        return post_lark_cli_chat(chat_id, text=text)

    return {
        "ok": False,
        "skipped": True,
        "reason": "FEISHU_WEBHOOK and FEISHU_CHAT_ID not set",
    }


def push_brief_card(
    title: str,
    summary: str,
    *,
    item_lines: Optional[list] = None,
) -> Dict[str, Any]:
    site = os.environ.get("FEISHU_SITE_URL", DEFAULT_SITE).strip() or DEFAULT_SITE
    url = os.environ.get("FEISHU_WEBHOOK", "").strip()
    if url:
        secret = os.environ.get("FEISHU_WEBHOOK_SECRET", "").strip()
        payload = build_card_payload(
            title, summary, lines=item_lines, site_url=site, secret=secret
        )
        return post_webhook(url, payload)

    chat_id = os.environ.get("FEISHU_CHAT_ID", "").strip()
    if chat_id:
        md = _format_markdown(title, summary, lines=item_lines, site_url=site)
        r = post_lark_cli_chat(chat_id, markdown=md)
        if r.get("ok"):
            return r
        # markdown 失败时退回纯文本
        plain = f"{title}\n\n{summary}"
        if item_lines:
            plain += "\n" + "\n".join(f"• {x}" for x in item_lines[:8])
        plain += f"\n\n{site}"
        r2 = post_lark_cli_chat(chat_id, text=plain)
        if r2.get("ok"):
            r2["fallback"] = "text"
            return r2
        r["fallback_text"] = r2
        return r

    return {
        "ok": False,
        "skipped": True,
        "reason": "FEISHU_WEBHOOK and FEISHU_CHAT_ID not set",
    }


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="飞书机器人主动推送测试")
    p.add_argument("--text", default="CZ Agent 飞书推送测试（可忽略）")
    p.add_argument("--title", default="CZ Agent 测试")
    p.add_argument("--card", action="store_true", help="用卡片/Markdown 格式推送")
    args = p.parse_args(argv)
    if args.card:
        r = push_brief_card(args.title, args.text, item_lines=["这是一条测试条目"])
    else:
        r = push_text(args.text)
    print(json.dumps(r, ensure_ascii=False, indent=2))
    if r.get("skipped"):
        print(
            "未配置 FEISHU_WEBHOOK / FEISHU_CHAT_ID，跳过。",
            flush=True,
        )
        return 2
    return 0 if r.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
