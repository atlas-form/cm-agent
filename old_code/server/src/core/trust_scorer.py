"""
信任评分器 — 4级信任 + EMA平滑更新，全异步 DB 持久化。

信任等级：HIGH (>=0.8), MODERATE (>=0.5), LOW (>=0.3), NONE (<0.3)
EMA公式：new_score = α * quality_score + (1 - α) * old_score，α = 0.3
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Tuple

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════════════
# 常量
# ═══════════════════════════════════════════════════════════════════════════

EMA_ALPHA = 0.3
DEFAULT_SCORE = 0.5

TRUST_THRESHOLDS = [
    (0.8, "HIGH"),
    (0.5, "MODERATE"),
    (0.3, "LOW"),
]


def _score_to_level(score: float) -> str:
    """将数值分数转为信任等级字符串。"""
    for threshold, level in TRUST_THRESHOLDS:
        if score >= threshold:
            return level
    return "NONE"


# ═══════════════════════════════════════════════════════════════════════════
# 数据类
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class TrustUpdate:
    role: str
    previous_score: float
    new_score: float
    previous_level: str
    new_level: str
    delta: float
    reason: str


# ═══════════════════════════════════════════════════════════════════════════
# 公共接口
# ═══════════════════════════════════════════════════════════════════════════

async def get_trust_score(role: str) -> Tuple[float, str]:
    """
    获取指定角色的当前信任分数和等级。

    Returns
    -------
    tuple[float, str]
        (score, level)，如无记录则返回默认值。
    """
    try:
        from src.database import get_db
        db = await get_db()
        row = await db.execute_fetchone(
            "SELECT score, trust_level FROM trust_scores WHERE role = ? ORDER BY updated_at DESC LIMIT 1",
            (role,),
        )
        if row:
            return float(row["score"]), str(row["trust_level"])
    except Exception as e:
        logger.warning("Failed to get trust score for %s: %s", role, e)

    return DEFAULT_SCORE, _score_to_level(DEFAULT_SCORE)


async def update_trust(role: str, quality_score: float, quality_passed: bool) -> TrustUpdate:
    """
    用 EMA 更新信任分数并持久化。

    Parameters
    ----------
    role : str
        角色名。
    quality_score : float
        本轮质量评分 (0~1)。
    quality_passed : bool
        本轮质量是否通过。

    Returns
    -------
    TrustUpdate
        更新前后的信任信息。
    """
    old_score, old_level = await get_trust_score(role)

    # EMA 平滑
    new_score = round(EMA_ALPHA * quality_score + (1 - EMA_ALPHA) * old_score, 4)
    new_score = max(0.0, min(1.0, new_score))  # clamp
    new_level = _score_to_level(new_score)
    delta = round(new_score - old_score, 4)

    # 构建原因说明
    if delta > 0:
        reason = f"质量评分{quality_score:.2f}{'(通过)' if quality_passed else '(未通过)'}，信任分上升"
    elif delta < 0:
        reason = f"质量评分{quality_score:.2f}{'(通过)' if quality_passed else '(未通过)'}，信任分下降"
    else:
        reason = f"质量评分{quality_score:.2f}，信任分不变"

    if old_level != new_level:
        reason += f"，等级变化 {old_level} → {new_level}"

    # 持久化
    try:
        from src.database import get_db
        db = await get_db()
        await db.execute(
            """INSERT INTO trust_scores (role, score, trust_level)
               VALUES (?, ?, ?)""",
            (role, new_score, new_level),
        )
        await db.commit()
    except Exception as e:
        logger.warning("Failed to save trust score for %s: %s", role, e)

    return TrustUpdate(
        role=role,
        previous_score=old_score,
        new_score=new_score,
        previous_level=old_level,
        new_level=new_level,
        delta=delta,
        reason=reason,
    )
