#!/usr/bin/env bash
# Build paper/insulin/response_to_reviewers.tex (no bibliography).
set -euo pipefail
cd "$(dirname "$0")"

JOB="response_to_reviewers"
pdflatex -interaction=nonstopmode "${JOB}.tex"
pdflatex -interaction=nonstopmode "${JOB}.tex"
