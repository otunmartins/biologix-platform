"""Discovery reporting router (MCP report tools parity)."""

from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from biologix_ai.http_api.deps import session_path

router = APIRouter(prefix="/api/reports", tags=["Reports"])


class CompilePDFRequest(BaseModel):
    experiment_id: str
    markdown_path: str = "SUMMARY_REPORT.md"
    output_pdf_name: str = "SUMMARY_REPORT.pdf"


class WriteSummaryRequest(BaseModel):
    experiment_id: str
    title: str = "Discovery summary"
    include_all_iterations: bool = True


@router.post("/compile-pdf", summary="Convert session Markdown report to PDF")
def compile_pdf(req: CompilePDFRequest) -> Dict[str, Any]:
    from biologix_ai.discovery_report import compile_markdown_to_pdf

    session = session_path(req.experiment_id)
    try:
        return compile_markdown_to_pdf(
            session,
            markdown_filename=req.markdown_path,
            output_pdf_name=req.output_pdf_name,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/write-summary", summary="Auto-build skeleton SUMMARY_REPORT from iteration JSON")
def write_summary(req: WriteSummaryRequest) -> Dict[str, Any]:
    from biologix_ai.discovery_report import write_session_summary_reports

    session = session_path(req.experiment_id)
    try:
        return write_session_summary_reports(
            session,
            title=req.title,
            include_all_iterations=req.include_all_iterations,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
