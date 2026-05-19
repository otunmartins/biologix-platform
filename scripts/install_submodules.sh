#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"

echo "=== Initializing git submodules ==="
cd "$REPO_ROOT"
git submodule update --init --recursive

echo ""
echo "=== Installing RetroSynthesisAgent ==="
if [ -d "extern/RetroSynthesisAgent" ]; then
    # Install RetroSynthesisAgent as an editable package (setup.py is in the submodule).
    # This makes `from RetroSynAgent.treeBuilder import Tree` work without any sys.path tricks.
    echo "  Installing RetroSynthesisAgent as editable package..."
    pip install -e "extern/RetroSynthesisAgent" || echo "  WARNING: editable install failed, falling back to PYTHONPATH"
    echo "  Installing RetroSynthesisAgent Python deps..."
    pip install \
        graphviz \
        pubchempy \
        pyvis \
        scholarly \
        jsonpickle \
        "fake-useragent>=1.4" \
        selenium \
        networkx \
        loguru \
        openai \
        "PyMuPDF>=1.22" \
        "python-dotenv>=1.0" \
        2>/dev/null || pip install \
        graphviz pubchempy pyvis scholarly jsonpickle fake-useragent \
        selenium networkx loguru openai PyMuPDF python-dotenv
    echo "  Verify: python -c \"import sys; sys.path.insert(0,'extern/RetroSynthesisAgent'); from RetroSynAgent.treeBuilder import Tree; print('RetroSynAgent OK')\""
else
    echo "  WARNING: extern/RetroSynthesisAgent not found — run: git submodule update --init --recursive"
fi

echo ""
echo "=== Installing AiZynthFinder from submodule ==="
if [ -d "extern/aizynthfinder" ]; then
    # paretoset is a required dep not always pulled automatically
    pip install paretoset
    pip install -e "extern/aizynthfinder" --no-deps 2>/dev/null || \
        pip install -e "extern/aizynthfinder" || \
        echo "  WARNING: AiZynthFinder install failed — install deps manually"
else
    echo "  WARNING: extern/aizynthfinder not found"
fi

echo ""
echo "=== Installing ADMET-AI from submodule ==="
if [ -d "extern/admet_ai" ]; then
    pip install -e "extern/admet_ai" --no-deps 2>/dev/null || \
        pip install -e "extern/admet_ai" || \
        echo "  WARNING: ADMET-AI install failed — install deps manually"
else
    echo "  WARNING: extern/admet_ai not found"
fi

echo ""
echo "=== Installing insulin-ai with retro + admet extras ==="
pip install -e ".[retro,admet,dev]"

echo ""
echo "=== Done ==="
echo "Verify: python -c 'from aizynthfinder.aizynthfinder import AiZynthFinder; print(\"AiZynthFinder OK\")'"
echo "Verify: python -c 'from admet_ai import ADMETModel; print(\"ADMET-AI OK\")'"
echo "Verify: python -c 'from RetroSynAgent.treeBuilder import Tree; print(\"RetroSynAgent OK\")'"
