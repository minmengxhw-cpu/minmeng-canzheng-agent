#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""根据飞书「群邀请链接」生成可扫码入群二维码（用于主动收推送）。

原理：
  1. 管理员建飞书群，加入自定义机器人（FEISHU_WEBHOOK 指向该群）
  2. 群设置 → 分享 → 复制邀请链接（或生成邀请二维码对应的链接）
  3. 本脚本把链接做成 PNG/SVG，挂到网站上
  4. 他人扫码入群后，即可在飞书手机端收到机器人主动推送

环境变量：
  FEISHU_JOIN_URL   飞书群邀请链接（必填才生成）
  例如：https://applink.feishu.cn/client/chat/chatter/add_by_link?link_token=...

输出（可提交公开仓，邀请链本身就是分享用的）：
  assets/feishu_join_qr.svg
  assets/feishu_join_qr.png   （若本机有 pillow）
  data/feishu_join.json
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "assets"
DATA = ROOT / "data"


def _load_env_files() -> None:
    for envf in (
        ROOT / ".env",
        Path.home() / "Library/Application Support/minmeng-canzheng-agent/.env",
        Path.home() / ".config/minmeng-canzheng-agent/env",
    ):
        if not envf.is_file():
            continue
        for line in envf.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            k, v = k.strip(), v.strip().strip('"').strip("'")
            if k and k not in os.environ:
                os.environ[k] = v
        break


def generate(url: str) -> dict:
    try:
        import qrcode
        import qrcode.image.svg
    except ImportError:
        print(
            "缺少依赖：pip3 install 'qrcode>=7.4'\n"
            "（生成 PNG 可选：pip3 install pillow）",
            file=sys.stderr,
        )
        raise SystemExit(2)

    ASSETS.mkdir(parents=True, exist_ok=True)
    DATA.mkdir(parents=True, exist_ok=True)

    # SVG（无 pillow 也能出）
    factory = qrcode.image.svg.SvgPathImage
    img_svg = qrcode.make(url, image_factory=factory, box_size=10, border=2)
    svg_path = ASSETS / "feishu_join_qr.svg"
    img_svg.save(svg_path)

    png_path = ASSETS / "feishu_join_qr.png"
    png_ok = False
    try:
        img_png = qrcode.make(url, box_size=10, border=2)
        img_png.save(png_path)
        png_ok = True
    except Exception as e:
        print(f"PNG 未生成（可忽略，站点用 SVG）：{e}")

    meta = {
        "join_url": url,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "qr_svg": "assets/feishu_join_qr.svg",
        "qr_png": "assets/feishu_join_qr.png" if png_ok else "",
        "hint": "扫码加入飞书群后，将通过群机器人主动收到动向速递",
    }
    (DATA / "feishu_join.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return meta


def main() -> int:
    _load_env_files()
    url = os.environ.get("FEISHU_JOIN_URL", "").strip()
    if not url:
        # 允许命令行传入
        if len(sys.argv) > 1 and sys.argv[1].startswith("http"):
            url = sys.argv[1].strip()
        else:
            print(
                "未设置 FEISHU_JOIN_URL。\n"
                "请在 .env 中配置飞书群邀请链接，或：\n"
                "  python3 scripts/gen_feishu_join_qr.py 'https://applink.feishu.cn/...'\n",
                file=sys.stderr,
            )
            return 2
    if "feishu.cn" not in url and "larksuite.com" not in url:
        print(
            f"警告：链接不像飞书邀请（{url[:60]}…），仍将生成二维码。",
            file=sys.stderr,
        )
    meta = generate(url)
    print(json.dumps(meta, ensure_ascii=False, indent=2))
    print("已生成二维码。把 assets/ 与 data/feishu_join.json 提交并 push 后，网站即可展示。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
