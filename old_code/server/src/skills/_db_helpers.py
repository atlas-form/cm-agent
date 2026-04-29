"""
Skills 层共享 DB 助手 — 提供真实数据查询，供各 Skill 使用。

所有方法均为异步，查询失败时返回空结构（不抛出异常）。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List

logger = logging.getLogger(__name__)


async def load_product_info(user_id: int, product_id: int) -> Dict[str, Any]:
    """
    加载产品基本信息 + 知识库卖点。

    Returns
    -------
    {
      "id", "name", "category", "description", "cost_price", "selling_price",
      "sku", "lifecycle_status",
      "selling_points": [...],   # 来自 product_knowledge（content_type in selling_point/advantage）
      "features": [...],         # 来自 product_knowledge（content_type in feature/spec）
    }
    空字典表示未找到或出错。
    """
    if not user_id or not product_id:
        return {}
    try:
        from src.database import get_db
        db = await get_db()
        row = await db.execute_fetchone(
            "SELECT id, name, category, description, cost_price, selling_price, sku, lifecycle_status "
            "FROM products WHERE id = ? AND user_id = ?",
            (product_id, user_id),
        )
        if not row:
            return {}
        result: Dict[str, Any] = dict(row)

        # 从知识库加载卖点 / 特性
        knowledge_rows = await db.execute_fetchall(
            "SELECT content_type, content FROM product_knowledge "
            "WHERE product_id = ? ORDER BY confidence DESC LIMIT 10",
            (product_id,),
        )
        selling_points: List[str] = []
        features: List[str] = []
        for k in knowledge_rows:
            ct = (k["content_type"] or "").lower()
            text = (k["content"] or "").strip()
            if not text:
                continue
            if ct in ("selling_point", "advantage", "卖点"):
                selling_points.append(text)
            elif ct in ("feature", "spec", "特性", "参数"):
                features.append(text)
            else:
                features.append(text)  # 其他类型归入 features

        result["selling_points"] = selling_points
        result["features"] = features
        return result
    except Exception as e:
        logger.debug("load_product_info failed: %s", e)
        return {}


async def load_user_products(user_id: int, limit: int = 10) -> List[Dict[str, Any]]:
    """
    加载用户商品列表（id, name, category, selling_price, lifecycle_status）。
    """
    if not user_id:
        return []
    try:
        from src.database import get_db
        db = await get_db()
        rows = await db.execute_fetchall(
            "SELECT id, name, category, selling_price, lifecycle_status "
            "FROM products WHERE user_id = ? ORDER BY updated_at DESC LIMIT ?",
            (user_id, limit),
        )
        return [dict(r) for r in rows]
    except Exception as e:
        logger.debug("load_user_products failed: %s", e)
        return []


async def load_metrics_summary(user_id: int, days: int = 30) -> Dict[str, Any]:
    """
    封装 metrics_store.get_summary()，查询失败时返回 {has_data: False}。
    """
    try:
        from src.core.metrics_store import get_summary
        return await get_summary(user_id, days=days)
    except Exception as e:
        logger.debug("load_metrics_summary failed: %s", e)
        return {"has_data": False}


async def load_platform_connections(user_id: int) -> List[Dict[str, Any]]:
    """
    加载用户已配置的平台连接信息（脱敏）。
    Returns [{"platform", "enabled", "shop_id", "last_sync_at"}, ...]
    """
    if not user_id:
        return []
    try:
        from src.database import get_db
        db = await get_db()
        rows = await db.execute_fetchall(
            "SELECT platform, enabled, shop_id, last_sync_at "
            "FROM platform_connections WHERE user_id = ?",
            (user_id,),
        )
        return [dict(r) for r in rows]
    except Exception as e:
        logger.debug("load_platform_connections failed: %s", e)
        return []
