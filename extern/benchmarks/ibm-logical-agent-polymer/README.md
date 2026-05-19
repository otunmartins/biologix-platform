# IBM logical-agent-driven polymer discovery

**Paper:** [Reinforcement Learning with Logical Action-Aware Features for Polymer Discovery](https://research.ibm.com/publications/reinforcement-learning-with-logical-action-aware-features-for-polymer-discovery) (RL4RealLife @ ICML 2021).

**Upstream:** [IBM/logical-agent-driven-polymer-discovery](https://github.com/IBM/logical-agent-driven-polymer-discovery)

Clone **into this directory**:

```bash
cd "$(git rev-parse --show-toplevel)/extern/benchmarks/ibm-logical-agent-polymer"
git clone https://github.com/IBM/logical-agent-driven-polymer-discovery.git
```

**Setup (from upstream README):**

```bash
cd logical-agent-driven-polymer-discovery
pip install -e md-envs
cd data && unzip polymerDiscovery.zip && cd ..
python scripts/update_pickled_function.py
```

**Smoke test CLI:** `python scripts/main.py test -h`

**Non-BO:** Reinforcement learning with logical action-aware features (neuro-symbolic RL), not Bayesian optimization.

**License:** Check the upstream `LICENSE` file before redistribution.
