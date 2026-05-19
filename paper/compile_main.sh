#!/usr/bin/env bash
# Build paper/main.tex (fairmeta). Run from repository root or this directory.
#
# Canonical insulin proposal (fairmeta) lives in ../docs/proposal.tex with
# ../docs/compile_proposal.sh; it shares ./references.bib with this paper.
#
# Question marks for citations usually mean BibTeX was skipped or only one pdflatex
# pass was run. This script runs the full sequence in the paper/ directory so
# main.aux and references.bib line up.
set -euo pipefail
cd "$(dirname "$0")"
export TEXINPUTS="$(pwd)/../docs:${TEXINPUTS:-.//:}"

JOB="main"
pdflatex -interaction=nonstopmode "${JOB}.tex"
bibtex "${JOB}"
pdflatex -interaction=nonstopmode "${JOB}.tex"
pdflatex -interaction=nonstopmode "${JOB}.tex"
