"""ADMET model caching: construction is expensive (ten chemprop checkpoints plus the
DrugBank reference set), so it must happen once per process, not once per molecule."""

import sys
import types

import pytest


@pytest.fixture
def fake_admet_ai(monkeypatch):
    """Install a fake admet_ai module that counts how many times its model is built."""
    construction_count = {"n": 0}

    class FakeADMETModel:
        def __init__(self):
            construction_count["n"] += 1

        def predict(self, smiles):
            return {"hERG": 0.1, "DILI": 0.1, "AMES": 0.1}

    fake_module = types.ModuleType("admet_ai")
    fake_module.ADMETModel = FakeADMETModel
    monkeypatch.setitem(sys.modules, "admet_ai", fake_module)

    from biologix_ai.services import toxicity_service

    toxicity_service._get_admet_model.cache_clear()
    yield construction_count
    toxicity_service._get_admet_model.cache_clear()


def test_admet_model_is_built_once_across_many_screens(fake_admet_ai):
    from biologix_ai.services.toxicity_service import screen_monomer

    # Ethylene oxide, water, methanol: three distinct monomers a real route
    # would screen in one experiment.
    for smiles in ("C1CO1", "O", "CO"):
        screen_monomer(smiles)

    assert fake_admet_ai["n"] == 1


def test_admet_model_is_shared_across_separate_calls(fake_admet_ai):
    """Simulates two experiments in the same worker process."""
    from biologix_ai.services.toxicity_service import _get_admet_model

    first = _get_admet_model()
    second = _get_admet_model()

    assert first is second
    assert fake_admet_ai["n"] == 1
