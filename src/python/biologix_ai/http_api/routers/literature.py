"""Literature mining and search router (MCP literature tools parity)."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from biologix_ai.http_api.deps import RUNS_DIR, session_path

router = APIRouter(prefix="/api/literature", tags=["Literature"])


class MineLiteratureRequest(BaseModel):
    query: str = "hydrogels insulin stabilization transdermal"
    max_candidates: int = Field(default=15, ge=1, le=50)
    iteration: int = Field(default=1, ge=1)
    top_candidates: List[str] = Field(default_factory=list)
    stability_mechanisms: List[str] = Field(default_factory=list)
    limitations: List[str] = Field(default_factory=list)
    use_paper_qa: bool = True
    experiment_id: str = ""


class PaperQARequest(BaseModel):
    question: str


class LookupMaterialRequest(BaseModel):
    material_name: str
    max_results: int = Field(default=5, ge=1, le=20)


class SearchRequest(BaseModel):
    query: str
    max_results: int = Field(default=20, ge=1, le=50)


@router.post("/mine", summary="Mine literature for polymer delivery candidates")
def mine_literature_endpoint(req: MineLiteratureRequest) -> Dict[str, Any]:
    from biologix_ai.services.literature_service import mine_literature

    session_dir: Optional[Path] = None
    if req.experiment_id:
        session_dir = session_path(req.experiment_id)
    return mine_literature(
        query=req.query,
        max_candidates=req.max_candidates,
        iteration=req.iteration,
        top_candidates=req.top_candidates or None,
        stability_mechanisms=req.stability_mechanisms or None,
        limitations=req.limitations or None,
        use_paper_qa=req.use_paper_qa,
        session_dir=session_dir,
    )


@router.post("/paper-qa", summary="PaperQA2 RAG over indexed PDFs")
async def paper_qa_endpoint(req: PaperQARequest) -> Dict[str, Any]:
    from biologix_ai.services.literature_service import paper_qa_query

    result = await paper_qa_query(req.question)
    if result.get("error"):
        raise HTTPException(status_code=400, detail=result["error"])
    return result


@router.get("/paper-qa/status", summary="PaperQA2 index status")
def paper_qa_status() -> Dict[str, Any]:
    from biologix_ai.services.literature_service import paper_qa_index_status

    return paper_qa_index_status()


@router.post("/paper-qa/index", summary="Build PaperQA2 search index")
def index_papers_endpoint() -> Dict[str, str]:
    from biologix_ai.services.literature_service import index_papers

    message = index_papers()
    if "not installed" in message:
        raise HTTPException(status_code=503, detail=message)
    return {"message": message}


@router.post("/lookup-material", summary="PubMed lookup for polymer structure by name")
def lookup_material_endpoint(req: LookupMaterialRequest) -> Dict[str, Any]:
    from biologix_ai.services.literature_service import lookup_material

    result = lookup_material(req.material_name, max_results=req.max_results)
    if result.get("error") and "provide" in result["error"]:
        raise HTTPException(status_code=422, detail=result["error"])
    return result


@router.post("/search/semantic-scholar", summary="Search Semantic Scholar")
def semantic_scholar_endpoint(req: SearchRequest) -> Dict[str, Any]:
    from biologix_ai.services.literature_service import semantic_scholar_search

    return semantic_scholar_search(req.query, max_results=req.max_results)


@router.post("/search/pubmed", summary="Search PubMed")
def pubmed_endpoint(req: SearchRequest) -> Dict[str, Any]:
    from biologix_ai.services.literature_service import pubmed_search

    return pubmed_search(req.query, max_results=req.max_results)


@router.post("/search/arxiv", summary="Search arXiv")
def arxiv_endpoint(req: SearchRequest) -> Dict[str, Any]:
    from biologix_ai.services.literature_service import arxiv_search

    return arxiv_search(req.query, max_results=req.max_results)


@router.post("/search/web", summary="DuckDuckGo web search")
def web_search_endpoint(req: SearchRequest) -> Dict[str, Any]:
    from biologix_ai.services.literature_service import web_search

    result = web_search(req.query, max_results=min(req.max_results, 10))
    if result.get("error"):
        raise HTTPException(status_code=503, detail=result["error"])
    return result
