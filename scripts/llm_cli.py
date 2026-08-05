#!/usr/bin/env python3
"""统一 JSON 分析入口：优先 MiniMax，额度/失败时回退 Grok Build。"""

from __future__ import annotations

import os
import re
import shutil
import sys
from typing import Any, Dict, Optional

from grok_cli import grok_json
from minimax_cli import minimax_json

# 进程内记忆：MiniMax 一旦判定额度耗尽，后续直接走 Grok，避免反复撞墙
_MINIMAX_EXHAUSTED = False
_LAST_ENGINE = ""


def last_engine() -> str:
    return _LAST_ENGINE


def _looks_like_quota_or_rate_limit(err: BaseException) -> bool:
    text = str(err) or ""
    keys = (
        "quota",
        "rate limit",
        "用量上限",
        "Token Plan",
        "2067",
        "insufficient",
        "余额",
        "额度",
        "exceeded",
        "too many requests",
        "429",
        "billing",
    )
    low = text.lower()
    return any(k.lower() in low for k in keys)


def _minimax_available() -> bool:
    return bool(shutil.which(os.environ.get("MINIMAX_CLI", "mmx")))


def _grok_available() -> bool:
    return bool(shutil.which(os.environ.get("GROK_CLI", "grok")))


def llm_json(
    system: str,
    user: str,
    max_tokens: int = 1800,
    temperature: float = 0.3,
) -> Dict[str, Any]:
    """优先 MiniMax；额度不足/失败时回退 Grok Build。

    环境变量：
      LLM_ENGINE=minimax|grok|auto  （默认 auto：minimax→grok）
      LLM_FALLBACK=0  禁止回退
    """
    global _MINIMAX_EXHAUSTED, _LAST_ENGINE

    engine = (os.environ.get("LLM_ENGINE") or "auto").strip().lower()
    allow_fallback = os.environ.get("LLM_FALLBACK", "1").strip() not in (
        "0", "false", "no",
    )

    prefer_minimax = engine in ("auto", "minimax", "")
    prefer_grok_only = engine == "grok"

    errors: list = []

    # —— 1) MiniMax ——
    if prefer_minimax and not prefer_grok_only and not _MINIMAX_EXHAUSTED:
        if not _minimax_available():
            errors.append("MiniMax CLI 不可用")
        else:
            try:
                result = minimax_json(
                    system, user, max_tokens=max_tokens, temperature=temperature
                )
                _LAST_ENGINE = "minimax"
                return result
            except Exception as exc:
                errors.append(f"MiniMax: {exc}")
                if _looks_like_quota_or_rate_limit(exc):
                    _MINIMAX_EXHAUSTED = True
                    print(
                        "llm: MiniMax 额度/限流，本进程后续改用 Grok Build",
                        file=sys.stderr,
                    )
                elif not allow_fallback:
                    raise
                else:
                    print(f"llm: MiniMax 失败，尝试 Grok Build — {exc}", file=sys.stderr)

    # —— 2) Grok Build 回退 / 强制 ——
    if prefer_grok_only or allow_fallback or _MINIMAX_EXHAUSTED or not prefer_minimax:
        if not _grok_available():
            errors.append("Grok Build CLI 不可用")
        else:
            try:
                result = grok_json(
                    system, user, max_tokens=max_tokens, temperature=temperature
                )
                _LAST_ENGINE = "grok"
                if not prefer_grok_only:
                    print("llm: 已使用 Grok Build 回退分析", file=sys.stderr)
                return result
            except Exception as exc:
                errors.append(f"Grok: {exc}")
                raise RuntimeError(
                    "分析失败（MiniMax 与 Grok 均不可用）: " + " | ".join(errors)
                ) from exc

    raise RuntimeError("分析失败: " + " | ".join(errors or ["无可用引擎"]))
