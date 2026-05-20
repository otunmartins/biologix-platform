"""
Asta Scientific Corpus MCP client (streamable HTTP).

Calls Ai2-hosted tools at https://asta-tools.allen.ai/mcp/v1 when ASTA_API_KEY
is set (optional header; higher rate limits with key). Used by mine_literature
as an alternative to direct Semantic Scholar REST.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

ASTA_MCP_URL = "https://asta-tools.allen.ai/mcp/v1"


def _normalize_paper(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Map Asta/S2-shaped dict to mine_literature paper shape."""
    paper_id = raw.get("paperId") or raw.get("paper_id") or raw.get("id") or ""
    authors = raw.get("authors")
    if isinstance(authors, list):
        names = []
        for a in authors[:8]:
            if isinstance(a, dict):
                names.append(a.get("name") or str(a))
            else:
                names.append(str(a))
        author_str = "; ".join(names)
    else:
        author_str = str(authors or "")
    return {
        "paper_id": paper_id,
        "title": raw.get("title") or "",
        "abstract": raw.get("abstract") or raw.get("snippet") or "",
        "url": raw.get("url") or "",
        "year": raw.get("year") or raw.get("publicationYear") or "",
        "authors": author_str,
    }


def papers_from_tool_result(result: Any) -> List[Dict[str, Any]]:
    """
    Extract a list of paper dicts from MCP CallToolResult (text JSON or structured).
    """
    papers: List[Dict[str, Any]] = []

    if result is None:
        return papers

    structured = getattr(result, "structuredContent", None)
    if structured is not None:
        if isinstance(structured, dict):
            for key in ("papers", "data", "results", "items"):
                if key in structured and isinstance(structured[key], list):
                    for item in structured[key]:
                        if isinstance(item, dict):
                            papers.append(_normalize_paper(item))
                    if papers:
                        return papers
            if "title" in structured:
                papers.append(_normalize_paper(structured))
                return papers

    content = getattr(result, "content", None) or []
    for block in content:
        text = getattr(block, "text", None)
        if not text or not text.strip():
            continue
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            continue
        if isinstance(data, list):
            for item in data:
                if isinstance(item, dict):
                    papers.append(_normalize_paper(item))
            continue
        if not isinstance(data, dict):
            continue
        for key in ("papers", "data", "results", "items"):
            if key in data and isinstance(data[key], list):
                for item in data[key]:
                    if isinstance(item, dict):
                        papers.append(_normalize_paper(item))
                break
        else:
            if "title" in data or "paperId" in data:
                papers.append(_normalize_paper(data))
    return papers


async def _search_papers_async(
    *,
    keyword: str,
    limit: int,
    api_key: Optional[str],
    timeout_s: float = 90.0,
    sse_read_timeout_s: float = 180.0,
) -> List[Dict[str, Any]]:
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    headers: Dict[str, str] = {}
    if api_key:
        headers["x-api-key"] = api_key

    url = ASTA_MCP_URL
    async with streamablehttp_client(
        url,
        headers=headers or None,
        timeout=timeout_s,
        sse_read_timeout=sse_read_timeout_s,
    ) as streams:
        read, write, _ = streams
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(
                "search_papers_by_relevance",
                {
                    "keyword": keyword,
                    "fields": "title,abstract,url,year,authors,paperId",
                    "limit": min(limit, 50),
                },
            )
            return papers_from_tool_result(result)


def search_papers_by_relevance_sync(
    *,
    keyword: str,
    limit: int = 12,
    api_key: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Blocking wrapper for MCP search_papers_by_relevance."""
    return asyncio.run(
        _search_papers_async(keyword=keyword, limit=limit, api_key=api_key)
    )
