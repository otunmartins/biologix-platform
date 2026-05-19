#!/usr/bin/env bash
# Build the fairmeta manuscript (proposal.tex). Run from anywhere; uses this script's directory.
set -euo pipefail
cd "$(dirname "$0")"
pdflatex -interaction=nonstopmode proposal.tex
bibtex proposal
pdflatex -interaction=nonstopmode proposal.tex
pdflatex -interaction=nonstopmode proposal.tex
