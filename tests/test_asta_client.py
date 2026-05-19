"""
Unit tests for Asta MCP result parsing (no live network).
"""

import json
import sys
from types import SimpleNamespace

ROOT = __import__("os").path.dirname(
    __import__("os").path.dirname(__import__("os").path.abspath(__file__))
)
sys.path.insert(0, __import__("os").path.join(ROOT, "src", "python"))


def test_papers_from_tool_result_json_list():
    from insulin_ai.literature.asta_client import papers_from_tool_result

    payload = [
        {
            "paperId": "abc",
            "title": "PEG hydrogels",
            "abstract": "Insulin delivery.",
            "url": "https://example.com/p1",
            "year": 2020,
        }
    ]
    result = SimpleNamespace(
        content=[SimpleNamespace(text=json.dumps(payload))],
        structuredContent=None,
    )
    papers = papers_from_tool_result(result)
    assert len(papers) == 1
    assert papers[0]["title"] == "PEG hydrogels"
    assert papers[0]["paper_id"] == "abc"


def test_papers_from_tool_result_wrapped_data():
    from insulin_ai.literature.asta_client import papers_from_tool_result

    payload = {"data": [{"title": "PLGA study", "abstract": "chitosan blend", "url": "u"}]}
    result = SimpleNamespace(
        content=[SimpleNamespace(text=json.dumps(payload))],
        structuredContent=None,
    )
    papers = papers_from_tool_result(result)
    assert len(papers) == 1
    assert papers[0]["title"] == "PLGA study"


def test_normalize_authors_list():
    from insulin_ai.literature.asta_client import _normalize_paper

    p = _normalize_paper(
        {
            "title": "T",
            "authors": [{"name": "A"}, {"name": "B"}],
        }
    )
    assert "A" in p["authors"] and "B" in p["authors"]
