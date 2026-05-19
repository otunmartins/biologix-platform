"""LiteratureService: facade over existing literature mining code.

Reuses the existing literature/ package and PaperQA paths. Generalizes
queries by accepting an arbitrary biologic_target parameter.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def search_literature(
    query: str,
    biologic_target: str = "insulin",
    max_results: int = 20,
    sources: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Search scientific literature, parameterized by biologic target.

    Sources: 'semantic_scholar', 'pubmed', 'arxiv'. Defaults to all available.
    """
    if sources is None:
        sources = ["semantic_scholar", "pubmed", "arxiv"]

    results: Dict[str, Any] = {
        "query": query,
        "biologic_target": biologic_target,
        "papers": [],
        "errors": [],
    }

    enriched_query = f"{query} {biologic_target} stabilization excipient polymer"

    for source in sources:
        try:
            if source == "semantic_scholar":
                results["papers"].extend(
                    _search_semantic_scholar(enriched_query, max_results)
                )
            elif source == "pubmed":
                results["papers"].extend(
                    _search_pubmed(enriched_query, max_results)
                )
            elif source == "arxiv":
                results["papers"].extend(
                    _search_arxiv(enriched_query, max_results)
                )
        except Exception as exc:
            results["errors"].append(f"{source}: {exc}")
            logger.warning("Literature search failed for %s: %s", source, exc)

    return results


def _search_semantic_scholar(query: str, max_results: int) -> List[Dict[str, Any]]:
    try:
        from insulin_ai.literature.scholar_client import search as scholar_search
        return scholar_search(query, max_results=max_results)
    except ImportError:
        logger.debug("scholar_client not available")
        return []


def _search_pubmed(query: str, max_results: int) -> List[Dict[str, Any]]:
    try:
        import requests as req

        url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
        params = {"db": "pubmed", "term": query, "retmax": max_results, "retmode": "json"}
        resp = req.get(url, params=params, timeout=15)
        data = resp.json()
        ids = data.get("esearchresult", {}).get("idlist", [])
        if not ids:
            return []

        summary_url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi"
        summary_params = {"db": "pubmed", "id": ",".join(ids), "retmode": "json"}
        summary_resp = req.get(summary_url, params=summary_params, timeout=15)
        summary_data = summary_resp.json().get("result", {})

        papers = []
        for pid in ids:
            info = summary_data.get(pid, {})
            papers.append({
                "pmid": pid,
                "title": info.get("title", ""),
                "source": "pubmed",
                "authors": ", ".join(
                    a.get("name", "") for a in info.get("authors", [])[:3]
                ),
            })
        return papers
    except Exception:
        return []


def _search_arxiv(query: str, max_results: int) -> List[Dict[str, Any]]:
    try:
        import xml.etree.ElementTree as ET

        import requests as req

        url = "http://export.arxiv.org/api/query"
        params = {"search_query": f"all:{query}", "max_results": max_results}
        resp = req.get(url, params=params, timeout=15)
        root = ET.fromstring(resp.text)
        ns = {"atom": "http://www.w3.org/2005/Atom"}
        papers = []
        for entry in root.findall("atom:entry", ns):
            title_el = entry.find("atom:title", ns)
            summary_el = entry.find("atom:summary", ns)
            id_el = entry.find("atom:id", ns)
            papers.append({
                "title": title_el.text.strip() if title_el is not None else "",
                "abstract": summary_el.text.strip() if summary_el is not None else "",
                "arxiv_id": id_el.text.strip() if id_el is not None else "",
                "source": "arxiv",
            })
        return papers
    except Exception:
        return []
