"""Small JSON adapter around the installed Grok CLI."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from typing import Any, Dict


def _content(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        for key in ("text", "content", "output", "message"):
            if key in value:
                return _content(value[key])
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, list):
        return "\n".join(_content(item) for item in value)
    return str(value)


def _parse_json(raw: str) -> Dict[str, Any]:
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        value = None
    text = _content(value if value is not None else raw).strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.I | re.S).strip()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            raise RuntimeError("Grok 未返回有效 JSON")
        parsed = json.loads(text[start:end + 1])
    if not isinstance(parsed, dict):
        raise RuntimeError("Grok 返回的不是 JSON 对象")
    return parsed


def grok_json(system: str, user: str, max_tokens: int = 1800, temperature: float = 0.3) -> Dict[str, Any]:
    """Call Grok CLI in headless mode and parse its JSON response."""
    del max_tokens, temperature  # Grok CLI controls these through the selected model.
    executable = shutil.which(os.environ.get("GROK_CLI", "grok"))
    if not executable:
        raise RuntimeError("找不到 Grok CLI：请确认 grok 已安装并登录")
    prompt = (
        "SYSTEM INSTRUCTIONS:\n" + system +
        "\n\nUSER TASK:\n" + user +
        "\n\n只输出严格 JSON 对象，不要 Markdown、解释或代码围栏。"
    )
    cmd = [
        executable, "-p", prompt, "--output-format", "json",
        "--no-alt-screen", "--no-subagents", "--max-turns", "1",
        "--disable-web-search",
    ]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, check=True,
            timeout=int(os.environ.get("GROK_TIMEOUT", "180")),
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("Grok CLI 调用超时") from exc
    except subprocess.CalledProcessError as exc:
        message = (exc.stderr or exc.stdout or "Grok CLI 调用失败").strip()
        raise RuntimeError(message[-1000:]) from exc
    return _parse_json(result.stdout)
