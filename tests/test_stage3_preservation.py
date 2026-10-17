import json

from tests.fixtures import ROOT, source_digest


def test_accepted_prototype_baseline_and_dependency_files_preserved():
    protected = json.loads((ROOT / "tests/stage3/protected_sources.json").read_text(encoding="utf-8-sig"))
    for item in protected:
        assert source_digest(ROOT / item["path"]) == item["sha256"], item["path"]
