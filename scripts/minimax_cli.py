#!/usr/bin/env python3
"""Small JSON adapter around the installed MiniMax CLI (mmx)."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from typing import Any


def _content(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        for key in ("content", "text", "output"):
            if key in value:
                return _content(value[key])
        if "message" in value:
            return _content(value["message"])
        if "choices" in value and value["choices"]:
            return _content(value["choices"][0])
    if isinstance(value, list) and value:
        return _content(value[0])
    return ""


def _parse_json(text: str) -> dict:
    text = text.strip()
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
        if match:
            text = match.group(1)
        else:
            start, end = text.find("{"), text.rfind("}")
            if start < 0 or end <= start:
                raise ValueError(f"MiniMax 未返回 JSON：{text[:300]}")
            text = text[start : end + 1]
        value = json.loads(text)
    if not isinstance(value, dict):
        raise ValueError("MiniMax 返回的 JSON 不是对象")
    return value


def minimax_json(system: str, user: str, max_tokens: int = 1800, temperature: float = 0.3) -> dict:
    """Call mmx non-interactively and parse its JSON response."""
    executable = shutil.which(os.environ.get("MINIMAX_CLI", "mmx"))
    if not executable:
        raise RuntimeError("找不到 MiniMax CLI：请确认 mmx 已安装")
    cmd = [
        executable, "--non-interactive", "--quiet", "--output", "json",
        "text", "chat", "--system", system, "--message", user,
        "--max-tokens", str(max_tokens), "--temperature", str(temperature),
        "--model", os.environ.get("MINIMAX_MODEL", "MiniMax-M3.0"),
    ]
    try:
        result = subprocess.run(
            cmd, check=True, capture_output=True, text=True,
            timeout=int(os.environ.get("MINIMAX_TIMEOUT", "300")),
        )
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "").strip()
        raise RuntimeError(f"MiniMax CLI 调用失败：{detail[:500]}") from exc
    raw = result.stdout.strip()
    try:
        wrapper = json.loads(raw)
        raw = _content(wrapper) or raw
    except json.JSONDecodeError:
        pass
    return _parse_json(raw)
