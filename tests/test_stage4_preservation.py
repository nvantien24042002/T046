import json

from tests.fixtures import ROOT, source_digest


def test_stage3_sources_preserved_during_stage4():
    protected = json.loads((ROOT / "tests/stage4/protected_sources.json").read_text(encoding="utf-8-sig"))
    for item in protected:
        assert source_digest(ROOT / item["path"]) == item["sha256"], item["path"]
