"""
统一异步LLM客户端 — httpx async，流式 tool_use，熔断器。

核心能力：
  - call_llm_stream: 流式调用（SSE token推送）
  - call_llm: 非流式调用
  - call_llm_json: JSON提取（3级fallback）
  - circuit breaker: 滑动窗口熔断
  - think-tag stripping: 兼容deepseek/qwen reasoning模型
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, AsyncGenerator, Dict, List, Optional

import httpx

from src.config import (
    ANTHROPIC_API_KEY,
    ANTHROPIC_MODEL,
    LLM_API_KEY,
    LLM_API_URL,
    LLM_MODEL,
    LLM_PROVIDER,
    LLM_TIMEOUT_SECONDS,
    LLM_FALLBACK_MODEL,
    LLM_FALLBACK_URL,
    LLM_FALLBACK_KEY,
    LLM_FALLBACK2_MODEL,
    LLM_FALLBACK2_URL,
    LLM_FALLBACK2_KEY,
    LLM_FALLBACK3_MODEL,
    LLM_FALLBACK3_URL,
    LLM_FALLBACK3_KEY,
)

logger = logging.getLogger(__name__)

_THINKING_OVERHEAD = 256
_MIN_MAX_TOKENS = 512
_DEFAULT_MAX_TOKENS = 2000
_MAX_RETRIES = 2
_RETRY_DELAYS = [1.0, 2.0, 4.0]
_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)


def _effective_max_tokens(requested: int) -> int:
    req = max(64, int(requested or _DEFAULT_MAX_TOKENS))

    # 小请求不再强制抬到超大 token 预算，降低首包和整体时延。
    if req <= 500:
        overhead = 96
        floor = 256
    elif req <= 1200:
        overhead = 160
        floor = 512
    else:
        overhead = _THINKING_OVERHEAD
        floor = _MIN_MAX_TOKENS

    return max(req + overhead, floor)


def _strip_think_tags(text: str) -> str:
    return _THINK_RE.sub("", text).strip()


# ═══════════════════════════════════════════════════════════════════════════
# Circuit Breaker
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class CircuitBreaker:
    fail_threshold: int = 20
    window_seconds: float = 120.0
    open_seconds: float = 6.0
    _failures: list[float] = field(default_factory=list, repr=False)
    _open_until: float = field(default=0.0, repr=False)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)

    @property
    def is_open(self) -> bool:
        return time.monotonic() < self._open_until

    async def record_failure(self) -> None:
        now = time.monotonic()
        async with self._lock:
            self._failures.append(now)
            cutoff = now - self.window_seconds
            self._failures = [t for t in self._failures if t >= cutoff]
            if len(self._failures) >= self.fail_threshold:
                self._open_until = now + self.open_seconds
                self._failures.clear()

    async def record_success(self) -> None:
        async with self._lock:
            self._failures.clear()

    def reset(self) -> None:
        self._failures.clear()
        self._open_until = 0.0


_breaker = CircuitBreaker()
_fallback_breaker = CircuitBreaker()   # fallback 1 独立熔断器
_fallback2_breaker = CircuitBreaker()  # fallback 2 独立熔断器
_fallback3_breaker = CircuitBreaker()  # fallback 3 独立熔断器


def _get_fallback_chain() -> list[dict]:
    """
    返回有效的 fallback 配置列表（已过滤空配置），每项为:
    {"model": ..., "url": ..., "key": ..., "breaker": ...}
    """
    candidates = [
        (LLM_FALLBACK_MODEL,  LLM_FALLBACK_URL,  LLM_FALLBACK_KEY,  _fallback_breaker),
        (LLM_FALLBACK2_MODEL, LLM_FALLBACK2_URL, LLM_FALLBACK2_KEY, _fallback2_breaker),
        (LLM_FALLBACK3_MODEL, LLM_FALLBACK3_URL, LLM_FALLBACK3_KEY, _fallback3_breaker),
    ]
    return [
        {"model": m, "url": u or LLM_API_URL, "key": k or LLM_API_KEY, "breaker": b}
        for m, u, k, b in candidates if m
    ]

# ═══════════════════════════════════════════════════════════════════════════
# 调用统计
# ═══════════════════════════════════════════════════════════════════════════

_call_stats: Dict[str, int] = {
    "total_calls": 0, "success": 0, "retried": 0,
    "circuit_blocked": 0, "timeout": 0, "server_error": 0,
    "client_error": 0, "parse_error": 0,
}


def get_llm_stats() -> Dict[str, int]:
    return dict(_call_stats)


def _stats_inc(key: str) -> None:
    _call_stats[key] = _call_stats.get(key, 0) + 1


# ═══════════════════════════════════════════════════════════════════════════
# Provider helpers
# ═══════════════════════════════════════════════════════════════════════════

def _resolve_provider(model: str | None) -> str:
    if model and model.startswith("claude"):
        return "anthropic"
    return LLM_PROVIDER


def _resolve_model(model: str | None, provider: str) -> str:
    if model:
        return model
    if provider == "anthropic":
        return ANTHROPIC_MODEL
    # 动态读取，支持运行时通过 /api/llm/model 切换
    import src.config as _cfg
    return _cfg.LLM_MODEL


def _resolve_url(provider: str, fallback: bool = False) -> str:
    if provider == "anthropic":
        return "https://api.anthropic.com/v1/messages"
    if fallback and LLM_FALLBACK_URL:
        return LLM_FALLBACK_URL
    return LLM_API_URL


def _resolve_headers(provider: str, fallback: bool = False) -> Dict[str, str]:
    if provider == "anthropic":
        return {
            "Content-Type": "application/json",
            "x-api-key": ANTHROPIC_API_KEY,
            "anthropic-version": "2023-06-01",
        }
    key = (LLM_FALLBACK_KEY or LLM_API_KEY) if fallback else LLM_API_KEY
    return {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {key}",
    }


def _has_fallback() -> bool:
    """是否配置了至少一个 fallback 模型。"""
    return bool(LLM_FALLBACK_MODEL)


def _is_retryable(status: int) -> bool:
    return status in (429, 502, 503, 504)


# ═══════════════════════════════════════════════════════════════════════════
# Anthropic format converters
# ═══════════════════════════════════════════════════════════════════════════

def _to_anthropic_messages(
    messages: List[Dict[str, Any]], system: str = ""
) -> tuple[str, List[Dict[str, Any]]]:
    sys_parts: List[str] = [system] if system else []
    converted: List[Dict[str, Any]] = []
    for msg in messages:
        role = msg.get("role", "")
        content = msg.get("content", "")
        if role == "system":
            sys_parts.append(content)
        elif role == "user":
            converted.append({"role": "user", "content": content})
        elif role == "assistant":
            tool_calls = msg.get("tool_calls", [])
            if tool_calls:
                blocks: List[Dict[str, Any]] = []
                if content:
                    blocks.append({"type": "text", "text": content})
                for tc in tool_calls:
                    func = tc.get("function", {})
                    args_str = func.get("arguments", "{}")
                    try:
                        args = json.loads(args_str) if isinstance(args_str, str) else args_str
                    except (json.JSONDecodeError, TypeError):
                        args = {}
                    blocks.append({
                        "type": "tool_use", "id": tc.get("id", ""),
                        "name": func.get("name", ""), "input": args,
                    })
                converted.append({"role": "assistant", "content": blocks})
            elif content:
                converted.append({"role": "assistant", "content": content})
        elif role == "tool":
            converted.append({
                "role": "user",
                "content": [{"type": "tool_result", "tool_use_id": msg.get("tool_call_id", ""), "content": content}],
            })
    if not converted or converted[0].get("role") != "user":
        converted.insert(0, {"role": "user", "content": "请继续。"})
    return "\n\n".join(p for p in sys_parts if p), converted


def _tools_to_anthropic(tools: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [
        {
            "name": t.get("function", {}).get("name", ""),
            "description": t.get("function", {}).get("description", ""),
            "input_schema": t.get("function", {}).get("parameters", {"type": "object", "properties": {}}),
        }
        for t in tools
    ]


# ═══════════════════════════════════════════════════════════════════════════
# call_llm — 非流式
# ═══════════════════════════════════════════════════════════════════════════

async def call_llm(
    messages: List[Dict[str, Any]] | None = None,
    system: str = "",
    model: str | None = None,
    temperature: float = 0.7,
    max_tokens: int = _DEFAULT_MAX_TOKENS,
    *,
    system_prompt: str | None = None,
    user_message: str | None = None,
    timeout: int | None = None,
) -> str | None:
    if system_prompt is not None:
        system = system_prompt
    if user_message is not None:
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user_message}]
    elif messages is None:
        messages = []
    if system and messages and messages[0].get("role") != "system":
        messages = [{"role": "system", "content": system}] + messages

    _stats_inc("total_calls")
    if _breaker.is_open:
        _stats_inc("circuit_blocked")
        return None

    provider = _resolve_provider(model)
    actual_model = _resolve_model(model, provider)
    api_url = _resolve_url(provider)
    headers = _resolve_headers(provider)
    effective_timeout = timeout or LLM_TIMEOUT_SECONDS

    if provider == "anthropic":
        sys_text, conv_msgs = _to_anthropic_messages(messages, system)
        payload: Dict[str, Any] = {
            "model": actual_model, "max_tokens": _effective_max_tokens(max_tokens),
            "temperature": temperature, "system": sys_text, "messages": conv_msgs,
        }
    else:
        payload = {
            "model": actual_model, "messages": messages,
            "temperature": temperature, "max_tokens": _effective_max_tokens(max_tokens),
        }

    async def _call_once(url: str, hdrs: Dict[str, str], pld: Dict[str, Any], brk: CircuitBreaker) -> str | None:
        for attempt in range(_MAX_RETRIES + 1):
            try:
                async with httpx.AsyncClient(timeout=effective_timeout, trust_env=False) as client:
                    resp = await client.post(url, headers=hdrs, json=pld)
                    resp.raise_for_status()
                    data = resp.json()
                if provider == "anthropic":
                    blocks = data.get("content", [])
                    text = "\n".join(b["text"] for b in blocks if b.get("type") == "text").strip()
                else:
                    msg_obj = data["choices"][0]["message"]
                    raw = msg_obj.get("content") or msg_obj.get("reasoning") or ""
                    text = _strip_think_tags(raw)
                await brk.record_success()
                _stats_inc("success")
                return text or None
            except httpx.HTTPStatusError as exc:
                _stats_inc("server_error" if exc.response.status_code >= 500 else "client_error")
                if _is_retryable(exc.response.status_code) and attempt < _MAX_RETRIES:
                    _stats_inc("retried")
                    await asyncio.sleep(_RETRY_DELAYS[attempt])
                    continue
                break
            except (httpx.TimeoutException, asyncio.TimeoutError):
                _stats_inc("timeout")
                if attempt < _MAX_RETRIES:
                    _stats_inc("retried")
                    await asyncio.sleep(_RETRY_DELAYS[attempt])
                    continue
                break
            except Exception:
                _stats_inc("server_error")
                break
        await brk.record_failure()
        return None

    # 主模型尝试
    if not _breaker.is_open:
        result = await _call_once(api_url, headers, payload, _breaker)
        if result is not None:
            return result

    # 多级 fallback
    for fb in _get_fallback_chain():
        if fb["breaker"].is_open:
            continue
        fb_url = fb["url"]
        fb_hdrs = {"Content-Type": "application/json", "Authorization": f"Bearer {fb['key']}"}
        fb_payload = dict(payload)
        fb_payload["model"] = fb["model"]
        logger.info("call_llm fallback to: %s", fb["model"])
        result = await _call_once(fb_url, fb_hdrs, fb_payload, fb["breaker"])
        if result is not None:
            return result

    return None


# ═══════════════════════════════════════════════════════════════════════════
# call_llm_stream — 流式调用
# ═══════════════════════════════════════════════════════════════════════════

async def call_llm_stream(
    system: str = "",
    message: str = "",
    tools: List[Dict[str, Any]] | None = None,
    history: List[Dict[str, Any]] | None = None,
    model: str | None = None,
    temperature: float = 0.7,
    max_tokens: int = _DEFAULT_MAX_TOKENS,
    *,
    chunk_timeout: float = float(LLM_TIMEOUT_SECONDS),
) -> AsyncGenerator[Dict[str, Any], None]:
    _stats_inc("total_calls")

    provider = _resolve_provider(model)

    # Early exit if no API key configured
    if provider == "anthropic" and not ANTHROPIC_API_KEY:
        yield {"type": "token", "text": "[LLM未配置] 请设置 ANTHROPIC_API_KEY 环境变量。"}
        yield {"type": "done", "reason": "no_api_key"}
        return
    if provider != "anthropic" and not LLM_API_KEY:
        yield {"type": "token", "text": "[LLM未配置] 请在 server/.env 配置 LLM_API_KEY。"}
        yield {"type": "done", "reason": "no_api_key"}
        return

    # ── 构建消息列表 ────────────────────────────────────────────────────
    msgs: List[Dict[str, Any]] = []
    if history:
        msgs.extend(history)
    if system and (not msgs or msgs[0].get("role") != "system"):
        msgs.insert(0, {"role": "system", "content": system})
    if message:
        msgs.append({"role": "user", "content": message})

    def _build_payload(mdl: str, use_anthropic: bool = False) -> Dict[str, Any]:
        if use_anthropic:
            sys_text, conv_msgs = _to_anthropic_messages(msgs, system)
            p: Dict[str, Any] = {
                "model": mdl, "max_tokens": _effective_max_tokens(max_tokens),
                "temperature": temperature, "system": sys_text,
                "messages": conv_msgs, "stream": True,
            }
            if tools:
                p["tools"] = _tools_to_anthropic(tools)
        else:
            p = {
                "model": mdl, "messages": msgs,
                "temperature": temperature, "max_tokens": _effective_max_tokens(max_tokens),
                "stream": True,
            }
            if tools:
                p["tools"] = tools
        return p

    # ── 多级 Fallback 链构建 ────────────────────────────────────────────
    # level 0 = primary, levels 1..N = fallbacks
    fallback_chain = _get_fallback_chain()

    primary_ok = not _breaker.is_open
    # 找到第一个熔断器未开的级别
    start_level = 0
    if not primary_ok:
        start_level = 1
        for i, fb in enumerate(fallback_chain, 1):
            if not fb["breaker"].is_open:
                start_level = i
                break
        else:
            # 全部熔断
            _stats_inc("circuit_blocked")
            yield {"type": "done", "reason": "all_circuit_open"}
            return

    # ── 依次尝试各级 ────────────────────────────────────────────────────
    async def _try_level(level: int) -> AsyncGenerator[Dict[str, Any], None]:
        """尝试指定级别，出错时 yield 错误，由外层决定是否切换到下一级。"""
        if level == 0:
            mdl = _resolve_model(model, provider)
            api_url = _resolve_url(provider)
            hdrs = _resolve_headers(provider)
            breaker = _breaker
            is_anthropic = (provider == "anthropic")
        else:
            fb = fallback_chain[level - 1]
            mdl = fb["model"]
            api_url = fb["url"]
            hdrs = {"Content-Type": "application/json", "Authorization": f"Bearer {fb['key']}"}
            breaker = fb["breaker"]
            is_anthropic = False

        p = _build_payload(mdl, is_anthropic)

        if level > 0:
            logger.info("Switching to fallback level %d: %s @ %s", level, mdl, api_url)
            yield {"type": "status", "step": "fallback_model", "model": mdl,
                   "level": level, "reason": "primary_unavailable"}

        try:
            streamer = _stream_anthropic if is_anthropic else _stream_openai
            async for ev in streamer(api_url, hdrs, p, chunk_timeout):
                yield ev
            await breaker.record_success()
            _stats_inc("success")
        except (httpx.TimeoutException, asyncio.TimeoutError):
            _stats_inc("timeout")
            await breaker.record_failure()
            raise
        except Exception:
            _stats_inc("server_error")
            await breaker.record_failure()
            raise

    last_exc: Exception | None = None
    for level in range(start_level, 1 + len(fallback_chain)):
        try:
            async for ev in _try_level(level):
                yield ev
            return  # 成功返回
        except (httpx.TimeoutException, asyncio.TimeoutError) as exc:
            last_exc = exc
            logger.warning("Level %d timeout (%s), trying next fallback...", level, exc)
            continue
        except Exception as exc:
            last_exc = exc
            logger.warning("Level %d failed (%s), trying next fallback...", level, exc)
            continue

    # 全部失败
    model_tried = _resolve_model(model, provider)
    if isinstance(last_exc, (httpx.TimeoutException, asyncio.TimeoutError)):
        yield {"type": "token", "text": (
            f"\n\n⚠️ **所有模型均响应超时**（等待 {LLM_TIMEOUT_SECONDS}s）。\n\n"
            f"已尝试：主模型（{model_tried}）+ {len(fallback_chain)} 个备用模型。\n\n"
            "请稍等片刻后重试，或检查网络连接状态。"
        )}
        yield {"type": "done", "reason": "timeout"}
    else:
        err_msg = str(last_exc) if last_exc else "unknown"
        yield {"type": "token", "text": (
            f"\n\n⚠️ **大模型调用失败**：{err_msg}\n\n"
            "请检查 server/.env 配置（LLM_API_KEY / LLM_API_URL）是否正确。"
        )}
        yield {"type": "done", "reason": "error", "error": err_msg}


async def _stream_openai(
    api_url: str, headers: Dict[str, str],
    payload: Dict[str, Any], chunk_timeout: float,
) -> AsyncGenerator[Dict[str, Any], None]:
    in_think = False
    text_buf = ""
    tc_acc: Dict[int, Dict[str, Any]] = {}
    usage_totals: Dict[str, int] = {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
    }

    def _as_non_negative_int(raw: Any) -> int:
        try:
            value = int(raw)
        except Exception:
            try:
                value = int(float(raw))
            except Exception:
                return 0
        return max(0, value)

    async with httpx.AsyncClient(timeout=httpx.Timeout(
        connect=10.0, read=chunk_timeout, write=10.0, pool=10.0
    ), trust_env=False) as client:
        async with client.stream("POST", api_url, headers=headers, json=payload) as resp:
            resp.raise_for_status()
            async for raw_line in resp.aiter_lines():
                line = raw_line.strip()
                if not line or not line.startswith("data:"):
                    continue
                data_str = line[5:].strip()
                if data_str == "[DONE]":
                    break
                try:
                    chunk = json.loads(data_str)
                except json.JSONDecodeError:
                    continue
                usage_chunk = chunk.get("usage") if isinstance(chunk.get("usage"), dict) else {}
                if usage_chunk:
                    prompt_tokens = _as_non_negative_int(usage_chunk.get("prompt_tokens"))
                    completion_tokens = _as_non_negative_int(usage_chunk.get("completion_tokens"))
                    total_tokens = _as_non_negative_int(usage_chunk.get("total_tokens"))
                    if total_tokens <= 0 and (prompt_tokens > 0 or completion_tokens > 0):
                        total_tokens = prompt_tokens + completion_tokens
                    usage_totals["prompt_tokens"] = max(usage_totals["prompt_tokens"], prompt_tokens)
                    usage_totals["completion_tokens"] = max(usage_totals["completion_tokens"], completion_tokens)
                    usage_totals["total_tokens"] = max(usage_totals["total_tokens"], total_tokens)

                choices = chunk.get("choices", [])
                if not choices:
                    continue
                delta = choices[0].get("delta", {})
                finish = choices[0].get("finish_reason")

                # tool_calls delta
                for tc_delta in (delta.get("tool_calls") or []):
                    idx = tc_delta.get("index", 0)
                    if idx not in tc_acc:
                        tc_acc[idx] = {"id": tc_delta.get("id", ""), "name": "", "args_buf": ""}
                    func = tc_delta.get("function", {})
                    if "name" in func:
                        tc_acc[idx]["name"] = func["name"]
                    if "arguments" in func:
                        tc_acc[idx]["args_buf"] += func["arguments"]
                    if tc_delta.get("id"):
                        tc_acc[idx]["id"] = tc_delta["id"]

                # text content only — do NOT fall back to "reasoning" in streaming mode.
                # gpt-oss:20b streams reasoning first (English thinking), content second (Chinese reply).
                # The English header in system prompt ensures content is always populated.
                content = delta.get("content", "")
                if content:
                    text_buf += content
                    while True:
                        if in_think:
                            end = text_buf.find("</think>")
                            if end == -1:
                                text_buf = ""
                                break
                            text_buf = text_buf[end + 8:]
                            in_think = False
                        else:
                            start = text_buf.find("<think>")
                            if start == -1:
                                if text_buf:
                                    yield {"type": "token", "text": text_buf}
                                    text_buf = ""
                                break
                            if start > 0:
                                yield {"type": "token", "text": text_buf[:start]}
                            text_buf = text_buf[start + 7:]
                            in_think = True

                if finish in ("tool_calls", "stop") and tc_acc:
                    for idx in sorted(tc_acc):
                        tc = tc_acc[idx]
                        try:
                            args = json.loads(tc["args_buf"]) if tc["args_buf"] else {}
                        except json.JSONDecodeError:
                            args = {}
                        yield {"type": "tool_call", "id": tc["id"], "name": tc["name"], "args": args}
                    tc_acc.clear()

    for idx in sorted(tc_acc):
        tc = tc_acc[idx]
        try:
            args = json.loads(tc["args_buf"]) if tc["args_buf"] else {}
        except json.JSONDecodeError:
            args = {}
        yield {"type": "tool_call", "id": tc["id"], "name": tc["name"], "args": args}

    done_event: Dict[str, Any] = {"type": "done"}
    if any(int(v or 0) > 0 for v in usage_totals.values()):
        done_event["usage"] = usage_totals
    yield done_event


async def _stream_anthropic(
    api_url: str, headers: Dict[str, str],
    payload: Dict[str, Any], chunk_timeout: float,
) -> AsyncGenerator[Dict[str, Any], None]:
    current_tool: Dict[str, Any] | None = None
    tool_buf = ""
    prompt_tokens = 0
    completion_tokens = 0

    def _as_non_negative_int(raw: Any) -> int:
        try:
            value = int(raw)
        except Exception:
            try:
                value = int(float(raw))
            except Exception:
                return 0
        return max(0, value)

    async with httpx.AsyncClient(timeout=httpx.Timeout(
        connect=10.0, read=chunk_timeout, write=10.0, pool=10.0
    ), trust_env=False) as client:
        async with client.stream("POST", api_url, headers=headers, json=payload) as resp:
            resp.raise_for_status()
            async for raw_line in resp.aiter_lines():
                line = raw_line.strip()
                if not line or not line.startswith("data:"):
                    continue
                data_str = line[5:].strip()
                if not data_str:
                    continue
                try:
                    chunk = json.loads(data_str)
                except json.JSONDecodeError:
                    continue
                etype = chunk.get("type", "")
                if etype == "message_start":
                    message_usage = chunk.get("message", {}).get("usage", {}) if isinstance(chunk.get("message"), dict) else {}
                    prompt_tokens = max(prompt_tokens, _as_non_negative_int(message_usage.get("input_tokens")))
                    completion_tokens = max(completion_tokens, _as_non_negative_int(message_usage.get("output_tokens")))
                elif etype == "message_delta":
                    message_usage = chunk.get("usage", {}) if isinstance(chunk.get("usage"), dict) else {}
                    if message_usage:
                        completion_tokens = max(completion_tokens, _as_non_negative_int(message_usage.get("output_tokens")))
                elif etype == "content_block_start":
                    block = chunk.get("content_block", {})
                    if block.get("type") == "tool_use":
                        current_tool = {"id": block.get("id", ""), "name": block.get("name", "")}
                        tool_buf = ""
                elif etype == "content_block_delta":
                    delta = chunk.get("delta", {})
                    if delta.get("type") == "text_delta":
                        text = delta.get("text", "")
                        if text:
                            yield {"type": "token", "text": text}
                    elif delta.get("type") == "input_json_delta":
                        tool_buf += delta.get("partial_json", "")
                elif etype == "content_block_stop":
                    if current_tool is not None:
                        try:
                            args = json.loads(tool_buf) if tool_buf else {}
                        except json.JSONDecodeError:
                            args = {}
                        yield {"type": "tool_call", "id": current_tool["id"], "name": current_tool["name"], "args": args}
                        current_tool = None
                        tool_buf = ""
                elif etype == "message_stop":
                    break

    total_tokens = max(0, prompt_tokens + completion_tokens)
    done_event: Dict[str, Any] = {"type": "done"}
    if total_tokens > 0:
        done_event["usage"] = {
            "prompt_tokens": int(prompt_tokens),
            "completion_tokens": int(completion_tokens),
            "total_tokens": int(total_tokens),
            "input_tokens": int(prompt_tokens),
            "output_tokens": int(completion_tokens),
        }
    yield done_event


# ═══════════════════════════════════════════════════════════════════════════
# call_llm_json — JSON提取
# ═══════════════════════════════════════════════════════════════════════════

async def call_llm_json(
    messages: List[Dict[str, Any]] | None = None,
    system: str = "",
    *,
    system_prompt: str | None = None,
    user_message: str | None = None,
    temperature: float = 0.3,
    max_tokens: int = 2500,
    timeout: int | None = None,
) -> Dict[str, Any] | None:
    if system_prompt is not None:
        system = system_prompt
    if user_message is not None:
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user_message}]
    raw = await call_llm(messages=messages, system=system, temperature=temperature, max_tokens=max_tokens, timeout=timeout)
    if not raw:
        return None
    # 3-level fallback
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        pass
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    if m:
        try:
            return json.loads(m.group())
        except json.JSONDecodeError:
            pass
    return None
