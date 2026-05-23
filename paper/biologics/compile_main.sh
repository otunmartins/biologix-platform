#!/usr/bin/env bash
# Build paper/biologics/main.tex (fairmeta). Run from repository root or this directory.
set -euo pipefail
cd "$(dirname "$0")"
export TEXINPUTS="$(pwd)/../../docs:${TEXINPUTS:-.//:}"

JOB="main"
pdflatex -interaction=nonstopmode "${JOB}.tex"
bibtex "${JOB}"
pdflatex -interaction=nonstopmode "${JOB}.tex"
pdflatex -interaction=nonstopmode "${JOB}.tex"
