"""LiteratureService: facade over existing literature mining code.

Reuses the existing literature/ package and PaperQA paths. Generalizes
queries by accepting an arbitrary biologic_target parameter.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
import xml.etree.ElementTree as ET
from pathlib import Path
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
        from biologix_ai.literature.scholar_client import search as scholar_search
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


def semantic_scholar_search(query: str, max_results: int = 20) -> Dict[str, Any]:
    """Search Semantic Scholar (MCP semantic_scholar_search parity)."""
    try:
        from biologix_ai.literature.scholar_client import SemanticScholarClient

        client = SemanticScholarClient(api_key=os.environ.get("SEMANTIC_SCHOLAR_API_KEY"))
        results = client.search_papers(query=query, limit=max_results)
        papers = results.get("data", [])
        return {"query": query, "count": len(papers), "papers": papers}
    except Exception as exc:
        return {"query": query, "count": 0, "papers": [], "error": str(exc)}


def pubmed_search(query: str, max_results: int = 20) -> Dict[str, Any]:
    """Search PubMed with abstracts (MCP pubmed_search parity)."""
    try:
        ids = _pubmed_esearch(query, retmax=max_results)
        if not ids:
            return {"query": query, "count": 0, "papers": []}
        papers = _pubmed_get_abstracts(ids[:max_results])
        return {"query": query, "count": len(papers), "papers": papers}
    except Exception as exc:
        return {"query": query, "count": 0, "papers": [], "error": str(exc)}


def arxiv_search(query: str, max_results: int = 20) -> Dict[str, Any]:
    """Search arXiv (MCP arxiv_search parity)."""
    try:
        import requests as req

        params = {
            "search_query": f"all:{query}",
            "start": 0,
            "max_results": max_results,
            "sortBy": "relevance",
            "sortOrder": "descending",
        }
        resp = req.get(
            "https://export.arxiv.org/api/query",
            params=params,
            headers={"User-Agent": "biologix-ai/1.0 (research@example.com)"},
            timeout=30,
        )
        resp.raise_for_status()
        root = ET.fromstring(resp.content)
        ns = {"atom": "http://www.w3.org/2005/Atom"}
        entries = root.findall("atom:entry", ns)
        papers = []
        for entry in entries:
            title = entry.find("atom:title", ns)
            aid = entry.find("atom:id", ns)
            papers.append({
                "title": (title.text or "").replace("\n", " ").strip() if title is not None else "",
                "arxiv_id": (aid.text or "").split("/")[-1] if aid is not None else "",
                "source": "arxiv",
            })
        return {"query": query, "count": len(papers), "papers": papers}
    except Exception as exc:
        return {"query": query, "count": 0, "papers": [], "error": str(exc)}


def web_search_results(query: str, max_results: int = 5) -> List[Dict[str, Any]]:
    """DuckDuckGo web search results.

    A hard 20-second deadline prevents DDGS from stalling when DuckDuckGo
    rate-limits or the connection hangs mid-stream.
    """
    import concurrent.futures

    try:
        from duckduckgo_search import DDGS
    except ImportError:
        return []

    def _fetch() -> List[Dict[str, Any]]:
        return list(DDGS(timeout=10).text(query, max_results=min(max_results, 10)))

    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
            future = ex.submit(_fetch)
            return future.result(timeout=20.0)
    except Exception:
        return []


def web_search(query: str, max_results: int = 5) -> Dict[str, Any]:
    """Web search via DuckDuckGo (MCP web_search parity)."""
    try:
        from duckduckgo_search import DDGS  # noqa: F401
    except ImportError:
        return {"query": query, "count": 0, "results": [], "error": "pip install duckduckgo-search"}
    results = web_search_results(query, max_results)
    return {"query": query, "count": len(results), "results": results}


def lookup_material(material_name: str, max_results: int = 5) -> Dict[str, Any]:
    """PubMed lookup for polymer structure info by material name."""
    if not material_name or not material_name.strip():
        return {"error": "provide a material name (e.g. chitosan, PLGA, PEG)."}
    try:
        import requests as req  # noqa: F401
    except ImportError:
        return {"error": "requests library required for lookup."}

    query = f"{material_name.strip()} polymer repeat unit SMILES structure"
    try:
        ids = _pubmed_esearch(query, retmax=min(max_results, 10))
        if not ids:
            return {
                "material_name": material_name,
                "query": query,
                "count": 0,
                "papers": [],
                "note": f"No PubMed hits for '{material_name}'.",
            }
        papers = _pubmed_get_abstracts(ids[:max_results])
        return {"material_name": material_name, "query": query, "count": len(papers), "papers": papers}
    except Exception as exc:
        return {"material_name": material_name, "error": str(exc)}


def _pubmed_esearch(query: str, retmax: int) -> List[str]:
    import requests as req

    base = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
    params = {
        "term": query,
        "db": "pubmed",
        "retmax": retmax,
        "retmode": "json",
        "tool": "biologix-ai",
        "email": "research@example.com",
    }
    if os.environ.get("NCBI_API_KEY"):
        params["api_key"] = os.environ["NCBI_API_KEY"]
    time.sleep(0.35)
    resp = req.get(f"{base}/esearch.fcgi", params=params, timeout=15)
    resp.raise_for_status()
    return resp.json().get("esearchresult", {}).get("idlist", [])


def _pubmed_get_abstracts(ids: List[str]) -> List[Dict[str, str]]:
    if not ids:
        return []
    import requests as req

    base = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
    params = {
        "id": ",".join(str(i) for i in ids),
        "db": "pubmed",
        "rettype": "xml",
        "tool": "biologix-ai",
        "email": "research@example.com",
    }
    if os.environ.get("NCBI_API_KEY"):
        params["api_key"] = os.environ["NCBI_API_KEY"]
    time.sleep(0.35)
    resp = req.get(f"{base}/efetch.fcgi", params=params, timeout=15)
    if resp.status_code != 200 or not resp.text.strip():
        return []
    root = ET.fromstring(resp.content)
    out = []
    for art in root.findall(".//PubmedArticle"):
        aid = art.find(".//PMID")
        title = art.find(".//ArticleTitle")
        abstract = art.find(".//AbstractText")
        out.append({
            "pmid": aid.text if aid is not None else "",
            "title": (title.text or "") if title is not None else "",
            "abstract": (abstract.text or "") if abstract is not None else "",
            "source": "pubmed",
        })
    return out


def paper_qa_available() -> bool:
    try:
        import paperqa  # noqa: F401

        return True
    except ImportError:
        return False


def paper_qa_index_status() -> Dict[str, Any]:
    """Check PaperQA2 index status."""
    if not paper_qa_available():
        return {"ready": False, "message": "paper-qa not installed. pip install paper-qa"}
    try:
        from biologix_ai.paper_qa_config import get_paper_qa_settings

        settings = get_paper_qa_settings()
        paper_dir = Path(settings.agent.index.paper_directory)
        if not paper_dir.is_dir():
            return {"ready": False, "message": f"Paper dir not found: {paper_dir}"}
        total = sum(1 for f in paper_dir.rglob("*") if f.suffix.lower() == ".pdf")
        if total == 0:
            return {"ready": False, "message": f"No PDFs in {paper_dir}. Add papers and run index_papers."}
        index_dir = Path(settings.agent.index.index_directory)
        index_name = settings.get_index_name()
        manifest_path = index_dir / index_name / "files.zip"
        if not manifest_path.exists():
            return {"ready": total <= 10, "message": f"0/{total} indexed. Run index_papers first."}
        try:
            import pickle
            import zlib

            manifest = pickle.loads(zlib.decompress(manifest_path.read_bytes()))
            errored = sum(1 for v in manifest.values() if v == "ERROR")
            indexed = len(manifest) - errored
            unindexed = max(0, total - len(manifest))
            ready = unindexed <= 10 and errored == 0
            msg = f"{indexed}/{total} indexed"
            if errored:
                msg += f", {errored} errors"
            if unindexed:
                msg += f", {unindexed} unindexed"
            return {"ready": ready, "message": msg, "indexed": indexed, "total": total}
        except Exception:
            return {"ready": False, "message": "Index may be incomplete. Try running index_papers."}
    except Exception as exc:
        return {"ready": False, "message": str(exc)}


def index_papers() -> str:
    """Build PaperQA2 search index."""
    if not paper_qa_available():
        return "paper-qa not installed. pip install paper-qa"
    from biologix_ai.paper_qa_config import build_index

    return build_index()


async def paper_qa_query(question: str) -> Dict[str, Any]:
    """Async PaperQA2 RAG query."""
    if not paper_qa_available():
        return {"error": "paper-qa not installed. pip install paper-qa"}
    status = paper_qa_index_status()
    if not status.get("ready"):
        return {"error": status.get("message", "Run index_papers first.")}
    try:
        from biologix_ai.paper_qa_config import get_paper_qa_settings
        from paperqa import agent_query

        settings = get_paper_qa_settings()
        response = await agent_query(query=question, settings=settings)
        answer = response.session.formatted_answer
        if not answer:
            return {"error": f"PaperQA could not answer (status: {response.status})"}
        return {"question": question, "answer": answer}
    except Exception as exc:
        return {"error": str(exc)}


def mine_literature(
    query: str = "hydrogels insulin stabilization transdermal",
    max_candidates: int = 15,
    iteration: int = 1,
    top_candidates: Optional[List[str]] = None,
    stability_mechanisms: Optional[List[str]] = None,
    limitations: Optional[List[str]] = None,
    use_paper_qa: bool = True,
    session_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """Mine literature via Asta or Semantic Scholar (MCP mine_literature parity)."""
    out: Dict[str, Any] = {"query": query, "sections": []}

    if use_paper_qa and paper_qa_available():
        status = paper_qa_index_status()
        if status.get("ready"):
            try:
                from biologix_ai.paper_qa_config import get_paper_qa_settings
                from paperqa import agent_query

                pqa_query = (
                    f"What polymer materials and stabilization mechanisms are effective for "
                    f"insulin delivery or transdermal patches? Query focus: {query}"
                )
                if top_candidates or stability_mechanisms:
                    pqa_query += (
                        f". Prior high performers: {top_candidates or 'none'}. "
                        f"Mechanisms: {stability_mechanisms or 'none'}."
                    )
                settings = get_paper_qa_settings()
                response = asyncio.run(agent_query(query=pqa_query, settings=settings))
                if response.session.formatted_answer:
                    out["sections"].append({
                        "source": "paper_qa",
                        "text": response.session.formatted_answer,
                    })
            except Exception as exc:
                out["paper_qa_skipped"] = str(exc)
        elif status.get("message"):
            out["paper_qa_status"] = status["message"]

    try:
        from biologix_ai.literature.literature_scholar_only import (
            format_mine_literature_text,
            run_asta_mine,
            run_scholar_mine,
        )

        asta_key = os.environ.get("ASTA_API_KEY")
        if asta_key:
            results = run_asta_mine(
                asta_api_key=asta_key,
                base_query=query,
                iteration=iteration,
                top_candidates=top_candidates,
                stability_mechanisms=stability_mechanisms,
                limitations=limitations,
                run_dir=session_dir,
                num_candidates=max_candidates,
            )
        else:
            results = run_scholar_mine(
                api_key=os.environ.get("SEMANTIC_SCHOLAR_API_KEY"),
                base_query=query,
                iteration=iteration,
                top_candidates=top_candidates,
                stability_mechanisms=stability_mechanisms,
                limitations=limitations,
                run_dir=session_dir,
                num_candidates=max_candidates,
            )
        out["mining_text"] = format_mine_literature_text(results)
        out["mining_results"] = results
    except Exception as exc:
        out["error"] = str(exc)

    return out
