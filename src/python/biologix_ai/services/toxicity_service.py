"""ToxicityService: SMARTS screening + ADMET-AI for residual monomer risk.

ADMET predictions are on small-molecule SMILES (monomers or realistic
residual fragments), never on full polymer graphs. All ADMET models are
trained on drug-like small molecules — predictions for monomers are
informative but not a substitute for regulatory studies.
"""

from __future__ import annotations

import importlib
import importlib.util
import json
import logging
import os
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

TOXIC_SMARTS = {
    "acrylamide": "[CH2]=[CH][C](=O)[NH2]",
    "epoxide": "[OX2r3]1[CX4r3][CX4r3]1",
    "aldehyde": "[CX3H1](=O)[#6]",
    "michael_acceptor": "[CX3]=[CX3][CX3](=O)",
    "aziridine": "[NX3r3]1[CX4r3][CX4r3]1",
    "isocyanate": "[NX2]=[CX2]=[OX1]",
    "acid_halide": "[CX3](=O)[F,Cl,Br,I]",
    "nitro_aromatic": "[$(c1ccccc1[N+](=O)[O-])]",
    "sulfonyl_halide": "[SX4](=O)(=O)[F,Cl,Br,I]",
    "peroxide": "[OX2][OX2]",
    "hydrazine": "[NX3][NX3]",
    "vinyl_halide": "[CX3]=[CX3][F,Cl,Br,I]",
}

ADMET_THRESHOLDS = {
    "hERG": {"key": "hERG", "threshold": 0.5, "direction": "above_is_bad"},
    "hepatotoxicity": {"key": "HepTox", "threshold": 0.5, "direction": "above_is_bad"},
    "AMES": {"key": "AMES", "threshold": 0.5, "direction": "above_is_bad"},
}
# TDC LD50_Zhu is -log10(LD50 [mol/kg]), not mg/kg. 500 mg/kg is the flag after conversion.
LD50_ZHU_MG_PER_KG_THRESHOLD = 500.0

_DEFAULT_ADMET_PYTHON = Path("/opt/conda/envs/biologix-admet/bin/python")
_REPO_ROOT = Path(__file__).resolve().parents[4]
_DEFAULT_ADMET_RUNNER = _REPO_ROOT / "scripts" / "run_admet_isolated.py"


class SMARTSHit(BaseModel):
    pattern_name: str
    smarts: str
    smiles: str


class ADMETProfile(BaseModel):
    smiles: str
    predictions: Dict[str, float] = Field(default_factory=dict)
    flags: List[str] = Field(default_factory=list)
    available: bool = True


class ToxicityResult(BaseModel):
    smiles: str
    smarts_hits: List[SMARTSHit] = Field(default_factory=list)
    admet: Optional[ADMETProfile] = None
    safe: bool = True
    warnings: List[str] = Field(default_factory=list)


def _is_admet_available() -> bool:
    return _admet_python() is not None or importlib.util.find_spec("admet_ai") is not None


def _admet_python() -> Optional[Path]:
    raw_path = os.environ.get("BIOLOGIX_ADMET_PYTHON", "").strip()
    candidate = Path(raw_path) if raw_path else _DEFAULT_ADMET_PYTHON
    if candidate.is_file() and os.access(candidate, os.X_OK):
        return candidate
    return None


def _ld50_zhu_mg_per_kg(log_inv_mol_per_kg: float, smiles: str) -> Optional[float]:
    """Convert TDC LD50_Zhu, -log10(mol/kg), into mg/kg using the molecule's mass."""
    try:
        from rdkit import Chem
        from rdkit.Chem import Descriptors
    except ImportError:
        return None
    molecule = Chem.MolFromSmiles(smiles)
    if molecule is None:
        return None
    mol_per_kg = 10 ** (-float(log_inv_mol_per_kg))
    return mol_per_kg * float(Descriptors.MolWt(molecule)) * 1000.0


def _profile_from_predictions(smiles: str, predictions: Dict[str, Any]) -> ADMETProfile:
    pred_dict = {
        key: float(value)
        for key, value in predictions.items()
        if isinstance(value, (int, float))
    }
    flags: List[str] = []
    for label, cfg in ADMET_THRESHOLDS.items():
        value = pred_dict.get(cfg["key"])
        if value is None or cfg["threshold"] is None:
            continue
        if cfg["direction"] == "above_is_bad" and value > cfg["threshold"]:
            flags.append(f"{label}={value:.3f} (threshold {cfg['threshold']})")
        elif cfg["direction"] == "lower_is_bad" and value < cfg["threshold"]:
            flags.append(f"{label}={value:.3f} (threshold {cfg['threshold']})")
    ld50_log = pred_dict.get("LD50_Zhu")
    if ld50_log is not None:
        ld50_mg = _ld50_zhu_mg_per_kg(ld50_log, smiles)
        if ld50_mg is not None and ld50_mg < LD50_ZHU_MG_PER_KG_THRESHOLD:
            flags.append(
                f"LD50_Zhu={ld50_log:.3f} -log10(mol/kg) = {ld50_mg:.1f} mg/kg "
                f"(threshold {LD50_ZHU_MG_PER_KG_THRESHOLD:.0f} mg/kg)"
            )
    return ADMETProfile(smiles=smiles, predictions=pred_dict, flags=flags)


def _direct_admet_predictions(smiles_list: List[str]) -> List[ADMETProfile]:
    admet_module = importlib.import_module("admet_ai")
    model = admet_module.ADMETModel()
    raw_predictions = model.predict(
        smiles=smiles_list[0] if len(smiles_list) == 1 else smiles_list
    )
    if isinstance(raw_predictions, dict):
        return [_profile_from_predictions(smiles_list[0], raw_predictions)]
    if hasattr(raw_predictions, "iterrows"):
        rows = {
            str(index): row.to_dict()
            for index, row in raw_predictions.iterrows()
        }
        return [
            _profile_from_predictions(smiles, rows.get(smiles, {}))
            for smiles in smiles_list
        ]
    raise TypeError(f"Unsupported ADMET prediction type: {type(raw_predictions).__name__}")


def _isolated_admet_predictions(
    admet_python: Path,
    smiles_list: List[str],
) -> List[ADMETProfile]:
    runner = Path(
        os.environ.get("BIOLOGIX_ADMET_RUNNER", str(_DEFAULT_ADMET_RUNNER))
    )
    timeout = float(os.environ.get("BIOLOGIX_ADMET_SUBPROCESS_TIMEOUT_S", "300"))
    child_environment = dict(os.environ)
    child_environment["PATH"] = (
        f"{admet_python.parent}:{child_environment.get('PATH', '')}"
    )
    child_environment["LD_LIBRARY_PATH"] = str(admet_python.parent.parent / "lib")
    child_environment.pop("PYTHONPATH", None)
    completed = subprocess.run(
        [str(admet_python), str(runner)],
        input=json.dumps({"smiles": smiles_list}),
        text=True,
        capture_output=True,
        check=True,
        timeout=timeout,
        env=child_environment,
        cwd=str(_REPO_ROOT),
    )
    payload = json.loads(completed.stdout)
    rows = payload.get("predictions")
    if not isinstance(rows, list):
        raise ValueError("ADMET subprocess returned no predictions list")
    by_smiles = {
        str(row.get("smiles")): row.get("predictions", {})
        for row in rows
        if isinstance(row, dict)
    }
    return [
        _profile_from_predictions(smiles, by_smiles.get(smiles, {}))
        for smiles in smiles_list
    ]


def _run_admet_batch(smiles_list: List[str]) -> List[ADMETProfile]:
    if not smiles_list:
        return []
    try:
        admet_python = _admet_python()
        if admet_python is not None:
            return _isolated_admet_predictions(admet_python, smiles_list)
        if importlib.util.find_spec("admet_ai") is not None:
            return _direct_admet_predictions(smiles_list)
    except Exception as exc:
        logger.error("ADMET-AI failed for %s: %s", ",".join(smiles_list), exc)
    return [
        ADMETProfile(smiles=smiles, available=False)
        for smiles in smiles_list
    ]


def _run_smarts_screen(smiles: str) -> List[SMARTSHit]:
    """Screen a SMILES against the curated SMARTS toxicity library."""
    hits: List[SMARTSHit] = []
    try:
        from rdkit import Chem
        mol = Chem.MolFromSmiles(smiles)
        if mol is None:
            return hits
        for name, smarts in TOXIC_SMARTS.items():
            pattern = Chem.MolFromSmarts(smarts)
            if pattern and mol.HasSubstructMatch(pattern):
                hits.append(SMARTSHit(pattern_name=name, smarts=smarts, smiles=smiles))
    except ImportError:
        logger.debug("RDKit not available; skipping SMARTS screening")
    return hits


def _run_admet(smiles: str) -> Optional[ADMETProfile]:
    """Run ADMET-AI predictions on a single SMILES."""
    return _run_admet_batch([smiles])[0]


def _combine_toxicity_result(
    smiles: str,
    smarts_hits: List[SMARTSHit],
    admet: ADMETProfile,
) -> ToxicityResult:
    warnings: List[str] = [
        f"SMARTS alert: {hit.pattern_name}" for hit in smarts_hits
    ]
    warnings.extend(admet.flags)
    if not admet.available:
        warnings.append("ADMET-AI unavailable; result contains SMARTS screening only.")
    return ToxicityResult(
        smiles=smiles,
        smarts_hits=smarts_hits,
        admet=admet,
        safe=not smarts_hits and not admet.flags,
        warnings=warnings,
    )


def _smiles_parses(smiles: str) -> bool:
    """Return whether RDKit accepts the SMILES. Assume true if RDKit is absent."""
    try:
        from rdkit import Chem
    except ImportError:
        return True
    return Chem.MolFromSmiles(smiles) is not None


def screen_monomer(smiles: str) -> ToxicityResult:
    """Full toxicity screen on a single monomer SMILES: SMARTS + ADMET."""
    if not _smiles_parses(smiles):
        return ToxicityResult(
            smiles=smiles,
            safe=False,
            admet=ADMETProfile(smiles=smiles, available=False),
            warnings=["SMILES did not parse. ADMET was not run."],
        )
    smarts_hits = _run_smarts_screen(smiles)
    admet = _run_admet(smiles)
    return _combine_toxicity_result(smiles, smarts_hits, admet)


def screen_monomers_batch(smiles_list: List[str]) -> List[ToxicityResult]:
    """Screen multiple monomers."""
    admet_profiles = _run_admet_batch(smiles_list)
    return [
        _combine_toxicity_result(smiles, _run_smarts_screen(smiles), admet)
        for smiles, admet in zip(smiles_list, admet_profiles)
    ]
