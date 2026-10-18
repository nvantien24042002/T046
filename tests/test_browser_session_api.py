import hashlib
import json
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from backend.browser_api import BrowserSessions, COOKIE, PREFIX, csv_cell
from backend.integrated_api import create_integrated_app
from storage.config import UploadConfig
from tests.fixtures import make_zip

ORIGIN='http://127.0.0.1:8011'
HEADERS={'Origin':ORIGIN,'X-Requested-With':'N2-05B'}


@pytest.fixture
def browser_api(tmp_path):
    now=[1000.0]
    sessions=BrowserSessions(clock=lambda:now[0],lifetime=60)
    config=UploadConfig(tmp_path/'private','isolated-internal-secret-sentinel-000000',
                        chunk_bytes=65536,min_free_bytes=0,max_bytes=500000,quota_bytes=10**9)
    app=create_integrated_app(config,{'legacy':'isolated-owner-key-sentinel-000000000'},browser_sessions=sessions)
    with TestClient(app,base_url=ORIGIN,client=('127.0.0.1',12345),headers=HEADERS) as first:
        second=TestClient(app,base_url=ORIGIN,client=('127.0.0.1',12346),headers=HEADERS)
        assert first.post(PREFIX+'/session').status_code==200
        assert second.post(PREFIX+'/session').status_code==200
        yield first,second,app,now
        second.close()


def create(client,data,key=None):
    return client.post(PREFIX+'/uploads',json={'filename':'fixture.zip','size_bytes':len(data)},headers={'Idempotency-Key':key or str(uuid4())})


def ready(client,tmp_path):
    data=make_zip(tmp_path/'fixture.zip').read_bytes()
    result=create(client,data);assert result.status_code==201
    upload=result.json()['upload_id']
    for offset in range(0,len(data),65536):
        part=data[offset:offset+65536]
        response=client.put(f'{PREFIX}/uploads/{upload}/chunks/{offset//65536}',content=part,
                            headers={'Upload-Offset':str(offset),'X-Chunk-SHA256':hashlib.sha256(part).hexdigest()})
        assert response.status_code==200
    assert client.post(f'{PREFIX}/uploads/{upload}/complete').status_code==200
    validated=client.post(f'{PREFIX}/uploads/{upload}/validate')
    assert validated.status_code==200 and validated.json()['ready']
    dataset=validated.json()['dataset_id']
    result=client.post(f'{PREFIX}/datasets/{dataset}/analyze',json={})
    assert result.status_code==200
    return upload,dataset,result.json()


def test_cookie_is_opaque_httponly_and_no_shared_principal(browser_api):
    a,b,app,_=browser_api
    assert a.cookies.get(COOKIE)!=b.cookies.get(COOKIE)
    sa=app.state.browser_sessions.lookup(a.cookies.get(COOKIE))
    sb=app.state.browser_sessions.lookup(b.cookies.get(COOKIE))
    assert sa.principal!=sb.principal
    fresh=TestClient(app,base_url=ORIGIN,client=('127.0.0.1',1),headers=HEADERS)
    response=fresh.post(PREFIX+'/session');cookie=response.headers['set-cookie'].lower()
    assert 'httponly' in cookie and 'samesite=strict' in cookie
    assert set(response.json())=={'state','expires_at'}
    fresh.close()


@pytest.mark.parametrize('method,path,body',[
    ('GET','',None),('GET','/receipts',None),('PUT','/chunks/0',b'x'),
    ('POST','/complete',None),('POST','/validate',None),('GET','/dataset',None),
    ('DELETE','',None),('POST','/dataset/cancel',None),
])
def test_cross_session_upload_operations_denied(browser_api,method,path,body):
    a,b,app,_=browser_api
    result=create(a,b'x');upload=result.json()['upload_id']
    response=b.request(method,f'{PREFIX}/uploads/{upload}{path}',content=body)
    assert response.status_code==404
    assert app.state.store.db.execute('SELECT committed_bytes FROM uploads WHERE id=?',(upload,)).fetchone()[0]==0


def test_create_chunk_complete_replay_and_global_concurrency(browser_api):
    a,b,app,_=browser_api;data=b'zip-data';key=str(uuid4())
    first=create(a,data,key).json();same=create(a,data,key).json()
    assert first['upload_id']==same['upload_id']
    assert first['chunk_size_bytes']==65536
    assert 'upload_capability' not in first
    res_b = create(b,data)
    assert res_b.status_code == 201
    assert res_b.json()['upload_id'] != first['upload_id']
    path=f"{PREFIX}/uploads/{first['upload_id']}"
    headers={'Upload-Offset':'0','X-Chunk-SHA256':hashlib.sha256(data).hexdigest()}
    assert a.put(path+'/chunks/0',content=data,headers=headers).status_code==200
    repeated=a.put(path+'/chunks/0',content=data,headers=headers)
    assert repeated.status_code==200 and repeated.json()['replayed']
    assert a.post(path+'/complete').status_code==200
    assert a.post(path+'/complete').status_code==200
    assert app.state.store.db.execute('SELECT count(*) FROM receipts').fetchone()[0]==1


def test_real_pipeline_identity_preview_export_and_cross_session(browser_api,tmp_path):
    a,b,app,_=browser_api
    upload,dataset,payload=ready(a,tmp_path)
    base=f'{PREFIX}/datasets/{dataset}'
    assert a.get(base+'/summary').json()['ready']
    assert a.get(base+'/results').json()['request_fingerprint']==payload['request_fingerprint']
    assert b.get(PREFIX+'/datasets').json()=={'datasets':[]}
    for method,suffix,body in [('GET','/summary',None),('GET','/results',None),('GET','/configurations',None),
                              ('POST','/analyze',{}),('POST','/dashboard',{}),('POST','/export',{}),
                              ('GET','/preview?image_path=images/flat.png',None)]:
        assert b.request(method,base+suffix,json=body).status_code==404
    query={'page':'quality','expected_fingerprint':payload['request_fingerprint'],'query':{}}
    projected=a.post(base+'/dashboard',json=query)
    assert projected.status_code==200 and projected.json()['state']=='ready'
    assert a.post(base+'/dashboard',json={**query,'expected_fingerprint':'0'*64}).status_code==409
    table=next(t for t in projected.json()['tables'] if t['export_filename'])
    export=a.post(base+'/export',json={**query,'table_key':table['key']})
    assert export.status_code==200 and export.headers['content-type'].startswith('text/csv')
    assert a.get(base+'/preview',params={'image_path':'images/flat.png'}).status_code==200
    assert a.get(base+'/preview',params={'image_path':'images/flat.png','annotated':'true'}).status_code==200
    assert app.state.store.db.execute('SELECT count(*) FROM validation_leases').fetchone()[0]==0
    text=json.dumps([projected.json(),a.get(base+'/summary').json(),a.get(PREFIX+'/datasets').json()])
    for forbidden in ['isolated-internal-secret-sentinel','isolated-owner-key-sentinel','upload_capability','owner_id']:
        assert forbidden not in text


def test_expiry_does_not_cancel_or_adopt_upload(browser_api):
    a,b,app,clock=browser_api
    upload=create(a,b'x').json()['upload_id']
    clock[0]+=61
    assert a.get(f'{PREFIX}/uploads/{upload}').status_code==401
    assert a.post(PREFIX+'/session').status_code==200
    assert a.get(f'{PREFIX}/uploads/{upload}').status_code==404
    assert app.state.store.db.execute('SELECT state FROM uploads WHERE id=?',(upload,)).fetchone()[0]=='created'


@pytest.mark.parametrize('headers',[{'Origin':'http://outside.invalid'}, {'X-Owner-Key':'fake-internal-credential'},
                                    {'Authorization':'Bearer fake'}, {'Upload-Capability':'fake'},
                                    {'X-Requested-With':''}])
def test_csrf_and_client_credential_headers_fail_closed(browser_api,headers):
    a,_,app,_=browser_api
    response=a.post(PREFIX+'/uploads',json={'filename':'test.zip','size_bytes':1},headers=headers)
    assert response.status_code==403
    assert app.state.store.db.execute('SELECT count(*) FROM uploads').fetchone()[0]==0


def test_static_spa_does_not_swallow_api_or_expose_source(tmp_path):
    dist=tmp_path/'approved-build';dist.mkdir();(dist/'index.html').write_text('<div>React build</div>')
    (dist/'assets').mkdir();(dist/'assets'/'app.js').write_text('console.log("public")')
    app=create_integrated_app(UploadConfig(tmp_path/'private','isolated-static-secret-sentinel-000000',min_free_bytes=0),
                              {'legacy':'isolated-owner-key-sentinel-000000000'},react_dist=dist)
    with TestClient(app,base_url=ORIGIN,client=('127.0.0.1',12345)) as c:
        assert c.get('/overview').status_code==200
        assert c.get('/assets/app.js').status_code==200
        for path in ['/api','/api/not-a-route','/analytics/not-a-route','/.env','/package.json','/assets/app.js.map','/client']:
            assert c.get(path).status_code==404


@pytest.mark.parametrize('value',['=SUM(1,2)','+cmd','-cmd','@SUM(1)','  =1','\tformula','\rformula','\nformula'])
def test_csv_formula_strings_are_neutralized(value):
    assert csv_cell(value)=="'"+value


@pytest.mark.parametrize('value',[-12,1.5,None,'normal','comma,value','quote"value'])
def test_csv_safe_values_and_numeric_semantics_retained(value):
    assert csv_cell(value)==value


def test_restart_registry_does_not_accept_old_cookie(browser_api):
    a,_,app,_=browser_api
    offered=a.cookies.get(COOKIE)
    assert app.state.browser_sessions.lookup(offered) is not None
    assert BrowserSessions().lookup(offered) is None


def test_unhealthy_registry_blocks_browser_resource_operations(browser_api):
    a,_,app,_=browser_api
    app.state.unhealthy=True
    response=create(a,b'x')
    assert response.status_code==503
    assert response.json()=={'error':{'code':'SERVICE_UNHEALTHY'}}
    assert app.state.store.db.execute('SELECT count(*) FROM uploads').fetchone()[0]==0


def test_https_cookie_is_secure(tmp_path):
    origin='https://127.0.0.1:8011'
    app=create_integrated_app(UploadConfig(tmp_path/'private','isolated-https-secret-sentinel-000000',min_free_bytes=0),
                              {'legacy':'isolated-owner-key-sentinel-000000000'},origins=(origin,))
    with TestClient(app,base_url=origin,client=('127.0.0.1',1)) as c:
        r=c.post(PREFIX+'/session',headers={'Origin':origin,'X-Requested-With':'N2-05B'})
        assert r.status_code==200 and 'secure' in r.headers['set-cookie'].lower()
