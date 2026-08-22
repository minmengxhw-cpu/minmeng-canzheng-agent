#!/usr/bin/env python3
"""统一 JSON 分析入口：仅使用 Grok 4.6。"""

from __future__ import annotations

import os
import shutil
from typing import Any, Dict

from grok_cli import grok_json

_LAST_ENGINE = ""


def last_engine() -> str:
    return _LAST_ENGINE


def _grok_available() -> bool:
    return bool(shutil.which(os.environ.get("GROK_CLI", "grok")))


def llm_json(
    system: str,
    user: str,
    max_tokens: int = 1800,
    temperature: float = 0.3,
) -> Dict[str, Any]:
    """调用 Grok 4.6；失败直接抛错，禁止静默切换模型。"""
    global _LAST_ENGINE

    if not _grok_available():
        raise RuntimeError("Grok CLI 不可用")
    result = grok_json(
        system, user, max_tokens=max_tokens, temperature=temperature
    )
    _LAST_ENGINE = "grok"
    return result
