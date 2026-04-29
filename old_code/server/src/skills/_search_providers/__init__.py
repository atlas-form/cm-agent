"""
_search_providers — 搜索引擎提供商注册表

热插拔入口：运行时替换搜索后端，无需修改任何 skill 代码。

快速替换示例：
    from src.skills._search_providers import set_provider
    from src.skills._search_providers.free_web import FreeWebSearchProvider
    set_provider(FreeWebSearchProvider())

环境变量选择（可选）：
    SEARCH_PROVIDER=free_web   默认，多引擎免费竞速
"""

from __future__ import annotations

import os
from .base import SearchProvider, SearchResult
from .free_web import FreeWebSearchProvider

__all__ = ["SearchProvider", "SearchResult", "FreeWebSearchProvider", "get_provider", "set_provider"]

# 默认使用免费多引擎提供商
_provider: SearchProvider = FreeWebSearchProvider()


def get_provider() -> SearchProvider:
    """获取当前激活的搜索提供商。"""
    return _provider


def set_provider(p: SearchProvider) -> None:
    """
    热替换搜索提供商。调用后所有后续搜索都使用新提供商。

    Args:
        p: 实现了 SearchProvider 接口的提供商实例
    """
    global _provider
    _provider = p
