# Polymer Generative Models Benchmark (Wisconsin)

**Paper:** [Benchmarking study of deep generative models for inverse polymer design](https://doi.org/10.1039/D4DD00395K) (*Digital Discovery*).

**Upstream:** [ytl0410/Polymer-Generative-Models-Benchmark](https://github.com/ytl0410/Polymer-Generative-Models-Benchmark)

Clone **into this directory** (folder name must match `.gitignore`):

```bash
cd "$(git rev-parse --show-toplevel)/extern/benchmarks/polymer-generative-models"
git clone https://github.com/ytl0410/Polymer-Generative-Models-Benchmark.git
```

**Zenodo:** pretrained models and generation results are linked from the upstream README (MOSES folder, GraphINVENT on Zenodo, RL weights, etc.). Do not commit large artifacts to insulin-ai.

**Non-BO:** Core work compares generative models; RL appears for fine-tuning per upstream—not Bayesian optimization over a surrogate.
