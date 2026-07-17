#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""飞书自定义机器人主动推送（Webhook）。

环境变量（不要写入仓库）：
  FEISHU_WEBHOOK          群机器人 Webhook URL（必填才推送）
  FEISHU_WEBHOOK_SECRET   可选签名密钥（机器人开启「签名校验」时必填）
  FEISHU_SITE_URL         可选，卡片底部链接，默认 GitHub Pages

用法：
  python3 scripts/feishu_push.py --text "测试"
  python3 scripts/feishu_push.py --title "动向速递" --text "摘要..." --site
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import os
import time
import urllib.error
import urllib.request
from typing import Any, Dict, Optional

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
        return {"ok": False, "http": e.code, "body": raw}
    except Exception as e:
        return {"ok": False, "http": 0, "body": str(e)}
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
    return {"ok": ok, "http": code, "body": body}


def push_text(text: str) -> Dict[str, Any]:
    url = os.environ.get("FEISHU_WEBHOOK", "").strip()
    if not url:
        return {"ok": False, "skipped": True, "reason": "FEISHU_WEBHOOK not set"}
    secret = os.environ.get("FEISHU_WEBHOOK_SECRET", "").strip()
    return post_webhook(url, build_text_payload(text, secret=secret))


def push_brief_card(
    title: str,
    summary: str,
    *,
    item_lines: Optional[list] = None,
) -> Dict[str, Any]:
    url = os.environ.get("FEISHU_WEBHOOK", "").strip()
    if not url:
        return {"ok": False, "skipped": True, "reason": "FEISHU_WEBHOOK not set"}
    secret = os.environ.get("FEISHU_WEBHOOK_SECRET", "").strip()
    site = os.environ.get("FEISHU_SITE_URL", DEFAULT_SITE).strip() or DEFAULT_SITE
    payload = build_card_payload(
        title, summary, lines=item_lines, site_url=site, secret=secret
    )
    return post_webhook(url, payload)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="飞书机器人主动推送测试")
    p.add_argument("--text", default="CZ Agent 飞书推送测试（可忽略）")
    p.add_argument("--title", default="CZ Agent 测试")
    p.add_argument("--card", action="store_true", help="用卡片格式推送")
    args = p.parse_args(argv)
    if args.card:
        r = push_brief_card(args.title, args.text, item_lines=["这是一条测试条目"])
    else:
        r = push_text(args.text)
    print(json.dumps(r, ensure_ascii=False, indent=2))
    if r.get("skipped"):
        print("未配置 FEISHU_WEBHOOK，跳过。", flush=True)
        return 2
    return 0 if r.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
