"""
free_web.py — 免费网络搜索提供商

多引擎并行竞速实现，无需付费 API 密钥。

引擎列表（按优先级）：
  1. Tavily AI（配置 TAVILY_API_KEY 时优先，高质量，空结果时回退并行）
  2. Brave Search（配置 BRAVE_API_KEY 时加入竞速，~2s，高质量）
  3. DuckDuckGo library（ddgs 包，通用中文搜索，~7s）
  4. Google News RSS（新闻/趋势，~1s，中文友好）
  5. Wikipedia 中文 API（知识/百科，~1s，精准）
  6. DDG Instant Answers（最终兜底）

竞速策略：
  - DDG 始终参与（全面但慢）
  - 按 topic/关键词选择 1-2 个快速引擎（~1s）并行
  - asyncio.wait(FIRST_COMPLETED) 谁先返回非空就用谁
  - 胜出引擎结果不足 3 条时合并其他已完成引擎的结果
  - 5 秒整体超时，超时后用 DDG Instant 兜底
  - TTL 缓存 5 分钟，相同查询直接复用（bypass_cache=True 跳过）

替换此提供商：
  from src.skills._search_providers import set_provider
  from src.skills._search_providers.codex import CodexSearchProvider
  set_provider(CodexSearchProvider())
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
import urllib.parse
import re
from typing import Any, Dict, List, Optional, Tuple

from .base import SearchProvider, SearchResult

logger = logging.getLogger(__name__)

# ── TTL 缓存（5分钟，最多200条，避免重复搜索）─────────────────────────────────
_SEARCH_CACHE: Dict[str, Tuple[List[SearchResult], float, str]] = {}
_CACHE_TTL = 300  # seconds


def _cache_get(key: str) -> Optional[Tuple[List[SearchResult], str]]:
    if key in _SEARCH_CACHE:
        results, ts, engine = _SEARCH_CACHE[key]
        if time.time() - ts < _CACHE_TTL:
            return results, engine
        del _SEARCH_CACHE[key]
    return None


def _cache_set(key: str, results: List[SearchResult], engine: str) -> None:
    # 不缓存空结果，避免瞬时网络抖动导致同查询在 TTL 内持续返回空。
    if not results:
        return
    _SEARCH_CACHE[key] = (results, time.time(), engine)
    if len(_SEARCH_CACHE) > 200:
        oldest = sorted(_SEARCH_CACHE.items(), key=lambda x: x[1][1])[:50]
        for k, _ in oldest:
            del _SEARCH_CACHE[k]


# ── 结果后处理工具 ────────────────────────────────────────────────────────────

def _deduplicate(results: List[SearchResult]) -> List[SearchResult]:
    """按 URL 去重，保留首次出现的结果。"""
    seen: set = set()
    deduped = []
    for r in results:
        url_key = r.get("url", "").rstrip("/").lower()
        if url_key and url_key in seen:
            continue
        if url_key:
            seen.add(url_key)
        deduped.append(r)
    return deduped


def _contains_cjk(text: str) -> bool:
    """是否包含中日韩字符（用于选择搜索引擎策略）。"""
    if not text:
        return False
    for ch in text:
        if "\u4e00" <= ch <= "\u9fff":
            return True
    return False


def _sanitize_results(results: List[SearchResult], query: str) -> List[SearchResult]:
    """
    过滤明显无关/低质结果，减少“看起来像搜了但没用”的体验。
    规则保持保守，只移除高噪声条目。
    """
    if not results:
        return []

    blocked_url_substrings = (
        "apps.apple.com",
        "play.google.com",
        "duck.ai",
        "substack.com",
        "reddit.com/r/duckduckgo",
    )
    blocked_title_keywords = (
        "ios browser",
        "android browser",
        "daily contest",
        "每日大赛",
        "福利姬",
        "成人视频",
        "porn",
        "xxx",
    )

    kept: List[SearchResult] = []
    q_tokens = [t for t in re.split(r"\s+", query.lower()) if t]
    for r in results:
        title = str(r.get("title", "")).strip()
        body = str(r.get("body", "")).strip()
        url = str(r.get("url", "")).strip()
        low_title = title.lower()
        low_url = url.lower()

        if not title or not url:
            continue
        if any(x in low_url for x in blocked_url_substrings):
            continue
        if any(x in low_title for x in blocked_title_keywords):
            continue

        # 轻量相关性门槛：标题或正文至少包含一个 query token（中英文均可）
        text_blob = f"{low_title} {body.lower()}"
        if q_tokens and not any(tok in text_blob for tok in q_tokens if len(tok) >= 2):
            # 对中文短 query 放宽（否则可能过度过滤）
            if not _contains_cjk(query):
                continue

        kept.append(r)
    return kept


def _rank_results(results: List[SearchResult], query: str) -> List[SearchResult]:
    """按关键词在标题（权重3）和正文（权重1）的出现次数排序。"""
    keywords = [kw for kw in query.lower().split() if len(kw) > 1]
    if not keywords:
        return results

    def _score(r: SearchResult) -> int:
        title = r.get("title", "").lower()
        body = r.get("body", "").lower()
        return sum(3 if kw in title else (1 if kw in body else 0) for kw in keywords)

    return sorted(results, key=_score, reverse=True)


# ── 各引擎封装函数 ────────────────────────────────────────────────────────────

async def _tavily_search(
    query: str, topic: str = "general", max_results: int = 5, days: int = 30
) -> List[SearchResult]:
    """Tavily AI 搜索（高质量，需 TAVILY_API_KEY）。"""
    api_key = os.getenv("TAVILY_API_KEY", "")
    if not api_key:
        return []
    try:
        import httpx
        async with httpx.AsyncClient(timeout=12.0) as client:
            resp = await client.post(
                "https://api.tavily.com/search",
                json={
                    "api_key": api_key,
                    "query": query,
                    "topic": topic,
                    "max_results": max_results,
                    "days": days,
                    "include_answer": True,
                },
            )
            if resp.status_code != 200:
                return []
            data = resp.json()
            return [
                {
                    "title": r.get("title", ""),
                    "body": r.get("content", "")[:600],
                    "url": r.get("url", ""),
                }
                for r in data.get("results", [])
            ]
    except Exception as e:
        logger.debug(f"Tavily search error: {e}")
        return []


async def _brave_search(query: str, max_results: int = 5) -> List[SearchResult]:
    """Brave Search API（免费层2000次/月，质量高，~2s；需 BRAVE_API_KEY）。"""
    api_key = os.getenv("BRAVE_API_KEY", "")
    if not api_key:
        return []
    try:
        import httpx
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(
                "https://api.search.brave.com/res/v1/web/search",
                params={
                    "q": query,
                    "count": max_results,
                    "country": "CN",
                    "search_lang": "zh-hans",
                    "freshness": "pw",  # 过去一周优先
                },
                headers={
                    "Accept": "application/json",
                    "Accept-Encoding": "gzip",
                    "X-Subscription-Token": api_key,
                },
            )
            if resp.status_code != 200:
                return []
            data = resp.json()
            return [
                {
                    "title": r.get("title", ""),
                    "body": r.get("description", "")[:600],
                    "url": r.get("url", ""),
                }
                for r in data.get("web", {}).get("results", [])
            ]
    except Exception as e:
        logger.debug(f"Brave search error: {e}")
        return []


async def _ddg_search(query: str, max_results: int = 5) -> List[SearchResult]:
    """DuckDuckGo 搜索（ddgs 库，无需 API 密钥，~7s）。"""
    try:
        try:
            from ddgs import DDGS
        except ImportError:
            from duckduckgo_search import DDGS  # 旧版兼容
        loop = asyncio.get_event_loop()

        def _sync():
            with DDGS() as d:
                return list(d.text(query, max_results=max_results, region="cn-zh"))

        raw = await asyncio.wait_for(loop.run_in_executor(None, _sync), timeout=7.0)
        if raw:
            return [
                {
                    "title": r.get("title", ""),
                    "body": r.get("body", "")[:600],
                    "url": r.get("href", ""),
                }
                for r in raw
            ]
    except ImportError:
        logger.debug("ddgs not installed; run: uv add ddgs")
    except Exception as e:
        logger.debug(f"DDG library search error: {e}")
    return []


async def _google_news_rss(query: str, max_results: int = 5) -> List[SearchResult]:
    """Google 新闻 RSS（无需 API 密钥，~1s，中文友好）。"""
    try:
        import httpx, xml.etree.ElementTree as ET, re
        from datetime import timezone
        from email.utils import parsedate_to_datetime

        q = urllib.parse.quote(query)
        url = f"https://news.google.com/rss/search?q={q}&hl=zh-CN&gl=CN&ceid=CN:zh-Hans"
        async with httpx.AsyncClient(timeout=4.0, follow_redirects=True) as client:
            resp = await client.get(url, headers={"User-Agent": "Mozilla/5.0"})
        if resp.status_code != 200:
            return []
        root = ET.fromstring(resp.text)
        channel = root.find("channel")
        if channel is None:
            return []
        results = []
        for item in channel.findall("item")[:max_results]:
            title = item.findtext("title", "")
            desc = re.sub(r"<[^>]+>", "", item.findtext("description", ""))[:500]
            if not title:
                continue

            published_at = ""
            raw_pub = item.findtext("pubDate", "")
            if raw_pub:
                try:
                    dt = parsedate_to_datetime(raw_pub)
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    else:
                        dt = dt.astimezone(timezone.utc)
                    published_at = dt.replace(microsecond=0).isoformat().replace("+00:00", "Z")
                except Exception:
                    published_at = ""

            row: SearchResult = {
                "title": title,
                "body": desc,
                "url": item.findtext("link", ""),
            }
            if published_at:
                row["published_at"] = published_at
            results.append(row)
        return results
    except Exception as e:
        logger.debug(f"Google News RSS error: {e}")
        return []


async def _baidu_search(query: str, max_results: int = 5) -> List[SearchResult]:
    """Baidu 网页搜索（无需 API Key，中文场景兜底）。"""
    try:
        import html
        import re
        import httpx

        q = urllib.parse.quote(query)
        url = f"https://www.baidu.com/s?wd={q}"
        async with httpx.AsyncClient(timeout=6.0, follow_redirects=True) as client:
            resp = await client.get(
                url,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/124.0 Safari/537.36"
                    )
                },
            )
        if resp.status_code != 200 or not resp.text:
            return []

        text = resp.text
        results: List[SearchResult] = []

        # 兼容 Baidu 常见结构：h3 + a（结果标题），摘要从相邻片段提取
        h3_matches = re.findall(
            r'<h3[^>]*>.*?<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>.*?</h3>',
            text,
            flags=re.IGNORECASE | re.DOTALL,
        )

        for href, raw_title in h3_matches:
            title = re.sub(r"<[^>]+>", "", raw_title)
            title = html.unescape(title).strip()
            if not title:
                continue
            results.append({
                "title": title[:120],
                "body": "",
                "url": href.strip(),
            })
            if len(results) >= max_results:
                break

        return results
    except Exception as e:
        logger.debug(f"Baidu search error: {e}")
        return []


async def _wikipedia_search(query: str, max_results: int = 3) -> List[SearchResult]:
    """Wikipedia 中文搜索（无需 API 密钥，~1s，适合行业知识/品牌/定义）。"""
    try:
        import httpx, re
        async with httpx.AsyncClient(timeout=6.0) as client:
            resp = await client.get(
                "https://zh.wikipedia.org/w/api.php",
                params={
                    "action": "query",
                    "list": "search",
                    "srsearch": query,
                    "format": "json",
                    "srlimit": max_results,
                    "utf8": "1",
                    "srprop": "snippet|titlesnippet",
                },
                headers={"User-Agent": "Mozilla/5.0"},
            )
        if resp.status_code != 200:
            return []
        results = []
        for item in resp.json().get("query", {}).get("search", []):
            snippet = re.sub(r"<[^>]+>", "", item.get("snippet", ""))
            title = item.get("title", "")
            # 正确编码非 ASCII 标题，避免空格/中文导致 URL 失效
            results.append({
                "title": title,
                "body": snippet[:400],
                "url": f"https://zh.wikipedia.org/wiki/{urllib.parse.quote(title)}",
            })
        return results
    except Exception as e:
        logger.debug(f"Wikipedia search error: {e}")
        return []


async def _ddg_instant(query: str, max_results: int = 5) -> List[SearchResult]:
    """DDG Instant Answers API（最终兜底，~1s）。"""
    try:
        import httpx
        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.get(
                "https://api.duckduckgo.com/",
                params={"q": query, "format": "json", "no_html": "1", "skip_disambig": "1"},
                headers={"User-Agent": "Mozilla/5.0"},
            )
            data = resp.json()
        results = []
        if data.get("Abstract"):
            results.append({
                "title": data.get("Heading", query),
                "body": data["Abstract"][:500],
                "url": data.get("AbstractURL", ""),
            })
        for topic in data.get("RelatedTopics", [])[:max_results]:
            if isinstance(topic, dict) and topic.get("Text"):
                results.append({
                    "title": topic.get("FirstURL", "").split("/")[-1].replace("-", " "),
                    "body": topic["Text"][:400],
                    "url": topic.get("FirstURL", ""),
                })
        return results[:max_results]
    except Exception as e:
        logger.debug(f"DDG instant answers error: {e}")
        return []


async def _jina_search_proxy(query: str, max_results: int = 5) -> List[SearchResult]:
    """
    Jina Reader 代理搜索抓取（无需 API Key）。

    通过 r.jina.ai 抓取公开搜索页并提取结果链接。
    在直连搜索引擎被验证码/反爬拦截时作为可用兜底。
    """
    try:
        import re
        import html
        import httpx

        q = urllib.parse.quote(query)
        target = f"http://www.baidu.com/s?wd={q}"
        url = f"https://r.jina.ai/{target}"

        async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
            resp = await client.get(
                url,
                headers={"User-Agent": "Mozilla/5.0"},
            )
        if resp.status_code != 200 or not resp.text:
            return []

        text = resp.text
        results: List[SearchResult] = []
        seen: set[str] = set()

        # 优先提取结果标题行（Jina 转换后的 Baidu 结果通常为 "### [title](url)"）
        heading_re = re.compile(
            r"^###\s+\[([^\]]{2,180})\]\((https?://[^)]+)\)",
            flags=re.MULTILINE,
        )
        for title, link in heading_re.findall(text):
            t = html.unescape(title).replace("_", "").strip()
            u = link.strip()
            if not t or not u:
                continue
            if u in seen:
                continue
            seen.add(u)
            results.append({"title": t, "body": "", "url": u})
            if len(results) >= max_results:
                break

        # heading 未命中时，回退到通用 markdown 链接提取
        if not results:
            link_re = re.compile(r"\[([^\]]{2,160})\]\((https?://[^)]+)\)")
            for title, link in link_re.findall(text):
                t = html.unescape(title).strip()
                u = link.strip()
                if not t or not u:
                    continue
                low = u.lower()
                if "baidu.com/" in low and "/link?url=" not in low:
                    # 跳过大部分导航链接，仅保留搜索结果跳转链接
                    continue
                if u in seen:
                    continue
                seen.add(u)
                results.append({"title": t, "body": "", "url": u})
                if len(results) >= max_results:
                    break

        return results
    except Exception as e:
        logger.debug(f"Jina proxy search error: {e}")
        return []


# ── 提供商类 ──────────────────────────────────────────────────────────────────

_NEWS_KWS = frozenset(["新闻", "资讯", "最新", "今日", "最近", "政策", "法规", "公告", "规则更新", "趋势", "热销", "爆款"])
_WIKI_KWS = frozenset(["百科", "是什么", "历史", "行业介绍", "知识", "简介", "定义"])


class FreeWebSearchProvider(SearchProvider):
    """
    免费网络多引擎并行竞速提供商。

    无需任何 API 密钥即可工作。
    配置 TAVILY_API_KEY 自动升级为高质量 AI 搜索（空结果时回退并行引擎）。
    配置 BRAVE_API_KEY 加入高质量 Brave 搜索（免费层2000次/月）。
    """

    async def search(
        self,
        query: str,
        topic: str = "general",
        max_results: int = 5,
        days: int = 30,
        bypass_cache: bool = False,
    ) -> Tuple[List[SearchResult], str]:
        # cache key 包含 days，避免不同时间范围复用错误缓存
        cache_key = f"{query}|{topic}|{max_results}|{days}"

        if not bypass_cache:
            cached = _cache_get(cache_key)
            if cached:
                return cached

        # ── 1. Tavily（最高质量，有 API Key 时优先；空结果则继续并行）──
        if os.getenv("TAVILY_API_KEY"):
            results = await _tavily_search(query, topic, max_results, days)
            if results:
                results = _deduplicate(results)
                _cache_set(cache_key, results, "Tavily")
                return results, "Tavily"
            # Tavily 返回空 → 不短路，继续走并行引擎

        # ── 2. 并行竞速：按查询类型选择最合适的引擎组合 ──
        task_engine: Dict[asyncio.Task, str] = {}

        # 中文查询优先百度/Jina，DDG 作为后置兜底，降低低质英文结果干扰。
        is_cjk_query = _contains_cjk(query)
        if not is_cjk_query:
            task_engine[asyncio.create_task(_ddg_search(query, max_results))] = "DuckDuckGo"

        # Brave 有 Key 时加入（高质量，~2s）
        if os.getenv("BRAVE_API_KEY"):
            task_engine[asyncio.create_task(_brave_search(query, max_results))] = "Brave"

        if topic == "news" or any(kw in query for kw in _NEWS_KWS):
            task_engine[asyncio.create_task(_google_news_rss(query, max_results))] = "Google新闻"
            task_engine[asyncio.create_task(_baidu_search(query, max_results))] = "Baidu"
            task_engine[asyncio.create_task(_jina_search_proxy(query, max_results))] = "JinaProxy"
            task_engine[asyncio.create_task(_wikipedia_search(query, max_results))] = "Wikipedia"
        elif any(kw in query for kw in _WIKI_KWS):
            task_engine[asyncio.create_task(_wikipedia_search(query, max_results))] = "Wikipedia"
            task_engine[asyncio.create_task(_baidu_search(query, max_results))] = "Baidu"
            task_engine[asyncio.create_task(_jina_search_proxy(query, max_results))] = "JinaProxy"
            task_engine[asyncio.create_task(_google_news_rss(query, max_results))] = "Google新闻"
        else:
            task_engine[asyncio.create_task(_wikipedia_search(query, max_results))] = "Wikipedia"
            task_engine[asyncio.create_task(_baidu_search(query, max_results))] = "Baidu"
            task_engine[asyncio.create_task(_jina_search_proxy(query, max_results))] = "JinaProxy"
            task_engine[asyncio.create_task(_google_news_rss(query, max_results))] = "Google新闻"

        # ── 3. 竞速：谁先返回非空结果就用谁（12秒整体超时）──
        loop = asyncio.get_event_loop()
        deadline = loop.time() + 12.0
        remaining: set = set(task_engine.keys())
        winner_task: Optional[asyncio.Task] = None
        winner_results: List[SearchResult] = []
        winner_engine = ""

        while remaining:
            timeout_left = deadline - loop.time()
            if timeout_left <= 0:
                break
            done, remaining = await asyncio.wait(
                remaining, timeout=timeout_left, return_when=asyncio.FIRST_COMPLETED
            )
            if not done:
                break
            for task in done:
                try:
                    result = task.result()
                    if result:
                        winner_task = task
                        winner_results = result
                        winner_engine = task_engine[task]
                        # 取消所有其他仍在等待的任务
                        for t in remaining:
                            t.cancel()
                        remaining = set()
                        break
                except Exception:
                    continue
            if winner_results:
                break

        # 取消所有剩余任务
        for t in remaining:
            t.cancel()

        # ── 4. 若胜出引擎结果不足3条，合并其他已完成引擎的结果 ──
        if winner_results and len(winner_results) < 3:
            for task, eng in task_engine.items():
                if task is winner_task:
                    continue
                if task.done() and not task.cancelled():
                    try:
                        other = task.result()
                        if other:
                            winner_results = winner_results + other
                            winner_engine = f"{winner_engine}+{eng}"
                    except Exception:
                        pass

        if winner_results:
            winner_results = _deduplicate(winner_results)
            winner_results = _sanitize_results(winner_results, query)
            winner_results = _rank_results(winner_results, query)[:max(max_results, 3)]
            if winner_results:
                _cache_set(cache_key, winner_results, winner_engine)
                return winner_results, winner_engine

        # 中文查询在竞速为空时，再补一次 DDG 尝试
        if is_cjk_query:
            ddg_results = await _ddg_search(query, max_results)
            ddg_results = _sanitize_results(_deduplicate(ddg_results), query)
            if ddg_results:
                ddg_results = _rank_results(ddg_results, query)[:max(max_results, 3)]
                _cache_set(cache_key, ddg_results, "DuckDuckGo")
                return ddg_results, "DuckDuckGo"

        if winner_results:
            _cache_set(cache_key, winner_results, winner_engine)
            return winner_results, winner_engine

        # 竞速后检查已完成但被忽略的任务（例如同轮多个完成）
        for task, eng in task_engine.items():
            if task.done() and not task.cancelled():
                try:
                    r = task.result()
                    if r:
                        r = _sanitize_results(_deduplicate(r), query)
                        if r:
                            _cache_set(cache_key, r, eng)
                            return r, eng
                except Exception:
                    continue

        # ── 5. 最终兜底：DDG Instant API ──
        results = await _ddg_instant(query, max_results)
        results = _sanitize_results(_deduplicate(results), query)
        engine = "DDG快速"
        _cache_set(cache_key, results, engine)
        return results, engine
