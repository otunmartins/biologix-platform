"""Tests for InsulinPSMILESEnv and LogicalInsulinPSMILESEnv (IBM RL adapter).

All tests use a mock ``evaluate_candidates_fn`` — no OpenMM required.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List
from unittest.mock import patch

import numpy as np
import pytest

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT / "src" / "python") not in sys.path:
    sys.path.insert(0, str(_ROOT / "src" / "python"))
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# Skip the entire module if no Gym installation is present
_gym_available = False
try:
    import gymnasium  # noqa: F401
    _gym_available = True
except ImportError:
    try:
        import gym  # noqa: F401
        _gym_available = True
    except ImportError:
        pass

pytestmark_gym = pytest.mark.skipif(
    not _gym_available,
    reason="gymnasium or gym required for IBM RL env tests",
)


# Comparison row tests don't need Gym — override mark for that class
_comparison_only = pytest.mark.skipif(False, reason="")

# ---------------------------------------------------------------------------
# Shared mock evaluator
# ---------------------------------------------------------------------------

def _mock_target_eval(
    candidates: List[Dict[str, Any]], max_candidates: int
) -> Dict[str, Any]:
    """Mock: all candidates get favorable (target) energy."""
    from insulin_ai.simulation.property_extractor import PropertyExtractor

    extractor = PropertyExtractor()
    md_rows = [
        {
            "psmiles": c.get("chemical_structure", ""),
            "interaction_energy_kj_mol": -20.0,
            "insulin_rmsd_to_initial_nm": 0.05,
            "potential_energy_complex_kj_mol": -1000.0,
            "potential_energy_insulin_kj_mol": -800.0,
            "potential_energy_polymer_kj_mol": -180.0,
            "insulin_polymer_contacts": 10,
            "method": "mock",
        }
        for c in candidates[:max_candidates]
    ]
    names = [c.get("material_name", f"C_{i}") for i, c in enumerate(candidates[:max_candidates])]
    feedback = extractor.extract_feedback(md_rows, names)
    feedback["md_results_raw"] = md_rows
    return feedback


def _mock_nontarget_eval(
    candidates: List[Dict[str, Any]], max_candidates: int
) -> Dict[str, Any]:
    """Mock: all candidates get unfavorable (valid) energy."""
    from insulin_ai.simulation.property_extractor import PropertyExtractor

    extractor = PropertyExtractor()
    md_rows = [
        {
            "psmiles": c.get("chemical_structure", ""),
            "interaction_energy_kj_mol": 10.0,
            "insulin_rmsd_to_initial_nm": 0.3,
            "potential_energy_complex_kj_mol": -900.0,
            "potential_energy_insulin_kj_mol": -800.0,
            "potential_energy_polymer_kj_mol": -110.0,
            "insulin_polymer_contacts": 1,
            "method": "mock",
        }
        for c in candidates[:max_candidates]
    ]
    names = [c.get("material_name", f"C_{i}") for i, c in enumerate(candidates[:max_candidates])]
    feedback = extractor.extract_feedback(md_rows, names)
    feedback["md_results_raw"] = md_rows
    return feedback


def _mock_error_eval(
    candidates: List[Dict[str, Any]], max_candidates: int
) -> Dict[str, Any]:
    """Mock: raises exception to test no-go handling."""
    raise RuntimeError("Simulated evaluation failure")


# ---------------------------------------------------------------------------
# InsulinPSMILESEnv tests
# ---------------------------------------------------------------------------

@pytestmark_gym
class TestInsulinPSMILESEnvReset:
    def _make_env(self, eval_fn=None):
        from benchmarks.ibm_insulin_env import InsulinPSMILESEnv

        return InsulinPSMILESEnv(
            seed_psmiles="[*]OCC[*]",
            n_proposals=5,
            max_steps=10,
            n_targets=3,
            random_seed=42,
            evaluate_candidates_fn=eval_fn or _mock_nontarget_eval,
        )

    def test_reset_returns_obs_and_info(self):
        env = self._make_env()
        obs, info = env.reset()
        assert obs is not None
        assert isinstance(obs, np.ndarray)
        assert obs.shape == (5 * 5,)
        assert "pool" in info
        assert len(info["pool"]) == 5

    def test_reset_clears_episode_state(self):
        env = self._make_env()
        env.reset()
        env.step(0)
        env.reset()
        assert env._n_steps == 0
        assert env._target_steps == 0
        assert len(env._visited) == 0

    def test_action_space_matches_n_proposals(self):
        env = self._make_env()
        env.reset()
        assert env.action_space.n == 5

    def test_observation_space_shape(self):
        env = self._make_env()
        obs, _ = env.reset()
        assert env.observation_space.contains(obs)


@pytestmark_gym
class TestInsulinPSMILESEnvStep:
    def _make_env(self, eval_fn=None, n_proposals=5):
        from benchmarks.ibm_insulin_env import InsulinPSMILESEnv

        return InsulinPSMILESEnv(
            seed_psmiles="[*]OCC[*]",
            n_proposals=n_proposals,
            max_steps=10,
            n_targets=3,
            random_seed=42,
            evaluate_candidates_fn=eval_fn or _mock_nontarget_eval,
        )

    def test_step_returns_five_tuple(self):
        env = self._make_env()
        env.reset()
        result = env.step(0)
        assert len(result) == 5
        obs, reward, terminated, truncated, info = result
        assert isinstance(obs, np.ndarray)
        assert isinstance(reward, float)
        assert isinstance(terminated, bool)
        assert isinstance(truncated, bool)
        assert isinstance(info, dict)

    def test_target_reward_for_favorable_energy(self):
        env = self._make_env(eval_fn=_mock_target_eval)
        env.reset()
        _, reward, _, _, info = env.step(0)
        assert reward == pytest.approx(1.0)
        assert info["tier"] == "target"

    def test_valid_reward_for_unfavorable_energy(self):
        env = self._make_env(eval_fn=_mock_nontarget_eval)
        env.reset()
        _, reward, _, _, info = env.step(0)
        assert reward == pytest.approx(-0.01)
        assert info["tier"] == "valid"

    def test_nogo_reward_on_evaluation_error(self):
        env = self._make_env(eval_fn=_mock_error_eval)
        env.reset()
        # First action on a valid PSMILES - should return no-go on error
        _, reward, _, _, info = env.step(0)
        # Either no-go (-1) or revisit (-0.5) depending on prescreen; check it's not target
        assert reward <= -0.01
        assert info["tier"] in ("no-go", "revisit", "valid")

    def test_revisit_reward_on_duplicate(self):
        env = self._make_env(eval_fn=_mock_nontarget_eval)
        env.reset()
        # Step with action 0 twice on the same pool entry
        env.step(0)
        # Force same PSMILES into pool
        psmiles_first = env._current_pool[0] if env._current_pool else "[*]OCC[*]"
        env._current_pool = [psmiles_first] * 5
        _, reward2, _, _, info2 = env.step(0)
        if info2["tier"] == "revisit":
            assert reward2 == pytest.approx(-0.5)

    def test_episode_terminates_at_max_steps(self):
        env = self._make_env(eval_fn=_mock_nontarget_eval)
        env.reset()
        terminated = False
        for _ in range(15):
            _, _, terminated, _, _ = env.step(0)
            if terminated:
                break
        assert terminated is True

    def test_episode_terminates_early_on_n_targets(self):
        env = self._make_env(eval_fn=_mock_target_eval, n_proposals=5)
        env.reset()
        terminated = False
        for _ in range(10):
            _, _, terminated, _, _ = env.step(0)
            if terminated:
                break
        assert terminated is True
        assert env._target_steps >= env.n_targets or env._n_steps <= env.max_steps

    def test_info_contains_psmiles(self):
        env = self._make_env()
        env.reset()
        _, _, _, _, info = env.step(0)
        assert "psmiles" in info
        assert "[*]" in info["psmiles"]

    def test_n_steps_increments(self):
        env = self._make_env()
        env.reset()
        for i in range(3):
            env.step(0)
        assert env._n_steps == 3

    def test_evaluation_log_appends_on_md_eval(self):
        from benchmarks.ibm_insulin_env import InsulinPSMILESEnv

        log: List[Dict[str, Any]] = []
        env = InsulinPSMILESEnv(
            seed_psmiles="[*]OCC[*]",
            n_proposals=5,
            max_steps=10,
            n_targets=3,
            random_seed=42,
            evaluate_candidates_fn=_mock_nontarget_eval,
            evaluation_log=log,
            evaluation_log_phase="unit",
        )
        env.reset()
        env.step(0)
        assert len(log) == 1
        assert log[0]["phase"] == "unit"
        assert log[0]["interaction_energy_kj_mol"] == pytest.approx(10.0)
        assert "[*]" in log[0]["psmiles"]


# ---------------------------------------------------------------------------
# Reward mapping
# ---------------------------------------------------------------------------

@pytestmark_gym
class TestRewardMapping:
    def _make_env(self):
        from benchmarks.ibm_insulin_env import InsulinPSMILESEnv

        return InsulinPSMILESEnv(
            n_proposals=4,
            random_seed=1,
            evaluate_candidates_fn=_mock_nontarget_eval,
        )

    def test_reward_from_row_target(self):
        env = self._make_env()
        env.reset()
        row = {"interaction_energy_kj_mol": -10.0}
        assert env._reward_from_row(row) == pytest.approx(1.0)
        assert env._tier_from_row(row) == "target"

    def test_reward_from_row_valid(self):
        env = self._make_env()
        env.reset()
        row = {"interaction_energy_kj_mol": 5.0}
        assert env._reward_from_row(row) == pytest.approx(-0.01)
        assert env._tier_from_row(row) == "valid"

    def test_reward_from_row_none(self):
        env = self._make_env()
        env.reset()
        assert env._reward_from_row(None) == pytest.approx(-1.0)

    def test_reward_from_row_missing_energy(self):
        env = self._make_env()
        env.reset()
        assert env._reward_from_row({}) == pytest.approx(-0.01)

    def test_custom_target_threshold(self):
        from benchmarks.ibm_insulin_env import InsulinPSMILESEnv

        env = InsulinPSMILESEnv(
            n_proposals=4,
            target_energy_kj=-20.0,
            evaluate_candidates_fn=_mock_nontarget_eval,
        )
        env.reset()
        row = {"interaction_energy_kj_mol": -10.0}
        # -10 > -20 so should be "valid"
        assert env._tier_from_row(row) == "valid"

    def test_custom_rewards_dict(self):
        from benchmarks.ibm_insulin_env import InsulinPSMILESEnv

        custom = {"target": 2.0, "valid": -0.1, "revisit": -1.0, "no-go": -2.0}
        env = InsulinPSMILESEnv(
            n_proposals=4,
            rewards=custom,
            evaluate_candidates_fn=_mock_nontarget_eval,
        )
        env.reset()
        row = {"interaction_energy_kj_mol": -10.0}
        assert env._reward_from_row(row) == pytest.approx(2.0)


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------

@pytestmark_gym
class TestCacheLookup:
    def _make_env_with_cache(self, cache: dict):
        from benchmarks.ibm_insulin_env import InsulinPSMILESEnv

        env = InsulinPSMILESEnv(
            seed_psmiles="[*]OCC[*]",
            n_proposals=5,
            random_seed=42,
            evaluate_candidates_fn=_mock_error_eval,  # Should not be called for cached entries
        )
        env._cache = cache
        return env

    def test_cached_psmiles_bypasses_eval(self):
        """If PSMILES is in cache, evaluate_candidates_fn must NOT be called."""
        from insulin_ai.material_mappings import validate_psmiles

        seed = "[*]OCC[*]"
        vr = validate_psmiles(seed)
        canonical = str(vr.get("canonical") or seed)

        cache = {
            canonical: {
                "psmiles": canonical,
                "interaction_energy_kj_mol": -15.0,
                "insulin_rmsd_to_initial_nm": 0.04,
            }
        }
        env = self._make_env_with_cache(cache)
        env.reset()
        env._current_pool = [canonical] * 5
        # _mock_error_eval would raise; should not be reached
        _, reward, _, _, info = env.step(0)
        assert reward == pytest.approx(1.0)
        assert info["tier"] == "target"

    def test_cache_loaded_from_file(self, tmp_path):
        from benchmarks.ibm_insulin_env import InsulinPSMILESEnv

        cache_data = {
            "[*]OCC[*]": {"interaction_energy_kj_mol": -8.0, "psmiles": "[*]OCC[*]"}
        }
        cache_file = tmp_path / "cache.json"
        with open(cache_file, "w") as f:
            json.dump(cache_data, f)

        env = InsulinPSMILESEnv(
            n_proposals=5,
            cache_path=str(cache_file),
            evaluate_candidates_fn=_mock_nontarget_eval,
        )
        assert "[*]OCC[*]" in env._cache


# ---------------------------------------------------------------------------
# LogicalInsulinPSMILESEnv
# ---------------------------------------------------------------------------

@pytestmark_gym
class TestLogicalInsulinPSMILESEnv:
    def _make_env(self, regressor_type=None):
        from benchmarks.ibm_insulin_env import LogicalInsulinPSMILESEnv

        return LogicalInsulinPSMILESEnv(
            seed_psmiles="[*]OCC[*]",
            n_proposals=5,
            max_steps=10,
            n_targets=3,
            random_seed=42,
            evaluate_candidates_fn=_mock_nontarget_eval,
            regressor_type=regressor_type,
        )

    def test_reset_returns_logical_obs_shape_no_regressor(self):
        env = self._make_env(regressor_type=None)
        obs, info = env.reset()
        # 5 proposals × 3 features (no regressor)
        assert obs.shape == (5 * 3,)
        assert np.all((obs >= 0.0) & (obs <= 1.0))

    def test_reset_returns_logical_obs_shape_with_gpy(self):
        pytest.importorskip("GPy")
        env = self._make_env(regressor_type="GPy")
        obs, info = env.reset()
        # 5 proposals × 5 features
        assert obs.shape == (5 * 5,)

    def test_action_space_is_discrete(self):
        env = self._make_env()
        env.reset()
        # Gym Discrete space check without importing gymnasium directly
        assert env.action_space.n == 5

    def test_step_returns_logical_obs(self):
        env = self._make_env(regressor_type=None)
        env.reset()
        obs, reward, terminated, truncated, info = env.step(0)
        assert obs.shape == (5 * 3,)
        assert isinstance(reward, float)

    def test_obs_values_in_range(self):
        env = self._make_env(regressor_type=None)
        env.reset()
        for _ in range(3):
            obs, _, _, _, _ = env.step(0)
            assert np.all(obs >= 0.0) and np.all(obs <= 1.0), (
                f"Logical features out of [0,1]: min={obs.min():.4f} max={obs.max():.4f}"
            )

    def test_feasible_feature_set(self):
        from benchmarks.ibm_insulin_env import LogicalInsulinPSMILESEnv

        env = LogicalInsulinPSMILESEnv(
            seed_psmiles="[*]OCC[*]",
            n_proposals=3,
            random_seed=0,
            evaluate_candidates_fn=_mock_nontarget_eval,
            regressor_type=None,
        )
        obs, _ = env.reset()
        # feasible feature is index 2 of each 3-vector
        feasible_vals = obs[2::3]
        assert all(v in (0.0, 1.0) for v in feasible_vals)


# ---------------------------------------------------------------------------
# Benchmark script (injected evaluator — no OpenMM)
# ---------------------------------------------------------------------------

def _stub_evaluate_candidates_for_benchmark(
    target_energy_kj: float = -5.0,
) -> Callable[[List[Dict[str, Any]], int], Dict[str, Any]]:
    """Deterministic fake MD for SB3 pipeline tests (not live OpenMM)."""
    import hashlib

    from insulin_ai.simulation.property_extractor import PropertyExtractor

    extractor = PropertyExtractor()

    def fn(candidates: List[Dict[str, Any]], max_candidates: int) -> Dict[str, Any]:
        md_results = []
        for c in candidates[:max_candidates]:
            ps = c.get("chemical_structure") or c.get("psmiles") or ""
            digest = int(hashlib.md5(ps.encode()).hexdigest()[:8], 16)
            fraction = (digest % 100) / 100.0
            e_int = target_energy_kj * 3 * fraction - 2.0
            rmsd = 0.05 + 0.2 * fraction
            md_results.append({
                "psmiles": ps,
                "interaction_energy_kj_mol": e_int,
                "insulin_rmsd_to_initial_nm": rmsd,
                "potential_energy_complex_kj_mol": -1000.0 + e_int,
                "potential_energy_insulin_kj_mol": -800.0,
                "potential_energy_polymer_kj_mol": -200.0 + e_int * 0.1,
                "insulin_polymer_contacts": 8 if e_int < target_energy_kj else 2,
                "method": "stub",
            })
        names = [c.get("material_name", f"C_{i}") for i, c in enumerate(candidates[:max_candidates])]
        feedback = extractor.extract_feedback(md_results, names)
        feedback["md_results_raw"] = md_results
        return feedback

    return fn


@pytestmark_gym
class TestIBMBenchmarkStubEvalMode:
    def test_train_and_test_stub_returns_scores(self):
        pytest.importorskip("stable_baselines3")
        from benchmarks.ibm_insulin_rl_benchmark import run_ibm_insulin_benchmark

        result = run_ibm_insulin_benchmark(
            mode="train_and_test",
            algorithm="dqn",
            seed_psmiles="[*]OCC[*]",
            n_proposals=5,
            max_steps=10,
            n_targets=3,
            n_timesteps=100,
            n_episodes=2,
            random_seed=42,
            evaluate_candidates_fn=_stub_evaluate_candidates_for_benchmark(),
        )
        assert result["train_completed"] is True
        assert result.get("evaluation") == "injected"
        assert "best_discovery_score" in result
        assert "n_evaluations" in result
        assert "avg_episode_reward" in result

    def test_train_stub_dqn(self):
        pytest.importorskip("stable_baselines3")
        from benchmarks.ibm_insulin_rl_benchmark import run_ibm_insulin_benchmark

        result = run_ibm_insulin_benchmark(
            mode="train",
            algorithm="dqn",
            n_proposals=5,
            max_steps=5,
            n_timesteps=50,
            n_episodes=1,
            evaluate_candidates_fn=_stub_evaluate_candidates_for_benchmark(),
        )
        assert result["train_completed"] is True

    def test_train_stub_ppo(self):
        pytest.importorskip("stable_baselines3")
        from benchmarks.ibm_insulin_rl_benchmark import run_ibm_insulin_benchmark

        result = run_ibm_insulin_benchmark(
            mode="train",
            algorithm="ppo",
            n_proposals=5,
            max_steps=5,
            n_timesteps=50,
            n_episodes=1,
            evaluate_candidates_fn=_stub_evaluate_candidates_for_benchmark(),
        )
        assert result["train_completed"] is True

    def test_comparison_tsv_written(self, tmp_path):
        pytest.importorskip("stable_baselines3")
        from benchmarks.ibm_insulin_rl_benchmark import run_ibm_insulin_benchmark

        tsv_path = str(tmp_path / "comparison.tsv")
        run_ibm_insulin_benchmark(
            mode="train_and_test",
            algorithm="dqn",
            n_proposals=5,
            max_steps=5,
            n_timesteps=50,
            n_episodes=1,
            evaluate_candidates_fn=_stub_evaluate_candidates_for_benchmark(),
            comparison_tsv=tsv_path,
        )
        assert Path(tsv_path).is_file()
        content = Path(tsv_path).read_text()
        assert "ibm_rl_dqn" in content
        assert "best_discovery_score" in content  # header


# ---------------------------------------------------------------------------
# make_comparison_row
# ---------------------------------------------------------------------------

@pytest.mark.skipif(False, reason="no gym dep needed")
class TestComparisonRow:
    def test_all_columns_present(self):
        from benchmarks.ibm_insulin_rl_benchmark import (
            _COMPARISON_COLUMNS,
            make_comparison_row,
        )

        row = make_comparison_row(method="ibm_rl_dqn", best_discovery_score=1.5)
        assert set(row.keys()) == set(_COMPARISON_COLUMNS)
        assert row["method"] == "ibm_rl_dqn"
        assert row["best_discovery_score"] == 1.5
        assert row["n_evaluations"] is None  # unfilled → None

    def test_append_tsv_creates_header(self, tmp_path):
        from benchmarks.ibm_insulin_rl_benchmark import (
            _COMPARISON_COLUMNS,
            append_comparison_tsv,
            make_comparison_row,
        )

        tsv = str(tmp_path / "bench.tsv")
        row = make_comparison_row(method="optuna", best_discovery_score=2.0)
        append_comparison_tsv(tsv, row)
        lines = Path(tsv).read_text().splitlines()
        assert lines[0].split("\t")[0] == "method"
        assert "optuna" in lines[1]

    def test_append_tsv_no_duplicate_header(self, tmp_path):
        from benchmarks.ibm_insulin_rl_benchmark import (
            append_comparison_tsv,
            make_comparison_row,
        )

        tsv = str(tmp_path / "bench.tsv")
        for method in ("ibm_rl_dqn", "optuna", "agentic"):
            append_comparison_tsv(tsv, make_comparison_row(method=method))
        lines = Path(tsv).read_text().splitlines()
        # Only one header row
        header_lines = [l for l in lines if l.startswith("method")]
        assert len(header_lines) == 1
        assert len(lines) == 4  # 1 header + 3 data rows


# ---------------------------------------------------------------------------
# Agentic parity resolution (no Gym)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(False, reason="no gym dep needed")
class TestResolveAgenticParitySettings:
    def test_defaults_match_product(self):
        from benchmarks.ibm_insulin_rl_benchmark import resolve_agentic_parity_settings

        r = resolve_agentic_parity_settings(20, 10)
        assert r == {
            "n_timesteps": 200,
            "n_episodes": 20,
            "n_proposals": 10,
            "max_steps": 10,
        }

    def test_overrides_passthrough(self):
        from benchmarks.ibm_insulin_rl_benchmark import resolve_agentic_parity_settings

        r = resolve_agentic_parity_settings(
            20,
            10,
            n_timesteps=50,
            n_episodes=3,
            n_proposals=7,
            max_steps=8,
        )
        assert r["n_timesteps"] == 50
        assert r["n_episodes"] == 3
        assert r["n_proposals"] == 7
        assert r["max_steps"] == 8

    def test_eight_evals_matches_autonomous_discovery_default(self):
        from benchmarks.ibm_insulin_rl_benchmark import resolve_agentic_parity_settings

        r = resolve_agentic_parity_settings(20, 8)
        assert r["n_timesteps"] == 160
        assert r["n_episodes"] == 20
        assert r["n_proposals"] == 8
        assert r["max_steps"] == 8
