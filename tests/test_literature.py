"""
Smoke tests for literature mining components.
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src", "python"))
sys.path.insert(0, ROOT)


def test_semantic_scholar_client_import():
    from insulin_ai.literature.scholar_client import SemanticScholarClient

    assert SemanticScholarClient is not None


def test_semantic_scholar_client_init():
    from insulin_ai.literature.scholar_client import SemanticScholarClient

    client = SemanticScholarClient(api_key=None)
    assert client.base_url == "https://api.semanticscholar.org/graph/v1"
    assert client.rate_limit_delay > 0


def test_materials_literature_miner_no_ollama():
    from insulin_ai.literature.mining_system import MaterialsLiteratureMiner

    m = MaterialsLiteratureMiner()
    assert m.ollama is None
    assert m.scholar is not None


def test_scholar_only_queries_and_seeds():
    from insulin_ai.literature.literature_scholar_only import (
        generate_search_queries,
        seed_candidates_from_papers,
    )

    q = generate_search_queries(1, "insulin patch", None, None, None, None)
    assert len(q) >= 4
    papers = [
        {"title": "PEG hydrogel for insulin", "abstract": "PLGA and chitosan were tested."}
    ]
    seeds = seed_candidates_from_papers(papers)
    names = {s["material_name"] for s in seeds}
    assert "PEG" in names or "hydrogel" in str(seeds)
