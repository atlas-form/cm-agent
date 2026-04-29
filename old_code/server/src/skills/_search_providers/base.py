"""
base.py — 搜索引擎提供商接口定义

实现此接口即可热插拔替换搜索后端，无需修改任何 skill 代码。

接入新搜索引擎只需三步：
  1. 新建文件  src/skills/_search_providers/my_engine.py
  2. 继承 SearchProvider，实现 search() 方法
  3. 注册：调用 set_provider(MyEngineProvider())
     或在 __init__.py 按环境变量自动选择

示例（切换到当前内置提供商）：
    from src.skills._search_providers import set_provider, FreeWebSearchProvider
    set_provider(FreeWebSearchProvider())
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Tuple

# 单条搜索结果的标准格式
SearchResult = Dict[str, Any]  # {"title": str, "body": str, "url": str}


class SearchProvider(ABC):
    """
    搜索引擎提供商抽象基类。

    所有搜索后端必须实现 search() 方法，返回统一格式的结果列表。
    上层 skill（SearchTrends / SearchCompetitor 等）和内联搜索
    都通过 _web_search() → get_provider().search() 统一调用，
    与具体引擎实现完全解耦。
    """

    @abstractmethod
    async def search(
        self,
        query: str,
        topic: str = "general",
        max_results: int = 5,
        days: int = 30,
        bypass_cache: bool = False,
    ) -> Tuple[List[SearchResult], str]:
        """
        执行搜索，返回 (results, engine_name)。

        Args:
            query:       搜索关键词
            topic:       查询类型，"general" | "news" | "finance"
            max_results: 最多返回条数
            days:        限定最近 N 天内的信息

        Returns:
            results:     [{"title": str, "body": str, "url": str}, ...]
            engine_name: 实际使用的引擎标识（显示在 SSE search_used 事件中）
        """
        ...

    @property
    def provider_name(self) -> str:
        """提供商名称，用于日志标识。"""
        return self.__class__.__name__
