#!/usr/bin/env python3
"""
PaperQA2 settings and indexing — shared by insulin_ai_mcp_server and scripts/index_papers.py.
Uses env vars: PAPER_DIRECTORY, PQA_LLM, PQA_SUMMARY_LLM, PQA_EMBEDDING.
Supports Ollama (e.g. PQA_EMBEDDING=ollama/nomic-embed-text) — no OpenAI key required.
"""

import os
from pathlib import Path


def _repo_root() -> Path:
    """insulin_ai/paper_qa_config.py -> repo root (four levels up)."""
    return Path(__file__).resolve().parent.parent.parent.parent


def _paper_dir() -> str:
    raw = os.environ.get("PAPER_DIRECTORY", "")
    if raw:
        return os.path.abspath(os.path.expanduser(raw))
    return str(_repo_root() / "papers")


def get_paper_qa_settings():
    """Build PaperQA Settings. Compatible with paper-qa v2026."""
    from paperqa import Settings
    from paperqa.settings import AgentSettings, IndexSettings

    return Settings(
        llm=os.environ.get("PQA_LLM", "gpt-4o-mini"),
        summary_llm=os.environ.get("PQA_SUMMARY_LLM", "gpt-4o-mini"),
        embedding=os.environ.get("PQA_EMBEDDING", "text-embedding-3-small"),
        temperature=0.1,
        agent=AgentSettings(
            index=IndexSettings(
                paper_directory=_paper_dir(),
                concurrency=1,
            )
        ),
    )


def build_index() -> str:
    """Build the PaperQA2 search index. Returns status message."""
    import asyncio

    settings = get_paper_qa_settings()
    paper_dir = Path(settings.agent.index.paper_directory)
    if not paper_dir.is_dir():
        return f"Error: Paper directory not found: {paper_dir}"
    pdf_count = sum(1 for _ in paper_dir.rglob("*.pdf"))
    if pdf_count == 0:
        return f"No PDFs in {paper_dir}. Add papers and re-run."
    print(f"Building index: {settings.get_index_name()}")
    print(f"Paper directory: {paper_dir} ({pdf_count} PDFs)")
    try:
        from paperqa.agents.search import get_directory_index
    except ImportError:
        try:
            from paperqa.agent_search import get_directory_index
        except ImportError:
            return "Error: paper-qa indexing API not found. Check paper-qa version."
    asyncio.run(get_directory_index(settings=settings))
    return "Done."
