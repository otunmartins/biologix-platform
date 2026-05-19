#!/usr/bin/env python3
"""
InsulinPSMILESEnv — Gym environments adapting IBM's logical-agent polymer
discovery architecture to the insulin-ai OpenMM evaluation pipeline.

Two classes mirror the IBM upstream structure:

* ``InsulinPSMILESEnv`` (base env, registered as ``insulin-ai:InsulinPSMILES-v1``)
  — actions are PSMILES strings drawn from a dynamic candidate pool; each step
  runs ``MDSimulator.evaluate_candidates`` (or a test stub / in-memory cache) and returns a
  4-tier IBM-compatible reward.

* ``LogicalInsulinPSMILESEnv`` (logical wrapper, registered as
  ``insulin-ai:logical-InsulinPSMILES-v1``)
  — wraps the base env, proposes N candidates via ``feedback_guided_mutation``,
  computes a 5-feature logical observation vector per candidate (visited, closer,
  confident, similar, feasible), and uses a GPy GP surrogate for efficient
  training.  The agent's action is a Discrete index into the proposal list.

The evaluation is **identical** to the agentic MCP workflow:
  ``MDSimulator.evaluate_candidates`` → ``PropertyExtractor.extract_feedback``
  → ``scoring.composite_screening_score`` / ``scoring.discovery_score``.

References
----------
* IBM upstream: https://github.com/IBM/logical-agent-driven-polymer-discovery
  (RL4RealLife @ ICML 2021)
* insulin-ai scoring: ``src/python/insulin_ai/simulation/scoring.py``
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

import numpy as np

# ---------------------------------------------------------------------------
# Optional heavy deps — imported lazily so the module is importable without
# a Gym installation for unit-testing the reward/cache logic.
# Both gymnasium (SB3 ≥ 2.0) and gym (legacy) are accepted.
# ---------------------------------------------------------------------------
_GYM_VERSION: str = "none"
gym: Any = None
spaces: Any = None

try:
    import gymnasium as _gymnasium
    from gymnasium import spaces as _gymnasium_spaces

    gym = _gymnasium
    spaces = _gymnasium_spaces
    _GYM_VERSION = "gymnasium"
except ImportError:
    try:
        import gym as _gym  # type: ignore[no-redef]
        from gym import spaces as _gym_spaces  # type: ignore[assignment]

        gym = _gym
        spaces = _gym_spaces
        _GYM_VERSION = "gym"
    except ImportError:
        pass  # gym not installed; Env classes will raise ImportError at instantiation


def _require_gym() -> None:
    """Raise a clear ImportError if no Gym installation is present."""
    if _GYM_VERSION == "none":
        raise ImportError(
            "A Gym installation is required for InsulinPSMILESEnv. "
            "Install with: pip install gymnasium stable-baselines3"
        )


# Minimal base class shim so the module is importable without Gym.
class _EnvBase:
    """No-op base used when Gym is unavailable."""

    metadata: Dict[str, Any] = {}

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)


if _GYM_VERSION != "none":
    _GymEnvBase = gym.Env
else:
    _GymEnvBase = _EnvBase  # type: ignore[assignment,misc]


_ROOT = Path(__file__).resolve().parents[1]

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Reward tier constants (matching IBM upstream defaults)
# ---------------------------------------------------------------------------
DEFAULT_REWARDS: Dict[str, float] = {
    "target": 1.0,
    "valid": -0.01,
    "revisit": -0.5,
    "no-go": -1.0,
}

# Target threshold: interaction energy must be below this (kJ/mol) for reward="target"
DEFAULT_TARGET_ENERGY_KJ: float = -5.0  # matches PropertyExtractor.interaction_favorable_max_kj


# ---------------------------------------------------------------------------
# Tanimoto fingerprint helper (pure RDKit, no external deps)
# ---------------------------------------------------------------------------
def _tanimoto_to_pool(query_smiles: str, pool_smiles: List[str]) -> float:
    """Return maximum Tanimoto similarity of query against all pool SMILES.

    Returns 0.0 if RDKit unavailable or all comparisons fail.
    """
    try:
        from rdkit import Chem, DataStructs

        from insulin_ai.material_mappings import morgan_fingerprint_bit_vect

        qmol = Chem.MolFromSmiles(query_smiles.replace("[*]", "[H]"))
        if qmol is None:
            return 0.0
        qfp = morgan_fingerprint_bit_vect(qmol, radius=2, n_bits=2048)
        best = 0.0
        for smi in pool_smiles:
            pmol = Chem.MolFromSmiles(smi.replace("[*]", "[H]"))
            if pmol is None:
                continue
            pfp = morgan_fingerprint_bit_vect(pmol, radius=2, n_bits=2048)
            sim = float(DataStructs.TanimotoSimilarity(qfp, pfp))
            if sim > best:
                best = sim
    except Exception:
        return 0.0
    return best


# ---------------------------------------------------------------------------
# Base environment
# ---------------------------------------------------------------------------
class InsulinPSMILESEnv(_GymEnvBase):  # type: ignore[misc]
    """Gym environment: each action evaluates one PSMILES against insulin.

    The action space is ``Discrete(n_proposals)`` where the valid actions index
    into a dynamic candidate pool refreshed each step via ``_refresh_pool()``.

    Parameters
    ----------
    seed_psmiles:
        Initial PSMILES to seed the mutation pool (must contain ``[*]``).
    n_proposals:
        Number of candidate PSMILES to propose per step.
    max_steps:
        Maximum steps per episode before ``terminated`` is True.
    n_targets:
        Episode ends early when this many ``target`` rewards are received.
    rewards:
        Dict overriding DEFAULT_REWARDS tier values.
    target_energy_kj:
        Interaction energy threshold (kJ/mol) below which reward is "target".
    md_steps:
        Steps passed to ``MDSimulator`` (default 5000).
    random_seed:
        Seed for NumPy and mutation RNG.
    evaluate_candidates_fn:
        Optional callable ``(candidates, max_candidates) -> md_result_dict`` to
        replace real OpenMM evaluation (inject cache or mock for tests).
    cache_path:
        Optional path to a JSON file mapping PSMILES to prior result rows (for
        unit tests only). The IBM benchmark does not preload a cache so
        evaluation matches the agentic workflow. After a live MD run, results
        are stored in memory and reused for the same canonical PSMILES within the
        process.
    verbose_md:
        Pass ``verbose=True`` to MDSimulator (produces more logging).
    evaluation_log:
        If set, each successful MD evaluation appends a dict with
        ``phase``, ``interaction_energy_kj_mol``, ``psmiles``, ``tier``.
        Used by the IBM benchmark to emit ``evaluation_trace`` in JSON.
    evaluation_log_phase:
        Label stored on each log entry (e.g. ``"train"`` / ``"test"``).
    """

    metadata = {"render_modes": []}

    def __init__(
        self,
        seed_psmiles: str = "[*]OCC[*]",
        n_proposals: int = 20,
        max_steps: int = 100,
        n_targets: int = 5,
        rewards: Optional[Dict[str, float]] = None,
        target_energy_kj: float = DEFAULT_TARGET_ENERGY_KJ,
        md_steps: int = 5000,
        random_seed: int = 42,
        evaluate_candidates_fn: Optional[
            Callable[[List[Dict[str, Any]], int], Dict[str, Any]]
        ] = None,
        cache_path: Optional[str] = None,
        verbose_md: bool = False,
        evaluation_log: Optional[List[Dict[str, Any]]] = None,
        evaluation_log_phase: str = "unknown",
        initial_best_interaction_energy_kj_mol: Optional[float] = None,
    ) -> None:
        _require_gym()
        super().__init__()

        self.seed_psmiles = seed_psmiles
        self.n_proposals = n_proposals
        self.max_steps = max_steps
        self.n_targets = n_targets
        self.rewards = dict(DEFAULT_REWARDS)
        if rewards:
            self.rewards.update(rewards)
        self.target_energy_kj = target_energy_kj
        self.md_steps = md_steps
        self.random_seed = random_seed
        self.verbose_md = verbose_md
        self._evaluate_candidates_fn = evaluate_candidates_fn
        self._evaluation_log = evaluation_log
        self.evaluation_log_phase = evaluation_log_phase
        # Cumulative best interaction energy (kJ/mol) over the whole benchmark run;
        # not reset in reset() so train→test and multi-episode stats stay comparable.
        self._best_interaction_energy_kj_mol: Optional[float] = (
            float(initial_best_interaction_energy_kj_mol)
            if initial_best_interaction_energy_kj_mol is not None
            else None
        )

        # --- Gym spaces (only constructed when Gym is available) ----------
        if _GYM_VERSION != "none":
            self.action_space = spaces.Discrete(n_proposals)
            self.observation_space = spaces.Box(
                low=0.0, high=1.0, shape=(n_proposals * 5,), dtype=np.float32
            )

        # --- Pre-computed cache -------------------------------------------
        self._cache: Dict[str, Dict[str, Any]] = {}
        if cache_path:
            self._load_cache(cache_path)

        # --- MDSimulator (lazy) ------------------------------------------
        self._sim: Any = None  # initialised on first use

        # --- Episode state -----------------------------------------------
        self._rng = np.random.default_rng(random_seed)
        self._current_pool: List[str] = []  # PSMILES proposals for current step
        self._visited: Set[str] = set()
        self._n_steps: int = 0
        self._target_steps: int = 0
        self._feedback_state: Dict[str, Any] = {
            "high_performer_psmiles": [],
            "problematic_psmiles": [],
            "top_candidates": [],
            "stability_mechanisms": [],
            "limitations": [],
            "target_properties": {},
        }
        # Last MD result (for info dict)
        self._last_md_result: Optional[Dict[str, Any]] = None
        self._obs: np.ndarray = np.zeros(n_proposals * 5, dtype=np.float32)

    def _record_interaction_energy_from_row(self, row: Dict[str, Any]) -> None:
        """Update cumulative best from an MD row (any path that yields a real energy)."""
        e_int = row.get("interaction_energy_kj_mol")
        if e_int is None:
            return
        fe = float(e_int)
        if self._best_interaction_energy_kj_mol is None:
            self._best_interaction_energy_kj_mol = fe
        else:
            self._best_interaction_energy_kj_mol = min(
                self._best_interaction_energy_kj_mol, fe
            )

    def _append_evaluation_log(
        self, psmiles: str, tier: str, row: Dict[str, Any]
    ) -> None:
        e_int = row.get("interaction_energy_kj_mol")
        if e_int is None:
            return
        self._record_interaction_energy_from_row(row)
        if self._evaluation_log is None:
            return
        self._evaluation_log.append(
            {
                "phase": self.evaluation_log_phase,
                "interaction_energy_kj_mol": float(e_int),
                "psmiles": psmiles,
                "tier": tier,
            }
        )

    # ------------------------------------------------------------------
    # Cache helpers
    # ------------------------------------------------------------------
    def _load_cache(self, cache_path: str) -> None:
        """Load a JSON cache mapping PSMILES → md_result_row."""
        p = Path(cache_path)
        if not p.is_file():
            logger.warning("Cache file not found: %s", cache_path)
            return
        try:
            with open(p) as f:
                data = json.load(f)
            if isinstance(data, dict):
                self._cache = data
            elif isinstance(data, list):
                for entry in data:
                    ps = entry.get("psmiles")
                    if ps:
                        self._cache[ps] = entry
            logger.info("Loaded %d cached PSMILES evaluations from %s", len(self._cache), p)
        except Exception as e:
            logger.warning("Failed to load cache %s: %s", cache_path, e)

    def add_to_cache(self, psmiles: str, result_row: Dict[str, Any]) -> None:
        """Add a single evaluation result to the in-memory cache."""
        self._cache[psmiles] = result_row

    # ------------------------------------------------------------------
    # MDSimulator lazy init
    # ------------------------------------------------------------------
    def _get_sim(self) -> Any:
        if self._sim is None:
            from insulin_ai.simulation import MDSimulator

            self._sim = MDSimulator(n_steps=self.md_steps)
        return self._sim

    # ------------------------------------------------------------------
    # Mutation pool refresh
    # ------------------------------------------------------------------
    def _build_initial_pool(self) -> List[str]:
        """Generate initial proposal pool seeded from seed_psmiles."""
        from insulin_ai.material_mappings import validate_psmiles
        from insulin_ai.mutation import feedback_guided_mutation

        vr = validate_psmiles(self.seed_psmiles)
        canonical = str(vr.get("canonical") or self.seed_psmiles)
        fb = dict(self._feedback_state)
        fb["high_performer_psmiles"] = [canonical]
        mutated = feedback_guided_mutation(
            fb,
            library_size=self.n_proposals * 2,
            random_seed=int(self._rng.integers(0, 1_000_000)),
        )
        pool: List[str] = []
        seen: Set[str] = set()
        for c in mutated:
            ps = c.get("chemical_structure") or c.get("psmiles")
            if ps and ps not in seen:
                vr2 = validate_psmiles(str(ps))
                if vr2.get("valid"):
                    pool.append(str(vr2.get("canonical") or ps))
                    seen.add(pool[-1])
                if len(pool) >= self.n_proposals:
                    break
        # Pad with seed if needed
        while len(pool) < self.n_proposals:
            pool.append(canonical)
        return pool[:self.n_proposals]

    def refresh_pool(self) -> List[str]:
        """Refresh the candidate pool using current feedback state."""
        return self._build_initial_pool()

    # ------------------------------------------------------------------
    # Gym API
    # ------------------------------------------------------------------
    def reset(
        self,
        *,
        seed: Optional[int] = None,
        options: Optional[Dict[str, Any]] = None,
    ) -> Tuple[np.ndarray, Dict[str, Any]]:
        if _GYM_VERSION == "gymnasium":
            super().reset(seed=seed)
        if seed is not None:
            self._rng = np.random.default_rng(seed)

        self._visited = set()
        self._n_steps = 0
        self._target_steps = 0
        self._last_md_result = None
        self._feedback_state = {
            "high_performer_psmiles": [],
            "problematic_psmiles": [],
            "top_candidates": [],
            "stability_mechanisms": [],
            "limitations": [],
            "target_properties": {},
        }
        self._current_pool = self._build_initial_pool()
        self._obs = np.zeros(self.n_proposals * 5, dtype=np.float32)

        info = {
            "pool": list(self._current_pool),
            "n_visited": 0,
            "n_cache_entries": len(self._cache),
        }
        return self._obs.copy(), info

    def step(
        self, action: int
    ) -> Tuple[np.ndarray, float, bool, bool, Dict[str, Any]]:
        """Select action-th PSMILES from current pool and evaluate it.

        Returns
        -------
        obs, reward, terminated, truncated, info
        """
        self._n_steps += 1
        psmiles = self._current_pool[int(action) % self.n_proposals]

        reward, tier, md_row = self._execute(psmiles)

        # Update episode state
        if tier == "target":
            self._target_steps += 1
        if md_row is not None:
            self._update_feedback(psmiles, md_row)

        # Check done
        terminated = (
            self._n_steps >= self.max_steps
            or self._target_steps >= self.n_targets
        )
        truncated = False

        # Refresh pool for next step
        self._current_pool = self._build_initial_pool()

        info = {
            "tier": tier,
            "psmiles": psmiles,
            "n_visited": len(self._visited),
            "target_steps": self._target_steps,
            "n_steps": self._n_steps,
            "pool": list(self._current_pool),
            "md_row": md_row,
        }
        return self._obs.copy(), reward, terminated, truncated, info

    # ------------------------------------------------------------------
    # Internal: evaluate one PSMILES
    # ------------------------------------------------------------------
    def _execute(
        self, psmiles: str
    ) -> Tuple[float, str, Optional[Dict[str, Any]]]:
        """Evaluate psmiles, return (reward_float, tier_str, md_row_or_None)."""
        from insulin_ai.material_mappings import prescreen_psmiles_for_md, validate_psmiles

        # Validate first
        vr = validate_psmiles(psmiles)
        if not vr.get("valid"):
            return self.rewards["no-go"], "no-go", None

        # Prescreen for MD compatibility
        pre = prescreen_psmiles_for_md(psmiles)
        if not pre.get("ok"):
            return self.rewards["no-go"], "no-go", None

        canonical = str(vr.get("canonical") or psmiles)

        # Check revisit
        if canonical in self._visited:
            return self.rewards["revisit"], "revisit", None
        self._visited.add(canonical)

        # Cache lookup
        if canonical in self._cache:
            row = self._cache[canonical]
            tier = self._tier_from_row(row)
            self._append_evaluation_log(canonical, tier, row)
            return self._reward_from_row(row), tier, row

        # Real evaluation
        try:
            candidate = [{"material_name": "Candidate_0", "chemical_structure": canonical}]
            if self._evaluate_candidates_fn is not None:
                result = self._evaluate_candidates_fn(candidate, 1)
            else:
                sim = self._get_sim()
                result = sim.evaluate_candidates(
                    candidate, max_candidates=1, verbose=self.verbose_md
                )
            self._last_md_result = result

            pa = result.get("property_analysis") or {}
            row = pa.get("Candidate_0") or {}
            row["psmiles"] = canonical

            # Cache the result for future episodes
            self._cache[canonical] = row

            tier = self._tier_from_row(row)
            self._append_evaluation_log(canonical, tier, row)
            return self._reward_from_row(row), tier, row
        except Exception as e:
            logger.warning("Evaluation failed for %s: %s", psmiles, e)
            return self.rewards["no-go"], "no-go", None

    def _reward_from_row(self, row: Dict[str, Any]) -> float:
        if row is None:
            return self.rewards["no-go"]
        e_int = row.get("interaction_energy_kj_mol")
        if e_int is None:
            return self.rewards["valid"]
        if float(e_int) <= self.target_energy_kj:
            return self.rewards["target"]
        return self.rewards["valid"]

    def _tier_from_row(self, row: Dict[str, Any]) -> str:
        if row is None:
            return "no-go"
        e_int = row.get("interaction_energy_kj_mol")
        if e_int is None:
            return "valid"
        return "target" if float(e_int) <= self.target_energy_kj else "valid"

    # ------------------------------------------------------------------
    # Feedback update
    # ------------------------------------------------------------------
    def _update_feedback(
        self, psmiles: str, row: Dict[str, Any]
    ) -> None:
        """Integrate one MD result into feedback_state for next mutation."""
        e_int = row.get("interaction_energy_kj_mol")
        if e_int is not None and float(e_int) <= self.target_energy_kj:
            hp = self._feedback_state.setdefault("high_performer_psmiles", [])
            if psmiles not in hp:
                hp.append(psmiles)
        elif e_int is not None and float(e_int) > 50.0:
            prob = self._feedback_state.setdefault("problematic_psmiles", [])
            if psmiles not in prob:
                prob.append(psmiles)


# ---------------------------------------------------------------------------
# Logical wrapper (mirrors LogicalAgentDrivenPolymerDiscovery)
# ---------------------------------------------------------------------------
class LogicalInsulinPSMILESEnv(_GymEnvBase):  # type: ignore[misc]
    """Logical-feature wrapper around InsulinPSMILESEnv.

    At each step:

    1. ``InsulinPSMILESEnv._build_initial_pool()`` proposes N candidate PSMILES.
    2. A GPy GP surrogate (trained on evaluated PSMILES ↦ interaction energy)
       predicts (mean, std_dev) for each candidate.
    3. A 5-feature logical observation vector is computed per candidate:
       ``[visited, closer, confident, similar, feasible]``.
    4. The policy selects one candidate (Discrete action).
    5. The selected PSMILES is evaluated by the base env.

    Parameters
    ----------
    n_proposals:
        Number of candidates proposed each step (action space size).
    regressor_type:
        ``"GPy"`` (default) or ``None`` (logical features without surrogate;
        uses only [visited, similar, feasible] like IBM's logical-no-regressor).
    regressor_train_interval:
        Retrain GP every N steps.
    All other kwargs are forwarded to InsulinPSMILESEnv.
    """

    metadata = {"render_modes": []}

    def __init__(
        self,
        seed_psmiles: str = "[*]OCC[*]",
        n_proposals: int = 20,
        max_steps: int = 100,
        n_targets: int = 5,
        rewards: Optional[Dict[str, float]] = None,
        target_energy_kj: float = DEFAULT_TARGET_ENERGY_KJ,
        md_steps: int = 5000,
        random_seed: int = 42,
        evaluate_candidates_fn: Optional[
            Callable[[List[Dict[str, Any]], int], Dict[str, Any]]
        ] = None,
        cache_path: Optional[str] = None,
        verbose_md: bool = False,
        regressor_type: Optional[str] = "GPy",
        regressor_train_interval: int = 5,
        evaluation_log: Optional[List[Dict[str, Any]]] = None,
        evaluation_log_phase: str = "unknown",
        rl_step_progress_log: Optional[List[Dict[str, Any]]] = None,
        initial_global_rl_step: int = 0,
        initial_best_interaction_energy_kj_mol: Optional[float] = None,
    ) -> None:
        _require_gym()
        super().__init__()

        self.env = InsulinPSMILESEnv(
            seed_psmiles=seed_psmiles,
            n_proposals=n_proposals,
            max_steps=max_steps,
            n_targets=n_targets,
            rewards=rewards,
            target_energy_kj=target_energy_kj,
            md_steps=md_steps,
            random_seed=random_seed,
            evaluate_candidates_fn=evaluate_candidates_fn,
            cache_path=cache_path,
            verbose_md=verbose_md,
            evaluation_log=evaluation_log,
            evaluation_log_phase=evaluation_log_phase,
            initial_best_interaction_energy_kj_mol=initial_best_interaction_energy_kj_mol,
        )

        self._rl_step_progress_log = rl_step_progress_log
        self._global_rl_step = int(initial_global_rl_step)

        self.n_proposals = n_proposals
        self.regressor_type = regressor_type
        self.regressor_train_interval = regressor_train_interval
        self._n_predicates = 3 if regressor_type is None else 5

        if _GYM_VERSION != "none":
            self.action_space = spaces.Discrete(n_proposals)
            self.observation_space = spaces.Box(
                low=0.0,
                high=1.0,
                shape=(n_proposals * self._n_predicates,),
                dtype=np.float32,
            )

        # Surrogate state
        self._regressor: Any = None
        self._evaluated_fps: List[np.ndarray] = []   # Morgan fingerprints
        self._evaluated_energies: List[float] = []   # interaction energies
        self._n_steps: int = 0

        # Track current proposals for the step
        self._proposed_psmiles: List[str] = []
        self._visited: Set[str] = set()
        # Target energy for "closer" feature normalization
        self._target_energy = target_energy_kj

    # ------------------------------------------------------------------
    # Gym API
    # ------------------------------------------------------------------
    def reset(
        self,
        *,
        seed: Optional[int] = None,
        options: Optional[Dict[str, Any]] = None,
    ) -> Tuple[np.ndarray, Dict[str, Any]]:
        obs, info = self.env.reset(seed=seed, options=options)
        self._n_steps = 0
        self._visited = set()
        self._evaluated_fps = []
        self._evaluated_energies = []
        self._regressor = None
        self._proposed_psmiles = list(self.env._current_pool)

        pred, std_dev = self._predict_pool(self._proposed_psmiles)
        logical_obs = self._logical_observation(self._proposed_psmiles, pred, std_dev)
        info["pool"] = self._proposed_psmiles
        return logical_obs, info

    def step(
        self, action: int
    ) -> Tuple[np.ndarray, float, bool, bool, Dict[str, Any]]:
        """Select action-th PSMILES, evaluate, return logical observation."""
        idx = int(action) % self.n_proposals
        selected = self._proposed_psmiles[idx]

        # Evaluate via base env
        obs, reward, terminated, truncated, info = self.env.step(idx)
        self._n_steps += 1
        self._visited.add(selected)

        # Update surrogate training data if we got a real energy
        md_row = info.get("md_row") or {}
        e_int = md_row.get("interaction_energy_kj_mol")
        if e_int is not None:
            fp = self._psmiles_to_fp(selected)
            if fp is not None:
                self._evaluated_fps.append(fp)
                self._evaluated_energies.append(float(e_int))

        # Retrain GP at interval
        if (
            self.regressor_type == "GPy"
            and len(self._evaluated_fps) >= 2
            and self._n_steps % self.regressor_train_interval == 0
        ):
            self._train_regressor()

        # Refresh pool for next step
        self._proposed_psmiles = list(self.env._current_pool)

        pred, std_dev = self._predict_pool(self._proposed_psmiles)
        logical_obs = self._logical_observation(self._proposed_psmiles, pred, std_dev)
        info["pool"] = self._proposed_psmiles

        if self._rl_step_progress_log is not None:
            self._global_rl_step += 1
            self._rl_step_progress_log.append(
                {
                    "phase": self.env.evaluation_log_phase,
                    "global_step": self._global_rl_step,
                    "running_best_interaction_energy_kj_mol": self.env._best_interaction_energy_kj_mol,
                }
            )

        return logical_obs, reward, terminated, truncated, info

    # ------------------------------------------------------------------
    # Logical observation
    # ------------------------------------------------------------------
    def _logical_observation(
        self,
        proposals: List[str],
        pred: List[float],
        std_dev: List[float],
    ) -> np.ndarray:
        """Build N × n_predicates logical feature matrix, returned flattened."""
        rows: List[List[float]] = []
        target = self._target_energy

        for i, ps in enumerate(proposals):
            visited_f = 1.0 if ps in self._visited else 0.0
            # "closer": how close is predicted energy to target (more negative = better)?
            output_target_diff = abs(pred[i] - target)
            # "similar": max Tanimoto to already-evaluated pool
            if self._evaluated_fps:
                evaluated_psmiles = [
                    ps_ for ps_ in self._visited
                    if self.env._cache.get(ps_)
                ]
                sim = _tanimoto_to_pool(ps, evaluated_psmiles) if evaluated_psmiles else 0.0
            else:
                sim = 0.0
            feasible_f = 1.0 if self._is_feasible(ps) else 0.0

            if self.regressor_type == "GPy":
                rows.append([visited_f, output_target_diff, std_dev[i], sim, feasible_f])
            else:
                rows.append([visited_f, sim, feasible_f])

        # Append zero sentinel (same as IBM) then MinMaxScale, then strip sentinel
        rows.append([0.0] * self._n_predicates)
        arr = np.array(rows, dtype=np.float32)
        col_min = arr.min(axis=0)
        col_max = arr.max(axis=0)
        col_range = col_max - col_min
        col_range[col_range == 0] = 1.0
        scaled = (arr - col_min) / col_range
        scaled = scaled[: self.n_proposals, :]  # drop sentinel

        # Invert: "closer" and "confident" should be high when GOOD
        if self.regressor_type == "GPy":
            scaled[:, 1] = 1.0 - scaled[:, 1]  # output_target_diff -> closer
            scaled[:, 2] = 1.0 - scaled[:, 2]  # std_dev -> confident
            scaled[:, 3] = 1.0 - scaled[:, 3]  # distance -> similar (already similarity)
        else:
            scaled[:, 1] = 1.0 - scaled[:, 1]  # distance -> similar

        return scaled.reshape(-1).astype(np.float32)

    def _is_feasible(self, psmiles: str) -> bool:
        try:
            from insulin_ai.material_mappings import prescreen_psmiles_for_md
            return bool(prescreen_psmiles_for_md(psmiles).get("ok"))
        except Exception:
            return False

    # ------------------------------------------------------------------
    # GP surrogate
    # ------------------------------------------------------------------
    def _psmiles_to_fp(self, psmiles: str) -> Optional[np.ndarray]:
        try:
            from rdkit import Chem

            from insulin_ai.material_mappings import morgan_fingerprint_bit_vect

            mol = Chem.MolFromSmiles(psmiles.replace("[*]", "[H]"))
            if mol is None:
                return None
            fp = morgan_fingerprint_bit_vect(mol, radius=2, n_bits=256)
            return np.array(fp, dtype=np.float32)
        except Exception:
            return None

    def _train_regressor(self) -> None:
        try:
            import GPy

            X = np.array(self._evaluated_fps)
            y = np.array(self._evaluated_energies).reshape(-1, 1)
            k = GPy.kern.RBF(X.shape[1], variance=1.0, lengthscale=0.1)
            m = GPy.models.GPRegression(X, y, kernel=k)
            m.optimize("bfgs")
            self._regressor = m
            logger.debug("GP surrogate retrained on %d points", len(self._evaluated_fps))
        except Exception as e:
            logger.warning("GP training failed: %s", e)

    def _predict_pool(
        self, proposals: List[str]
    ) -> Tuple[List[float], List[float]]:
        """Predict (mean energy, std_dev) for each candidate in proposals."""
        if self.regressor_type != "GPy" or self._regressor is None:
            return [0.0] * len(proposals), [0.0] * len(proposals)

        fps = []
        indices_with_fp = []
        for i, ps in enumerate(proposals):
            fp = self._psmiles_to_fp(ps)
            if fp is not None:
                fps.append(fp)
                indices_with_fp.append(i)

        pred_out = [0.0] * len(proposals)
        std_out = [0.0] * len(proposals)

        if not fps:
            return pred_out, std_out

        try:
            X_pred = np.array(fps)
            p_mean, p_var = self._regressor.predict(X_pred)
            for idx, i in enumerate(indices_with_fp):
                pred_out[i] = float(p_mean[idx, 0])
                std_out[i] = float(np.sqrt(max(float(p_var[idx, 0]), 0.0)))
        except Exception as e:
            logger.warning("GP prediction failed: %s", e)

        return pred_out, std_out


# ---------------------------------------------------------------------------
# Gymnasium registration (only if gymnasium is available)
# ---------------------------------------------------------------------------
def _register_envs() -> None:
    try:
        import gymnasium as gym

        gym.register(
            id="insulin-ai/InsulinPSMILES-v1",
            entry_point="benchmarks.ibm_insulin_env:InsulinPSMILESEnv",
        )
        gym.register(
            id="insulin-ai/logical-InsulinPSMILES-v1",
            entry_point="benchmarks.ibm_insulin_env:LogicalInsulinPSMILESEnv",
        )
    except Exception:
        pass


_register_envs()
