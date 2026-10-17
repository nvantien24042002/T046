"""Recovery tests exclusively against isolated temporary registries."""
import hashlib
import io
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest

from tests.test_integrated_api import integrated_api, upload_fixture_via_api, auth
from tests.fixtures import make_zip
from ui.backend_adapter import BackendClient, BackendError, upload_source_key


def source_key(name, data):
    return upload_source_key(name, len(data), hashlib.sha256(data).hexdigest())


@pytest.mark.parametrize('filename', ['new customer (42).zip', 'khách_hàng_東京.zip'])
def test_lost_create_ack_recovers_same_session(integrated_api, filename):
    tc, app, owners = integrated_api
    data = b'new source bytes' * 2000
    key = source_key(filename, data)
    original_post = tc.post
    lost = False
    def post(*args, **kwargs):
        nonlocal lost
        response = original_post(*args, **kwargs)
        if not lost:
            lost = True
            raise httpx.ReadError('lost acknowledgement')
        return response
    tc.post = post
    adapter = BackendClient(owner_key=owners['alice'], http_client=tc)
    with pytest.raises(BackendError):
        adapter.stream_upload(io.BytesIO(data), filename, len(data), idempotency_key=key)
    session = adapter.stream_upload(io.BytesIO(data), filename, len(data), idempotency_key=key)
    assert session['state'] == 'uploaded'
    assert app.state.store.db.execute('SELECT COUNT(*) FROM uploads').fetchone()[0] == 1
    assert (app.state.store.uploads_dir/session['upload_id']/'payload.part').read_bytes() == data
    count = app.state.store.db.execute('SELECT COUNT(*) FROM receipts').fetchone()[0]
    # A repeated full invocation verifies receipts and reuses the sealed upload.
    assert adapter.stream_upload(io.BytesIO(data), filename, len(data), idempotency_key=key)['upload_id'] == session['upload_id']
    assert app.state.store.db.execute('SELECT COUNT(*) FROM receipts').fetchone()[0] == count


def test_lost_chunk_ack_resumes_without_duplicate_bytes(integrated_api):
    tc, app, owners = integrated_api
    data = b'a' * 40000
    adapter = BackendClient(owner_key=owners['alice'], http_client=tc)
    real_put = tc.put
    lost = False
    def put(*args, **kwargs):
        nonlocal lost
        result = real_put(*args, **kwargs)
        if not lost:
            lost = True
            raise httpx.ReadError('lost chunk ack')
        return result
    tc.put = put
    key = source_key('different.zip', data)
    with pytest.raises(BackendError):
        adapter.stream_upload(io.BytesIO(data), 'different.zip', len(data), idempotency_key=key)
    session = adapter.stream_upload(io.BytesIO(data), 'different.zip', len(data), idempotency_key=key)
    assert session['received_bytes'] == len(data)
    assert app.state.store.db.execute('SELECT COUNT(*) FROM uploads').fetchone()[0] == 1
    assert app.state.store.db.execute('SELECT COUNT(*) FROM receipts').fetchone()[0] == 3
    assert (app.state.store.uploads_dir/session['upload_id']/'payload.part').read_bytes() == data


@pytest.mark.parametrize('filename,size,data,code', [
    ('wrong.zip', 20000, b'a'*20000, 'SOURCE_METADATA_MISMATCH'),
    ('original.zip', 20001, b'a'*20001, 'SOURCE_METADATA_MISMATCH'),
    ('original.zip', 20000, b'b'*20000, 'SOURCE_PREFIX_MISMATCH'),
])
def test_manual_recovery_rejects_mismatch_without_writes(integrated_api, filename, size, data, code):
    tc, app, owners = integrated_api
    adapter = BackendClient(owner_key=owners['alice'], http_client=tc)
    session = adapter.create_upload('original.zip', 20000)
    adapter.upload_chunk(session['upload_id'], session['upload_capability'], 0, 0, b'a'*16384)
    before = tuple(app.state.store.db.execute('SELECT * FROM uploads').fetchone())
    with pytest.raises(BackendError) as caught:
        adapter.stream_upload(io.BytesIO(data), filename, size, recovery_session=session, confirmed_legacy_source=True)
    assert caught.value.code == code
    assert tuple(app.state.store.db.execute('SELECT * FROM uploads').fetchone()) == before
    assert app.state.store.db.execute('SELECT COUNT(*) FROM receipts').fetchone()[0] == 1


def test_fingerprint_and_global_concurrency(integrated_api):
    tc, app, owners = integrated_api
    data=b'first'
    key=source_key('arbitrary.zip',data)
    assert key != source_key('arbitrary.zip',b'other')
    assert key != source_key('renamed.zip',data)
    def create(who, token):
        return tc.post('/api/v1/uploads', headers={'X-Owner-Key':owners[who], 'Idempotency-Key':token}, json={'filename':'arbitrary.zip','size_bytes':len(data)})
    with ThreadPoolExecutor(2) as executor:
        results=list(executor.map(lambda who:create(who, key), ['alice','bob']))
    assert sorted(r.status_code for r in results)==[201,429]
    assert next(r for r in results if r.status_code==429).json()['error']['code']=='ONE_ACTIVE_UPLOAD'
    winner='alice' if results[0].status_code==201 else 'bob'
    with ThreadPoolExecutor(2) as executor:
        retries=list(executor.map(lambda _:create(winner,key),range(2)))
    assert len({r.json()['upload_id'] for r in retries})==1
    assert all(r.status_code==201 for r in retries)
    assert app.state.store.db.execute('SELECT COUNT(*) FROM uploads').fetchone()[0]==1


def test_duplicate_analysis_requests_reuse_pipeline(integrated_api,tmp_path,monkeypatch):
    tc, app, owners=integrated_api
    archive=make_zip(tmp_path/'new.zip')
    session=upload_fixture_via_api(tc,owners,archive.read_bytes(),filename='new.zip')
    url='/api/v1/uploads/'+session['upload_id']
    headers=auth(owners,session)
    result=tc.post(url+'/validate',headers=headers)
    assert result.status_code==200
    import time
    deadline=time.monotonic()+15
    while tc.get(url+'/dataset',headers=headers).json()['state']!='ready':
        assert time.monotonic()<deadline
        time.sleep(.05)
    dataset=tc.get(url+'/dataset',headers=headers).json()['dataset_id']
    import backend.integrated_api as api
    original=api.execute_pipeline
    calls=[]
    def pipeline(*args,**kwargs):
        calls.append(1)
        return original(*args,**kwargs)
    monkeypatch.setattr(api,'execute_pipeline',pipeline)
    with ThreadPoolExecutor(2) as executor:
        results=list(executor.map(lambda _:tc.post('/api/v1/datasets/'+dataset+'/analyze',headers=headers,json={}),range(2)))
    assert all(r.status_code==200 for r in results)
    assert len(calls)==1
    assert results[0].json()==results[1].json()
    assert tc.post('/api/v1/datasets/'+dataset+'/analyze',headers=headers,json={'image_cfg':{}}).status_code==200
    assert len(calls)==2
    assert tc.post('/api/v1/datasets/'+dataset+'/analyze',headers=headers,json={'image_cfg':{}}).status_code==200
    assert len(calls)==2
    assert tc.post('/api/v1/datasets/'+dataset+'/analyze',headers=headers,json={}).status_code==200
    assert len(calls)==2
    assert tc.post(url+'/validate',headers=headers).json()['state']=='ready'
    assert app.state.store.db.execute('SELECT COUNT(*) FROM validation_attempts').fetchone()[0]==1


def test_manual_recovery_requires_explicit_confirmation(integrated_api):
    tc, app, owners=integrated_api
    adapter=BackendClient(owner_key=owners['alice'],http_client=tc)
    session=adapter.create_upload('old.zip',12)
    with pytest.raises(BackendError) as caught:
        adapter.stream_upload(io.BytesIO(b'x'*12),'old.zip',12,recovery_session=session)
    assert caught.value.code=='RECOVERY_CONFIRMATION_REQUIRED'
    assert app.state.store.db.execute('SELECT committed_bytes FROM uploads').fetchone()[0]==0


def test_same_name_size_different_content_does_not_adopt_zero_byte_session(integrated_api):
    tc, app, owners=integrated_api
    adapter=BackendClient(owner_key=owners['alice'],http_client=tc)
    adapter.create_upload('same.zip',12,source_key('same.zip',b'a'*12))
    with pytest.raises(BackendError) as caught:
        adapter.stream_upload(io.BytesIO(b'b'*12),'same.zip',12,idempotency_key=source_key('same.zip',b'b'*12))
    assert caught.value.code=='ONE_ACTIVE_UPLOAD'
    assert app.state.store.db.execute('SELECT COUNT(*) FROM uploads').fetchone()[0]==1
    assert app.state.store.db.execute('SELECT committed_bytes FROM uploads').fetchone()[0]==0


def test_lost_complete_ack_reuses_sealed_session(integrated_api):
    tc,app,owners=integrated_api
    adapter=BackendClient(owner_key=owners['alice'],http_client=tc)
    original=tc.post
    calls=[]
    def post(url,**kwargs):
        result=original(url,**kwargs)
        if str(url).endswith('/complete'):
            calls.append(1)
            raise httpx.ReadError('lost completion acknowledgement')
        return result
    tc.post=post
    data=b'z'*12
    key=source_key('completed.zip',data)
    with pytest.raises(BackendError):
        adapter.stream_upload(io.BytesIO(data),'completed.zip',12,idempotency_key=key)
    assert adapter.stream_upload(io.BytesIO(data),'completed.zip',12,idempotency_key=key)['state']=='uploaded'
    assert len(calls)==1
    assert app.state.store.db.execute('SELECT COUNT(*) FROM uploads').fetchone()[0]==1


def test_streamlit_selection_and_legacy_fields_do_not_start_upload(monkeypatch):
    import streamlit as st
    from streamlit.testing.v1 import AppTest
    from tests.fixtures import ROOT
    data=io.BytesIO(b'not transmitted')
    data.name='new user 東京.zip'
    data.size=len(data.getvalue())
    monkeypatch.setattr(st,'file_uploader',lambda *args,**kwargs:data)
    monkeypatch.setenv('CVAT_USE_BACKEND','1')
    monkeypatch.setenv('CVAT_OWNER_KEY','synthetic-ui-key')
    def forbidden(*args,**kwargs):
        raise AssertionError('Selecting a file must not contact or mutate backend')
    monkeypatch.setattr(BackendClient,'check_health',forbidden)
    monkeypatch.setattr(BackendClient,'stream_upload',forbidden)
    page=AppTest.from_file(str(ROOT/'app.py')).run(timeout=30)
    assert not page.exception
    assert any(b.label=='Tải lên hoặc tiếp tục phiên đã xác minh' for b in page.button)
    next(t for t in page.text_input if t.label=='Upload ID của phiên gốc').input('original-session')
    next(t for t in page.text_input if t.label=='Upload capability của phiên gốc').input('synthetic-capability')
    page.run()
    assert not page.exception
    # No confirmation means the action remains unavailable.
    assert not any(b.label=='Tải lên hoặc tiếp tục phiên đã xác minh' for b in page.button)


def test_terminal_session_requires_explicit_new_attempt_and_keeps_history(integrated_api):
    tc,app,owners=integrated_api
    adapter=BackendClient(owner_key=owners['alice'],http_client=tc)
    data=b'terminal source'
    key=source_key('again.zip',data)
    old=adapter.create_upload('again.zip',len(data),key)
    assert tc.delete('/api/v1/uploads/'+old['upload_id'],headers=auth(owners,old)).status_code==200
    before=tuple(app.state.store.db.execute('SELECT * FROM uploads').fetchone())
    with pytest.raises(BackendError) as caught:
        adapter.stream_upload(io.BytesIO(data),'again.zip',len(data))
    assert caught.value.code=='UPLOAD_NOT_RESUMABLE'
    new=adapter.stream_upload(io.BytesIO(data),'again.zip',len(data),restart_terminal=True)
    assert new['upload_id']!=old['upload_id']
    assert tuple(app.state.store.db.execute('SELECT * FROM uploads WHERE id=?',(old['upload_id'],)).fetchone())==before
    again=adapter.stream_upload(io.BytesIO(data),'again.zip',len(data),restart_terminal=True)
    assert again['upload_id']==new['upload_id']
    assert app.state.store.db.execute('SELECT COUNT(*) FROM uploads').fetchone()[0]==2
