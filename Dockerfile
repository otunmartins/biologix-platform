# Biologix AI Platform — Docker image
#
# Build (full, ~8-12 GB with models + precursor DB):
#   docker build -t biologix-ai:local .
#
# Build (slim — defers ~800 MB AiZynth models + precursor DB to first run):
#   docker build --build-arg SLIM=1 -t biologix-ai:slim .
#
# Run (via compose — recommended):
#   docker compose run --rm biologix
#
# Run (direct):
#   docker run -it --rm --init -e ANTHROPIC_API_KEY=sk-ant-... biologix-ai:local

# ─────────────────────────────────────────────────────────────────────────────
# Stage: base OS + conda
# ─────────────────────────────────────────────────────────────────────────────
# Pin to amd64: ambertools, openmm, and several other conda-forge packages
# have no linux/aarch64 builds.  On Apple Silicon, Docker Desktop runs this
# image via Rosetta 2 emulation transparently.
FROM --platform=linux/amd64 condaforge/miniforge3:24.11.3-0 AS base

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

# System dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
        git \
        curl \
        ca-certificates \
        unzip \
        build-essential \
        libgl1-mesa-glx \
        libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# Disable the "omnia" channel if it somehow appears in the base image
RUN conda config --remove channels omnia 2>/dev/null || true

WORKDIR /app

# ─────────────────────────────────────────────────────────────────────────────
# Stage: conda env (cached layer — only invalidated when env YML changes)
# ─────────────────────────────────────────────────────────────────────────────
FROM base AS conda-env

# Copy only the env spec first for maximum cache re-use
COPY environment-simulation.yml ./

# -e . requires pyproject.toml, which is not copied until the app stage.
# install_submodules.sh already runs: pip install -e ".[retro,admet,dev]"
RUN sed '/^[[:space:]]*- -e \.$/d' environment-simulation.yml > /tmp/environment-docker.yml \
    && mamba env create -f /tmp/environment-docker.yml \
    && mamba install -n biologix-ai-sim -y -c conda-forge "git>=2.40" \
    && /opt/conda/envs/biologix-ai-sim/bin/git --version \
    && /opt/conda/envs/biologix-ai-sim/bin/python -m pip install "mcp[cli]>=1.0.0" \
    && /opt/conda/envs/biologix-ai-sim/bin/python -c "from importlib.metadata import version; from mcp.server.fastmcp import FastMCP; print('mcp', version('mcp'))" \
    && mamba create -n pymol-viz -y -c conda-forge python=3.11 pymol-open-source \
    && PYMOL_HEADLESS=1 /opt/conda/envs/pymol-viz/bin/pymol -c -d "quit" \
    && mamba clean --all --yes

# ─────────────────────────────────────────────────────────────────────────────
# Stage: full application
# ─────────────────────────────────────────────────────────────────────────────
FROM conda-env AS app

# Build arg: set SLIM=1 to skip AiZynth model download + precursor DB build
ARG SLIM=0
ARG IMAGE_VERSION=dev

# Copy the full repo (after .dockerignore is applied)
COPY . .

# Ensure submodules are present. During `docker build` the build context already
# contains the checked-out submodule directories (user runs
# `git submodule update --init --recursive` before building), so this is a
# no-op if they're there, or a fallback clone if not.
RUN git submodule update --init --recursive 2>/dev/null || true

# Environment wiring
ENV CONDA_ENV=biologix-ai-sim
ENV PYTHONPATH=/app/src/python
ENV RETRO_LLM_BACKEND=skip
ENV BIOLOGIX_AI_AIZYNTH_CONFIG=/app/data/aizynthfinder/config.yml
ENV PATH="/opt/conda/envs/biologix-ai-sim/bin:/root/.opencode/bin:${PATH}"
ENV CONDA_DEFAULT_ENV=biologix-ai-sim
# Conda-forge C++ libs (libLerc, graphviz) require newer libstdc++ than the base image.
ENV LD_LIBRARY_PATH=/opt/conda/envs/biologix-ai-sim/lib
ENV BIOLOGIX_AI_IMAGE_VERSION=${IMAGE_VERSION}
ENV OPENCODE_DISABLE_AUTOUPDATE=true
# Resumable Molport tier-3 downloads during Docker build (avoid HF CDN 408 on streaming).
ENV HF_HUB_DOWNLOAD_TIMEOUT=600
ENV HF_HUB_ETAG_TIMEOUT=60
ENV HF_HUB_ENABLE_HF_TRANSFER=1
# Make conda activate work inside RUN steps
ENV BASH_ENV=/opt/conda/etc/profile.d/conda.sh

# Install submodules, torch, precursor DB, and the project package into the env
RUN source /opt/conda/etc/profile.d/conda.sh && conda activate biologix-ai-sim \
    && bash scripts/install_submodules.sh \
    && python -c "from importlib.metadata import version; from mcp.server.fastmcp import FastMCP; import openmm; print('post-install OK: mcp', version('mcp'))"

# Download AiZynth models (skipped when SLIM=1); then refresh precursor tier 4 (ZINC stock).
RUN if [ "$SLIM" = "0" ]; then \
      source /opt/conda/etc/profile.d/conda.sh && conda activate biologix-ai-sim \
      && bash scripts/setup_aizynthfinder.sh \
      && python scripts/build_precursor_db.py --tiers 4; \
    else \
      echo "SLIM build: skipping AiZynthFinder model download"; \
    fi

# Install OpenCode CLI (pinned for MCP abort/cleanup fixes in 1.17.x)
ARG OPENCODE_VERSION="1.17.4"
RUN curl -fsSL https://opencode.ai/install | bash \
    && /root/.opencode/bin/opencode upgrade "$OPENCODE_VERSION" \
    && /root/.opencode/bin/opencode --version | tee /app/.opencode-version

# Create runtime directories (volume mount points)
RUN mkdir -p /app/runs /app/papers /app/data/aizynthfinder

# Snapshot baked-in data so entrypoint can seed an empty /app/data volume mount
RUN if [ -d /app/data ]; then cp -a /app/data /app/.data-seed; fi

# Strip Windows CRLF line-endings that a Windows clone may have introduced,
# then make the entrypoint executable.
RUN sed -i 's/\r$//' /app/docker/entrypoint.sh /app/docker/restore_terminal.sh /app/docker/cpu_defaults.sh /app/scripts/*.sh 2>/dev/null || true \
    && chmod +x /app/docker/entrypoint.sh /app/docker/restore_terminal.sh /app/scripts/docker_cpu_limit.sh /app/scripts/docker_run.sh /app/scripts/docker_compose_run.sh /app/scripts/host_docker_tty_guard.sh

ENTRYPOINT ["/app/docker/entrypoint.sh"]
