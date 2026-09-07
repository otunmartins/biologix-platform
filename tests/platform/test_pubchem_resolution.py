"""PubChem name resolution.

The REST call requests IsomericSMILES but PubChem returns the value under the key
"SMILES", so reading only the requested key returned None for every compound and
the failure was swallowed by a bare except.
"""

from biologix_ai.retrosynthesis import precursor_registry


class _Response:
    def __init__(self, body: bytes):
        self._body = body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _serve(monkeypatch, body: bytes):
    monkeypatch.setattr(precursor_registry.urllib.request, "urlopen", lambda *a, **k: _Response(body))


def test_modern_smiles_key_is_read(monkeypatch):
    import urllib.request
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: _Response(
        b'{"PropertyTable":{"Properties":[{"CID":962,"SMILES":"O"}]}}'))
    assert precursor_registry._pubchem_smiles("water") == "O"


def test_legacy_isomeric_key_still_works(monkeypatch):
    import urllib.request
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: _Response(
        b'{"PropertyTable":{"Properties":[{"CID":962,"IsomericSMILES":"O"}]}}'))
    assert precursor_registry._pubchem_smiles("water") == "O"


def test_connectivity_key_is_accepted(monkeypatch):
    import urllib.request
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: _Response(
        b'{"PropertyTable":{"Properties":[{"ConnectivitySMILES":"C1CO1"}]}}'))
    assert precursor_registry._pubchem_smiles("ethylene oxide") == "C1CO1"


def test_empty_property_table_returns_none(monkeypatch):
    import urllib.request
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: _Response(
        b'{"PropertyTable":{"Properties":[]}}'))
    assert precursor_registry._pubchem_smiles("nonexistent-compound") is None
