from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional, Tuple

import httpx

from src.config import (
    ENABLE_ACTION_WEBHOOK_ADAPTER,
    ACTION_WEBHOOK_ALLOWLIST,
    ACTION_WEBHOOK_TIMEOUT_SECONDS,
)
from src.database import get_db
import src.database as db_module

logger = logging.getLogger(__name__)


async def _adapter_create_campaign(payload: Dict[str, Any], ctx: Dict[str, Any]) -> Dict[str, Any]:
    db = await get_db()
    name = str(payload.get("name") or "").strip()
    if not name:
        raise ValueError("campaign name不能为空")
    budget = float(payload.get("budget") or 0)
    status = str(payload.get("status") or "draft")
    product_id = payload.get("product_id")
    platform = str(payload.get("platform") or "").strip()
    campaign_type = str(payload.get("campaign_type") or "standard")

    # 先写本地 DB
    cur = await db.execute(
        """
        INSERT INTO campaigns (user_id, name, product_id, budget, status, metadata)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (ctx["user_id"], name, product_id, budget, status, "{}"),
    )
    await db.commit()
    local_id = cur.lastrowid
    result: Dict[str, Any] = {"campaign_id": local_id, "name": name}

    # 若指定了平台且用户已连接，同步到真实平台
    if platform and budget > 0:
        try:
            from src.services.platform_adapters import get_platform_registry
            registry = await get_platform_registry(ctx.get("user_id", 1), db_module)
            adapter = registry.get(platform)
            if adapter and adapter.is_configured():
                platform_result = await adapter.create_campaign(
                    name=name, budget=budget, campaign_type=campaign_type
                )
                result["platform"] = platform
                result["platform_synced"] = platform_result.success
                result["platform_id"] = platform_result.platform_id
                result["platform_message"] = platform_result.message
                logger.info(f"广告活动已同步到 {platform}: {platform_result.message}")
        except Exception as e:
            logger.warning(f"同步广告活动到平台 {platform} 失败: {e}")
            result["platform_sync_error"] = str(e)

    return result


async def _adapter_update_product_price(payload: Dict[str, Any], ctx: Dict[str, Any]) -> Dict[str, Any]:
    """修改商品价格并同步到真实平台。"""
    product_id = str(payload.get("product_id") or "").strip()
    new_price = float(payload.get("new_price") or payload.get("price") or 0)
    sku_id = str(payload.get("sku_id") or "")
    platform = str(payload.get("platform") or "").strip()

    if not product_id:
        raise ValueError("product_id 不能为空")
    if new_price <= 0:
        raise ValueError("价格必须大于 0")
    if not platform:
        raise ValueError("platform 不能为空，请指定平台（taobao/jd/pdd/douyin）")

    try:
        from src.services.platform_adapters import get_platform_registry
        registry = await get_platform_registry(ctx.get("user_id", 1), db_module)
        adapter = registry.get(platform)
        if adapter is None:
            raise ValueError(f"平台 {platform} 未配置，请先在「平台连接」页面填写 API 凭证")
        if not adapter.is_configured():
            raise ValueError(f"平台 {platform} 凭证不完整，请检查配置")
        result = await adapter.update_price(product_id, new_price, sku_id)
        if not result.success:
            raise ValueError(result.message)
        return {
            "product_id": product_id,
            "platform": platform,
            "new_price": new_price,
            "platform_id": result.platform_id,
            "message": result.message,
        }
    except ValueError:
        raise
    except Exception as e:
        raise ValueError(f"价格同步失败: {e}")


async def _adapter_update_inventory(payload: Dict[str, Any], ctx: Dict[str, Any]) -> Dict[str, Any]:
    """修改商品库存并同步到真实平台。"""
    product_id = str(payload.get("product_id") or "").strip()
    quantity = int(payload.get("quantity") or payload.get("inventory") or 0)
    sku_id = str(payload.get("sku_id") or "")
    platform = str(payload.get("platform") or "").strip()

    if not product_id:
        raise ValueError("product_id 不能为空")
    if quantity < 0:
        raise ValueError("库存数量不能为负数")
    if not platform:
        raise ValueError("platform 不能为空，请指定平台")

    try:
        from src.services.platform_adapters import get_platform_registry
        registry = await get_platform_registry(ctx.get("user_id", 1), db_module)
        adapter = registry.get(platform)
        if adapter is None:
            raise ValueError(f"平台 {platform} 未配置")
        if not adapter.is_configured():
            raise ValueError(f"平台 {platform} 凭证不完整")
        result = await adapter.update_inventory(product_id, quantity, sku_id)
        if not result.success:
            raise ValueError(result.message)
        return {
            "product_id": product_id,
            "platform": platform,
            "quantity": quantity,
            "message": result.message,
        }
    except ValueError:
        raise
    except Exception as e:
        raise ValueError(f"库存同步失败: {e}")


async def _adapter_save_workspace_memory(payload: Dict[str, Any], ctx: Dict[str, Any]) -> Dict[str, Any]:
    db = await get_db()
    role = str(payload.get("role") or "ops")
    key = str(payload.get("key") or "").strip()
    content = str(payload.get("content") or "").strip()
    memory_type = str(payload.get("memory_type") or "fact")
    if not key or not content:
        raise ValueError("memory key/content不能为空")
    existing = await db.execute_fetchone(
        "SELECT id FROM workspace_memory WHERE workspace_id = ? AND role = ? AND key = ?",
        (ctx["workspace_id"], role, key),
    )
    if existing:
        await db.execute(
            "UPDATE workspace_memory SET content = ?, memory_type = ? WHERE id = ?",
            (content, memory_type, existing["id"]),
        )
        await db.commit()
        return {"memory_id": existing["id"], "mode": "updated"}
    cur = await db.execute(
        "INSERT INTO workspace_memory (workspace_id, role, key, content, memory_type) VALUES (?, ?, ?, ?, ?)",
        (ctx["workspace_id"], role, key, content, memory_type),
    )
    await db.commit()
    return {"memory_id": cur.lastrowid, "mode": "created"}


async def _adapter_create_workspace_task(payload: Dict[str, Any], ctx: Dict[str, Any]) -> Dict[str, Any]:
    db = await get_db()
    title = str(payload.get("title") or "").strip()
    if not title:
        raise ValueError("task title不能为空")
    description = str(payload.get("description") or "")
    owner_role = str(payload.get("owner_role") or "ops")
    priority = int(payload.get("priority") or 0)
    depends_on = str(payload.get("depends_on") or "")
    acceptance_criteria = str(payload.get("acceptance_criteria") or "")
    cur = await db.execute(
        """
        INSERT INTO workspace_tasks
        (workspace_id, title, description, owner_role, status, priority, depends_on, acceptance_criteria, result)
        VALUES (?, ?, ?, ?, 'pending', ?, ?, ?, '')
        """,
        (ctx["workspace_id"], title, description, owner_role, priority, depends_on, acceptance_criteria),
    )
    await db.commit()
    return {"task_id": cur.lastrowid, "title": title}


def _allowed_webhook(url: str) -> bool:
    if not ENABLE_ACTION_WEBHOOK_ADAPTER:
        return False
    if not ACTION_WEBHOOK_ALLOWLIST:
        return False
    return any(url.startswith(prefix) for prefix in ACTION_WEBHOOK_ALLOWLIST)


async def _adapter_webhook(payload: Dict[str, Any], ctx: Dict[str, Any]) -> Dict[str, Any]:
    url = str(payload.get("url") or "").strip()
    if not url:
        raise ValueError("webhook url不能为空")
    if not _allowed_webhook(url):
        raise ValueError("webhook url不在允许列表")
    body = payload.get("body")
    if not isinstance(body, dict):
        body = {}
    headers = payload.get("headers")
    if not isinstance(headers, dict):
        headers = {}
    body.update(
        {
            "_ctx": {
                "workspace_id": ctx.get("workspace_id"),
                "run_id": ctx.get("run_id"),
                "task_id": ctx.get("task_id"),
                "user_id": ctx.get("user_id"),
            }
        }
    )
    method = str(payload.get("method") or "POST").upper()
    if method not in {"POST", "PUT", "PATCH"}:
        raise ValueError("webhook method仅支持 POST/PUT/PATCH")
    async with httpx.AsyncClient(timeout=ACTION_WEBHOOK_TIMEOUT_SECONDS, trust_env=False) as client:
        resp = await client.request(method, url, headers=headers, json=body)
    text = resp.text[:1000]
    try:
        parsed = resp.json()
    except Exception:
        parsed = {"raw": text}
    return {"status_code": resp.status_code, "response": parsed}


async def execute_adapter_action(
    *,
    action_type: str,
    payload: Dict[str, Any],
    context: Dict[str, Any],
) -> Tuple[bool, Dict[str, Any]]:
    """
    适配器动作入口:
    - create_campaign / adapter:campaign.create
    - save_workspace_memory / adapter:memory.save
    - create_workspace_task / adapter:task.create
    - webhook_call / adapter:webhook
    """
    at = (action_type or "").strip()
    mapper = {
        "create_campaign": _adapter_create_campaign,
        "adapter:campaign.create": _adapter_create_campaign,
        "save_workspace_memory": _adapter_save_workspace_memory,
        "adapter:memory.save": _adapter_save_workspace_memory,
        "create_workspace_task": _adapter_create_workspace_task,
        "adapter:task.create": _adapter_create_workspace_task,
        "webhook_call": _adapter_webhook,
        "adapter:webhook": _adapter_webhook,
        # 新增：真实平台操作
        "update_product_price": _adapter_update_product_price,
        "adapter:product.update_price": _adapter_update_product_price,
        "update_inventory": _adapter_update_inventory,
        "adapter:product.update_inventory": _adapter_update_inventory,
    }
    fn = mapper.get(at)
    if not fn:
        return False, {"error": f"不支持的adapter action: {at}"}
    try:
        result = await fn(payload, context)
        return True, result
    except Exception as e:
        return False, {"error": str(e)}
