"""Windows ACL subprocess decoding; no real private stores or credentials."""
import json
from types import SimpleNamespace

import pytest

from backend.local_issuer_paths import IssuerUnavailable, private_acl


@pytest.mark.parametrize('unexpected_sid,protected,accepted', [
    (False, True, True),
    (True, True, False),
    (False, False, False),
])
def test_windows_acl_output_is_decoded_without_relaxing_policy(
        tmp_path, monkeypatch, unexpected_sid, protected, accepted):
    monkeypatch.setattr('backend.local_issuer_paths.os', SimpleNamespace(name='nt'))
    monkeypatch.setattr('backend.local_issuer_paths.subprocess.CREATE_NO_WINDOW', 0, raising=False)
    sid='S-1-5-21-100-200-300-1000'
    data={'me':sid, 'owner':sid, 'protected':protected,
          'rules':[{'sid':sid,'type':'Allow'}]}
    if unexpected_sid:
        data['rules'].append({'sid':'S-1-1-0','type':'Allow'})
    monkeypatch.setattr('backend.local_issuer_paths.subprocess.check_output',
                        lambda *args,**kwargs:json.dumps(data).encode())
    if accepted:
        private_acl(tmp_path,protected=True)
    else:
        with pytest.raises(IssuerUnavailable):
            private_acl(tmp_path,protected=True)
