"""
产品生命周期状态机 — 规则驱动的状态流转，0 LLM 调用。

状态图：
  draft → 选品中 → 上架中 → 运营中 → 下架 → 归档（终态）
                 ↘ 归档        ↘ 下架
                                       ↗ 运营中（重新上架）

所有状态变更记录在 products.metadata["lifecycle_history"] 中，方便审计。
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════════════
# 状态机定义
# ═══════════════════════════════════════════════════════════════════════════

# 合法的状态转移映射：current → 可达状态列表
VALID_TRANSITIONS: Dict[str, List[str]] = {
    "draft":   ["选品中"],
    "选品中":  ["上架中", "归档"],
    "上架中":  ["运营中", "下架"],
    "运营中":  ["下架"],
    "下架":    ["归档", "运营中"],
    "归档":    [],   # 终态，不可再流转
}

# 所有合法状态集合（用于校验外部输入）
ALL_STATUSES = frozenset(VALID_TRANSITIONS.keys())

# 各状态的中文描述，用于 summary
_STATUS_LABELS: Dict[str, str] = {
    "draft":  "草稿",
    "选品中": "选品中",
    "上架中": "上架中",
    "运营中": "运营中",
    "下架":   "下架",
    "归档":   "归档",
}


# ═══════════════════════════════════════════════════════════════════════════
# 纯函数 — 不访问 DB
# ═══════════════════════════════════════════════════════════════════════════

def can_transition(current: str, target: str) -> bool:
    """
    检查从 current 到 target 的转移是否合法。

    Parameters
    ----------
    current : str
        当前状态。
    target : str
        目标状态。

    Returns
    -------
    bool
        True 表示允许转移。
    """
    return target in VALID_TRANSITIONS.get(current, [])


def get_available_transitions(current: str) -> List[str]:
    """
    获取当前状态下所有可达的下一个状态。

    Parameters
    ----------
    current : str
        当前状态。

    Returns
    -------
    list[str]
        可达状态列表；若 current 不合法则返回空列表。
    """
    return list(VALID_TRANSITIONS.get(current, []))


# ═══════════════════════════════════════════════════════════════════════════
# 异步 DB 操作
# ═══════════════════════════════════════════════════════════════════════════

async def transition_product(product_id: int, target_status: str) -> Dict[str, Any]:
    """
    执行产品状态流转，并将变更历史追加到 products.metadata。

    Parameters
    ----------
    product_id : int
        产品 ID。
    target_status : str
        目标状态，须在 ALL_STATUSES 内且转移合法。

    Returns
    -------
    dict
        {
            "success": bool,
            "previous": str,     # 流转前状态
            "current": str,      # 流转后状态（成功时 == target_status）
            "available_next": list[str],
            "error": str         # 仅在 success=False 时存在
        }
    """
    if target_status not in ALL_STATUSES:
        return {
            "success": False,
            "previous": "",
            "current": "",
            "available_next": [],
            "error": f"无效状态 '{target_status}'，合法状态：{sorted(ALL_STATUSES)}",
        }

    try:
        from src.database import get_db
        db = await get_db()

        row = await db.execute_fetchone(
            "SELECT lifecycle_status, metadata FROM products WHERE id = ?",
            (product_id,),
        )
        if not row:
            return {
                "success": False,
                "previous": "",
                "current": "",
                "available_next": [],
                "error": f"产品 ID={product_id} 不存在",
            }

        current_status: str = row["lifecycle_status"] or "draft"

        if not can_transition(current_status, target_status):
            available = get_available_transitions(current_status)
            return {
                "success": False,
                "previous": current_status,
                "current": current_status,
                "available_next": available,
                "error": (
                    f"不允许从 '{current_status}' 转移到 '{target_status}'。"
                    f"当前可选：{available if available else '（终态，无法流转）'}"
                ),
            }

        # 解析现有 metadata，追加历史记录
        try:
            meta: Dict[str, Any] = json.loads(row["metadata"] or "{}")
        except (json.JSONDecodeError, TypeError):
            meta = {}

        history: List[Dict[str, Any]] = meta.get("lifecycle_history", [])
        history.append({
            "from": current_status,
            "to": target_status,
            "at": datetime.now(timezone.utc).isoformat(),
        })
        meta["lifecycle_history"] = history

        await db.execute(
            """UPDATE products
               SET lifecycle_status = ?, metadata = ?, updated_at = datetime('now')
               WHERE id = ?""",
            (target_status, json.dumps(meta, ensure_ascii=False), product_id),
        )
        await db.commit()

        available_next = get_available_transitions(target_status)
        return {
            "success": True,
            "previous": current_status,
            "current": target_status,
            "available_next": available_next,
        }

    except Exception as e:
        logger.warning("transition_product(%d, %s) failed: %s", product_id, target_status, e)
        return {
            "success": False,
            "previous": "",
            "current": "",
            "available_next": [],
            "error": str(e),
        }


async def get_lifecycle_summary(user_id: int) -> Dict[str, Any]:
    """
    统计指定用户名下各生命周期阶段的产品数量。

    Parameters
    ----------
    user_id : int
        用户 ID。

    Returns
    -------
    dict
        {
            "total": int,
            "by_status": {
                "draft": int,
                "选品中": int,
                "上架中": int,
                "运营中": int,
                "下架": int,
                "归档": int,
            },
            "active_count": int,      # 上架中 + 运营中
            "terminal_count": int,    # 归档
        }
    """
    # 初始化各状态计数为 0
    by_status: Dict[str, int] = {s: 0 for s in ALL_STATUSES}

    try:
        from src.database import get_db
        db = await get_db()

        rows = await db.execute_fetchall(
            """SELECT lifecycle_status, COUNT(*) AS cnt
               FROM products
               WHERE user_id = ?
               GROUP BY lifecycle_status""",
            (user_id,),
        )
        for r in (rows or []):
            status = r["lifecycle_status"] or "draft"
            # 兼容 DB 中存储了非标准值的情况
            if status in by_status:
                by_status[status] = int(r["cnt"])

    except Exception as e:
        logger.warning("get_lifecycle_summary(%d) failed: %s", user_id, e)

    total = sum(by_status.values())
    active_count = by_status.get("上架中", 0) + by_status.get("运营中", 0)
    terminal_count = by_status.get("归档", 0)

    return {
        "total": total,
        "by_status": by_status,
        "active_count": active_count,
        "terminal_count": terminal_count,
    }
