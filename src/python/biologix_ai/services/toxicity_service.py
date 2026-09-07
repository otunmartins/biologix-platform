"""ToxicityService: SMARTS screening + ADMET-AI for residual monomer risk.

ADMET predictions are on small-molecule SMILES (monomers or realistic
residual fragments), never on full polymer graphs. All ADMET models are
trained on drug-like small molecules — predictions for monomers are
informative but not a substitute for regulatory studies.
"""

from __future__ import annotations

import functools
import logging
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
    "DILI": {"key": "DILI", "threshold": 0.5, "direction": "above_is_bad"},
    "AMES": {"key": "AMES", "threshold": 0.5, "direction": "above_is_bad"},
}


class SMARTSHit(BaseModel):
    pattern_name: str
    smarts: str
    smiles: str


class ADMETProfile(BaseModel):
    smiles: str
    predictions: Dict[str, float] = Field(default_factory=dict)
    flags: List[str] = Field(default_factory=list)
    available: bool = True
    in_domain: bool = True
    domain_note: str = ""


class ToxicityResult(BaseModel):
    smiles: str
    smarts_hits: List[SMARTSHit] = Field(default_factory=list)
    admet: Optional[ADMETProfile] = None
    safe: bool = True
    parsed: bool = True
    warnings: List[str] = Field(default_factory=list)


def _ld50_log_inverse_molar_to_mg_kg(smiles: str, value: float) -> Optional[float]:
    """Convert log10(1/(mol/kg)) LD50_Zhu output to mg/kg."""
    try:
        from rdkit import Chem
        from rdkit.Chem import Descriptors

        mol = Chem.MolFromSmiles(smiles)
        if mol is None:
            return None
        return 1000.0 * Descriptors.MolWt(mol) * (10.0 ** (-float(value)))
    except (ImportError, TypeError, ValueError, OverflowError):
        return None


def _is_admet_available() -> bool:
    try:
        from admet_ai import ADMETModel  # noqa: F401
        return True
    except ImportError:
        return False


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


# ADMET-AI models are trained on drug-like small molecules. Applied to trivial
# reagents they return confident nonsense - a real run flagged water as
# AMES-positive with an LD50 of 309 mg/kg. Predictions below this size are
# reported but not turned into safety findings.
ADMET_MIN_HEAVY_ATOMS = 4


def _heavy_atom_count(smiles: str) -> Optional[int]:
    try:
        from rdkit import Chem
    except ImportError:
        return None
    mol = Chem.MolFromSmiles(smiles)
    return mol.GetNumHeavyAtoms() if mol is not None else None


@functools.lru_cache(maxsize=1)
def _get_admet_model():
    """Load the ADMET-AI ensemble once per worker process, not once per molecule.

    Construction loads ten chemprop checkpoints plus the DrugBank reference set; on a
    real run that cost was paid again for every monomer screened, and again for every
    experiment. The model is stateless across calls (default DrugBank path, no ATC
    filter), so one cached instance is safe to reuse for the life of the process.
    """
    from admet_ai import ADMETModel

    return ADMETModel()


def _run_admet(smiles: str) -> Optional[ADMETProfile]:
    """Run ADMET-AI predictions on a single SMILES."""
    if not _is_admet_available():
        return ADMETProfile(smiles=smiles, available=False)

    try:
        model = _get_admet_model()
        preds = model.predict(smiles=smiles)

        flags: List[str] = []
        pred_dict: Dict[str, float] = {}

        if isinstance(preds, dict):
            pred_dict = {k: float(v) for k, v in preds.items() if isinstance(v, (int, float))}
        else:
            pred_dict = {}

        for label, cfg in ADMET_THRESHOLDS.items():
            val = pred_dict.get(cfg["key"])
            if val is not None and cfg["threshold"] is not None:
                if cfg["direction"] == "above_is_bad" and val > cfg["threshold"]:
                    flags.append(f"{label}={val:.3f} (threshold {cfg['threshold']})")
                elif cfg["direction"] == "lower_is_bad" and val < cfg["threshold"]:
                    flags.append(f"{label}={val:.3f} (threshold {cfg['threshold']})")

        # TDC/ADMET-AI LD50_Zhu is log10(1 / (mol/kg)), not mg/kg. Convert
        # with molecular weight before applying the conventional 500 mg/kg
        # acute-toxicity review threshold. Comparing the raw ~0-10 model value
        # directly with 500 incorrectly flags virtually every molecule.
        ld50_log = pred_dict.get("LD50_Zhu")
        if ld50_log is not None:
            ld50_mg_kg = _ld50_log_inverse_molar_to_mg_kg(smiles, ld50_log)
            if ld50_mg_kg is not None:
                pred_dict["LD50_Zhu_mg_kg"] = ld50_mg_kg
                if ld50_mg_kg < 500.0:
                    flags.append(
                        f"LD50_Zhu={ld50_mg_kg:.1f} mg/kg predicted "
                        "(review threshold 500 mg/kg)"
                    )

        heavy = _heavy_atom_count(smiles)
        if heavy is not None and heavy < ADMET_MIN_HEAVY_ATOMS:
            return ADMETProfile(
                smiles=smiles,
                predictions=pred_dict,
                flags=[],
                in_domain=False,
                domain_note=(
                    f"{heavy} heavy atom(s): below the drug-like applicability domain of the "
                    f"ADMET models, so predictions are reported but not treated as findings"
                ),
            )
        return ADMETProfile(smiles=smiles, predictions=pred_dict, flags=flags)

    except Exception as exc:
        logger.error("ADMET-AI failed for %s: %s", smiles, exc)
        return ADMETProfile(smiles=smiles, available=False)


def _is_parseable(smiles: str) -> Optional[bool]:
    """True/False when RDKit can judge the structure, None when RDKit is absent."""
    try:
        from rdkit import Chem
    except ImportError:
        return None
    return Chem.MolFromSmiles(smiles) is not None


def screen_monomer(smiles: str) -> ToxicityResult:
    """Full toxicity screen on a single monomer SMILES: SMARTS + ADMET."""
    # An unparseable structure matches no SMARTS pattern and gets no ADMET
    # prediction, which previously produced an empty warning list and safe=True.
    # Nothing was screened, so nothing can be called safe.
    if _is_parseable(smiles) is False:
        return ToxicityResult(
            smiles=smiles,
            safe=False,
            parsed=False,
            warnings=[
                f"Structure {smiles!r} could not be parsed as SMILES; no toxicity screen was performed"
            ],
        )
    smarts_hits = _run_smarts_screen(smiles)
    admet = _run_admet(smiles)

    warnings: List[str] = []
    safe = True

    if smarts_hits:
        safe = False
        for hit in smarts_hits:
            warnings.append(f"SMARTS alert: {hit.pattern_name}")

    if admet and admet.flags:
        safe = False
        warnings.extend(admet.flags)

    return ToxicityResult(
        smiles=smiles,
        smarts_hits=smarts_hits,
        admet=admet,
        safe=safe,
        warnings=warnings,
    )


def screen_monomers_batch(smiles_list: List[str]) -> List[ToxicityResult]:
    """Screen multiple monomers."""
    return [screen_monomer(s) for s in smiles_list]
