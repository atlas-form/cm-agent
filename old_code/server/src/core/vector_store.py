"""
向量语义搜索 — 基于 embedding 模型，SQLite 存储 + 余弦相似度检索。

工作原理：
1. 文本 -> embedding API -> float 向量
2. 向量存入 knowledge_embeddings 表（JSON blob）
3. 查询时计算余弦相似度，返回 top-K 最相关条目

降级策略：
- embedding 端点不可用 / 无可用模型时，自动 fallback 到关键词 LIKE 搜索。
- 网络瞬时错误时不关闭能力，避免误判为永久故障。
"""

from __future__ import annotations

import json
import logging
import math
import os
from typing import Any, Dict, List, Optional, Tuple

import httpx

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════════════
# 配置与运行时状态
# ═══════════════════════════════════════════════════════════════════════════

_EMBED_TIMEOUT = 10.0
_EMBED_UNAVAILABLE = False
_EMBED_UNAVAILABLE_REASON = ""
_EMBED_CACHED_MODEL = ""
_EMBED_MODEL_BLOCKLIST: set[str] = set()

_KNOWN_EMBED_MODELS: tuple[str, ...] = (
    "doubao-embedding-large-text-250515",
    "doubao-embedding-large-text-240915",
    "doubao-embedding-text-240715",
    "doubao-embedding-text-240515",
    "doubao-embedding-vision-250615",
    "doubao-embedding-vision-250328",
    "doubao-embedding-vision-241215",
    "bge-m3:latest",
)


def _stable_unique(tokens: List[str]) -> List[str]:
    out: List[str] = []
    for token in tokens:
        item = str(token or "").strip()
        if not item or item in out:
            continue
        out.append(item)
    return out


def _derive_embeddings_urls(base_url: str) -> List[str]:
    token = str(base_url or "").strip().rstrip("/")
    if not token:
        return []

    candidates: List[str] = []

    def _add_v1_alternative(prefix_url: str) -> None:
        prefix = str(prefix_url or "").rstrip("/")
        if not prefix:
            return
        if prefix.endswith("/api/v3"):
            root = prefix[: -len("/api/v3")].rstrip("/")
            if root:
                candidates.append(f"{root}/v1/embeddings")
            return
        candidates.append(f"{prefix}/v1/embeddings")

    for suffix in ("/v1/chat/completions", "/chat/completions"):
        if token.endswith(suffix):
            prefix = token[: -len(suffix)]
            if suffix.startswith("/v1"):
                candidates.extend([f"{prefix}/v1/embeddings", f"{prefix}/embeddings"])
            else:
                candidates.append(f"{prefix}/embeddings")
                _add_v1_alternative(prefix)
            break

    if not candidates:
        if token.endswith("/v1"):
            candidates.extend([f"{token}/embeddings", f"{token[: -len('/v1')]}/embeddings"])
        elif token.endswith("/api/v3"):
            candidates.append(f"{token}/embeddings")
            _add_v1_alternative(token)
        elif token.endswith("/embeddings"):
            candidates.append(token)
        else:
            candidates.extend([f"{token}/embeddings", f"{token}/v1/embeddings"])

    return _stable_unique(candidates)

    candidates: List[str] = []

    for suffix in ("/v1/chat/completions", "/chat/completions"):
        if token.endswith(suffix):
            prefix = token[: -len(suffix)]
            if suffix.startswith("/v1"):
                candidates.extend([f"{prefix}/v1/embeddings", f"{prefix}/embeddings"])
            else:
                candidates.extend([f"{prefix}/embeddings", f"{prefix}/v1/embeddings"])
            break

    if not candidates:
        if token.endswith("/v1") or token.endswith("/api/v3"):
            candidates.extend([f"{token}/embeddings", f"{token}/v1/embeddings"])
        elif token.endswith("/embeddings"):
            candidates.append(token)
        else:
            candidates.extend([f"{token}/embeddings", f"{token}/v1/embeddings"])

    return _stable_unique(candidates)


def _extract_error_info(payload: Any) -> tuple[str, str]:
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict):
            code = str(error.get("code") or "").strip()
            message = str(error.get("message") or "").strip()
            return code, message
    return "", str(payload)[:260]


def _is_invalid_model_error(status_code: int, error_code: str, error_message: str) -> bool:
    code = str(error_code or "").lower()
    msg = str(error_message or "").lower()

    if status_code not in {400, 404}:
        return False

    checks = (
        "invalidparameter",
        "model_not_found",
        "invalidendpointormodel.notfound",
        "does not exist",
        "requested model",
        "model",
    )
    if any(item in code for item in checks):
        return True
    if any(item in msg for item in checks):
        return True
    return False


def _build_embed_candidates(cfg: Any) -> List[str]:
    return _stable_unique(
        [
            os.getenv("DOUBAO_EMBEDDING_MODEL", ""),
            os.getenv("LLM_EMBEDDING_MODEL", ""),
            os.getenv("EMBEDDING_MODEL", ""),
            str(getattr(cfg, "DOUBAO_EMBEDDING_MODEL", "") or ""),
            str(getattr(cfg, "LLM_EMBEDDING_MODEL", "") or ""),
            *_KNOWN_EMBED_MODELS,
        ]
    )


def _get_embed_config() -> Tuple[List[str], str, List[str]]:
    """获取 embedding API 配置：URL候选、KEY、候选模型列表。"""
    import src.config as cfg

    cfg_doubao_url = str(getattr(cfg, "DOUBAO_API_URL", "") or "").strip()
    cfg_llm_url = str(getattr(cfg, "LLM_API_URL", "") or "").strip()
    cfg_doubao_key = str(getattr(cfg, "DOUBAO_API_KEY", "") or "").strip()
    cfg_llm_key = str(getattr(cfg, "LLM_API_KEY", "") or "").strip()

    base_url = (
        os.getenv("DOUBAO_API_URL", "").strip()
        or os.getenv("LLM_API_URL", "").strip()
        or cfg_llm_url
        or cfg_doubao_url
    )
    api_key = (
        os.getenv("DOUBAO_API_KEY", "").strip()
        or os.getenv("LLM_API_KEY", "").strip()
        or cfg_llm_key
        or cfg_doubao_key
    )

    embed_urls = _derive_embeddings_urls(base_url)
    model_candidates = _build_embed_candidates(cfg)
    return embed_urls, api_key, model_candidates


async def _request_embedding(
    *,
    embed_url: str,
    api_key: str,
    model: str,
    text: str,
) -> tuple[str, Optional[List[float]], str]:
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"}
    payload = {"model": model, "input": text[:2000]}

    try:
        async with httpx.AsyncClient(timeout=_EMBED_TIMEOUT, trust_env=False) as client:
            resp = await client.post(embed_url, headers=headers, json=payload)
    except httpx.TimeoutException:
        return "transient", None, "timeout"
    except Exception as exc:  # noqa: BLE001
        return "transient", None, str(exc)[:220]

    status = int(resp.status_code or 0)

    body: Any
    try:
        body = resp.json()
    except Exception:
        body = {"raw": resp.text[:300]}

    if status >= 400:
        error_code, error_message = _extract_error_info(body)
        if _is_invalid_model_error(status, error_code, error_message):
            return "model_invalid", None, f"{error_code}:{error_message}"[:260]
        if status == 404:
            return "endpoint_missing", None, f"{error_code}:{error_message}"[:260]
        if status in {401, 403}:
            return "auth_error", None, f"{error_code}:{error_message}"[:260]
        if status == 429 or status >= 500:
            return "transient", None, f"{error_code}:{error_message}"[:260]
        return "hard_error", None, f"{error_code}:{error_message}"[:260]

    rows = body.get("data") if isinstance(body, dict) and isinstance(body.get("data"), list) else []
    if rows and isinstance(rows[0], dict) and isinstance(rows[0].get("embedding"), list):
        vector = rows[0].get("embedding") or []
        if vector:
            return "ok", vector, f"dim={len(vector)}"
    return "invalid_response", None, "missing_embedding"


# ═══════════════════════════════════════════════════════════════════════════
# Embedding 生成
# ═══════════════════════════════════════════════════════════════════════════

async def generate_embedding(text: str) -> Optional[List[float]]:
    """调用 embedding 端点生成向量。失败返回 None。"""
    global _EMBED_UNAVAILABLE
    global _EMBED_UNAVAILABLE_REASON
    global _EMBED_CACHED_MODEL

    if _EMBED_UNAVAILABLE:
        return None
    if not text or not text.strip():
        return None

    embed_urls, api_key, model_candidates = _get_embed_config()
    if not embed_urls or not api_key:
        _EMBED_UNAVAILABLE = True
        _EMBED_UNAVAILABLE_REASON = "config_missing"
        logger.warning("Embedding disabled: config missing (url/key)")
        return None

    ordered_candidates: List[str] = []
    if _EMBED_CACHED_MODEL and _EMBED_CACHED_MODEL not in _EMBED_MODEL_BLOCKLIST:
        ordered_candidates.append(_EMBED_CACHED_MODEL)

    for candidate in model_candidates:
        token = str(candidate or "").strip()
        if not token or token in ordered_candidates:
            continue
        if token in _EMBED_MODEL_BLOCKLIST:
            continue
        ordered_candidates.append(token)

    if not ordered_candidates:
        _EMBED_UNAVAILABLE = True
        _EMBED_UNAVAILABLE_REASON = "no_valid_model"
        logger.warning("Embedding disabled: no model candidate left after blocklist")
        return None

    endpoint_missing_count = 0

    for embed_url in embed_urls:
        for candidate in ordered_candidates:
            kind, vector, detail = await _request_embedding(
                embed_url=embed_url,
                api_key=api_key,
                model=candidate,
                text=text,
            )
            if kind == "ok" and vector is not None:
                _EMBED_CACHED_MODEL = candidate
                return vector

            if kind == "model_invalid":
                _EMBED_MODEL_BLOCKLIST.add(candidate)
                logger.debug("Embedding model invalid, will skip next time: %s (%s)", candidate, detail)
                continue

            if kind == "endpoint_missing":
                endpoint_missing_count += 1
                logger.debug("Embedding endpoint missing: %s", embed_url)
                break

            if kind == "auth_error":
                _EMBED_UNAVAILABLE = True
                _EMBED_UNAVAILABLE_REASON = "auth_error"
                logger.warning("Embedding API auth failed, disabling vector search")
                return None

            if kind == "transient":
                logger.debug("Embedding transient failure with model %s at %s: %s", candidate, embed_url, detail)
                return None

            logger.debug("Embedding request failed with model %s at %s: %s (%s)", candidate, embed_url, kind, detail)

    if endpoint_missing_count >= len(embed_urls):
        _EMBED_UNAVAILABLE = True
        _EMBED_UNAVAILABLE_REASON = "endpoint_missing"
        logger.warning("Embedding API not available at all candidate endpoints: %s", ", ".join(embed_urls))
        return None

    _EMBED_UNAVAILABLE = True
    _EMBED_UNAVAILABLE_REASON = "no_valid_model"
    logger.warning("Embedding disabled: all candidate models rejected")
    return None


# ═══════════════════════════════════════════════════════════════════════════
# 余弦相似度
# ═══════════════════════════════════════════════════════════════════════════

def _cosine_similarity(a: List[float], b: List[float]) -> float:
    """计算两个向量的余弦相似度。"""
    if len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


# ═══════════════════════════════════════════════════════════════════════════
# 数据库操作
# ═══════════════════════════════════════════════════════════════════════════

async def upsert_embedding(
    source_type: str,
    source_id: str,
    content: str,
    metadata: Optional[Dict[str, Any]] = None,
) -> bool:
    """
    生成并存储 embedding。若内容不变则跳过。

    Parameters
    ----------
    source_type : str
        来源类型，如 "product_knowledge", "global_knowledge"
    source_id : str
        来源记录的唯一 ID（字符串化）
    content : str
        要向量化的文本内容
    metadata : dict | None
        附加元数据（存为 JSON）

    Returns
    -------
    bool
        是否成功生成并保存
    """
    if not content or not content.strip():
        return False

    embedding = await generate_embedding(content)
    if embedding is None:
        return False

    try:
        from src.database import get_db

        db = await get_db()
        embedding_json = json.dumps(embedding)
        meta_json = json.dumps(metadata or {}, ensure_ascii=False)
        await db.execute(
            """INSERT OR REPLACE INTO knowledge_embeddings
               (source_type, source_id, content, embedding, metadata)
               VALUES (?, ?, ?, ?, ?)""",
            (source_type, source_id, content[:500], embedding_json, meta_json),
        )
        await db.commit()
        return True
    except Exception as e:  # noqa: BLE001
        logger.warning("Failed to upsert embedding: %s", e)
        return False


async def search_similar(
    query: str,
    source_type: Optional[str] = None,
    filter_ids: Optional[List[str]] = None,
    top_k: int = 5,
    min_score: float = 0.5,
) -> List[Dict[str, Any]]:
    """
    语义相似度搜索。

    Parameters
    ----------
    query : str
        查询文本
    source_type : str | None
        限定来源类型（None=不限）
    filter_ids : list[str] | None
        限定只在这些 source_id 中搜索
    top_k : int
        返回最多 K 条
    min_score : float
        最低相似度阈值（0~1）

    Returns
    -------
    list[dict]
        按相似度降序排列，每条含 source_id, content, score, metadata
    """
    query_embedding = await generate_embedding(query)
    if query_embedding is None:
        # fallback: 返回空，调用方应使用关键词搜索
        return []

    try:
        from src.database import get_db

        db = await get_db()

        sql = "SELECT source_id, content, embedding, metadata FROM knowledge_embeddings"
        conditions = []
        params: List[Any] = []

        if source_type:
            conditions.append("source_type = ?")
            params.append(source_type)
        if filter_ids:
            placeholders = ",".join("?" * len(filter_ids))
            conditions.append(f"source_id IN ({placeholders})")
            params.extend(filter_ids)

        if conditions:
            sql += " WHERE " + " AND ".join(conditions)

        rows = await db.execute_fetchall(sql, tuple(params))
    except Exception as e:  # noqa: BLE001
        logger.warning("Vector search DB query failed: %s", e)
        return []

    results: List[Tuple[float, Dict[str, Any]]] = []
    for row in rows:
        try:
            vec = json.loads(row["embedding"])
            score = _cosine_similarity(query_embedding, vec)
            if score >= min_score:
                meta = {}
                try:
                    meta = json.loads(row["metadata"] or "{}")
                except Exception:
                    pass
                results.append(
                    (
                        score,
                        {
                            "source_id": row["source_id"],
                            "content": row["content"],
                            "score": round(score, 4),
                            "metadata": meta,
                        },
                    )
                )
        except Exception:
            continue

    results.sort(key=lambda x: x[0], reverse=True)
    return [item for _, item in results[:top_k]]


# ═══════════════════════════════════════════════════════════════════════════
# 产品知识语义搜索（供 chat_pipeline 使用）
# ═══════════════════════════════════════════════════════════════════════════

async def search_product_knowledge(
    query: str,
    product_id: int,
    top_k: int = 5,
) -> List[str]:
    """
    对单个产品的知识库进行语义搜索。
    若 embedding 不可用，fallback 到 DB 关键词 LIKE 查询。

    Returns
    -------
    list[str]
        匹配的知识条目文本列表
    """
    results = await search_similar(
        query=query,
        source_type="product_knowledge",
        filter_ids=[str(product_id)],
        top_k=top_k,
    )

    if results:
        return [r["content"] for r in results]

    # Fallback: 关键词 LIKE 搜索
    try:
        from src.database import get_db

        db = await get_db()
        # 提取查询关键词（2字以上中文词）
        import re

        keywords = re.findall(r"[\u4e00-\u9fff]{2,6}", query)
        if not keywords:
            rows = await db.execute_fetchall(
                "SELECT content FROM product_knowledge WHERE product_id = ? ORDER BY confidence DESC LIMIT ?",
                (product_id, top_k),
            )
        else:
            # 用第一个关键词做模糊匹配
            rows = await db.execute_fetchall(
                "SELECT content FROM product_knowledge WHERE product_id = ? AND content LIKE ? ORDER BY confidence DESC LIMIT ?",
                (product_id, f"%{keywords[0]}%", top_k),
            )
            if not rows:
                rows = await db.execute_fetchall(
                    "SELECT content FROM product_knowledge WHERE product_id = ? ORDER BY confidence DESC LIMIT ?",
                    (product_id, top_k),
                )
        return [r["content"] for r in rows]
    except Exception:
        return []


async def index_product_knowledge(product_id: int) -> int:
    """
    批量为一个产品的知识库条目生成 embedding（后台调用）。
    返回成功生成的条数。
    """
    try:
        from src.database import get_db

        db = await get_db()
        rows = await db.execute_fetchall(
            "SELECT id, content FROM product_knowledge WHERE product_id = ? ORDER BY created_at DESC LIMIT 50",
            (product_id,),
        )
        count = 0
        for row in rows:
            ok = await upsert_embedding(
                source_type="product_knowledge",
                source_id=str(product_id),
                content=row["content"],
                metadata={"knowledge_id": row["id"], "product_id": product_id},
            )
            if ok:
                count += 1
        return count
    except Exception as e:  # noqa: BLE001
        logger.warning("Failed to index product knowledge: %s", e)
        return 0


def is_vector_search_available() -> bool:
    """检查向量搜索是否可用（embedding API 未被标记为不可用）。"""
    return not _EMBED_UNAVAILABLE


def vector_search_unavailable_reason() -> str:
    """返回向量能力不可用原因（调试/监控用）。"""
    return str(_EMBED_UNAVAILABLE_REASON or "")




