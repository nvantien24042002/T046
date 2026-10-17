"""Same-process browser boundary. Domain services retain their existing semantics.

Only an opaque application cookie reaches the browser. Session state is deliberately
process-local: restart/expiry revokes access, never cancels or adopts stored resources.
"""
from dataclasses import dataclass
import csv
import hashlib
import io
import json
from pathlib import Path
import re
import secrets
import threading
import time

from fastapi import Request
from starlette.responses import FileResponse, JSONResponse, Response
from starlette.concurrency import run_in_threadpool

from storage.uploads import UploadError
from storage.files import no_links

PREFIX = '/api/browser/v1'
COOKIE = 'n205b_session'
INTENT = 'N2-05B'


def csv_cell(value):
    if isinstance(value,(int,float)) or value is None:return value
    value=str(value)
    if value[:1] in ('\t','\r','\n') or value.lstrip(' \t\r\n').startswith(('=','+','-','@')):
        return "'"+value
    return value


@dataclass(frozen=True)
class BrowserSession:
    principal: str
    origin: str
    expires: float


class BrowserSessions:
    def __init__(self, *, clock=time.time, lifetime=3600, capacity=1000):
        self.clock, self.lifetime, self.capacity = clock, lifetime, capacity
        self._records = {}
        self._lock = threading.RLock()

    def lookup(self, offered):
        if not isinstance(offered, str) or not re.fullmatch(r'[A-Za-z0-9_-]{40,64}', offered):
            return None
        with self._lock:
            record = self._records.get(hashlib.sha256(offered.encode()).digest())
            return record if record and self.clock() < record.expires else None

    def create(self, origin):
        with self._lock:
            self._records = {k:v for k,v in self._records.items() if self.clock() < v.expires}
            if len(self._records) >= self.capacity:
                raise UploadError(429, 'SESSION_CAPACITY')
            offered = secrets.token_urlsafe(32)
            record = BrowserSession('anon_'+secrets.token_hex(16), origin, self.clock()+self.lifetime)
            self._records[hashlib.sha256(offered.encode()).digest()] = record
            return offered, record

    def revoke(self, offered):
        with self._lock:
            self._records.pop(hashlib.sha256(offered.encode()).digest(), None)


def add_browser_routes(app, origins, handlers, metadata_model, *, sessions=None, react_dist=None):
    sessions = sessions or BrowserSessions()
    app.state.browser_sessions = sessions

    def intent(request):
        # A custom same-origin request header plus bound Origin protects mutations.
        # Neither is identity: the opaque session and resource owner check are required.
        if request.headers.get('x-requested-with') != INTENT:
            raise UploadError(403, 'BROWSER_REQUEST_REQUIRED')
        if request.headers.get('sec-fetch-site') == 'cross-site':
            raise UploadError(403, 'ORIGIN_DENIED')
        if any(request.headers.get(k) for k in ('x-owner-key','upload-capability','authorization')):
            raise UploadError(403, 'CLIENT_CREDENTIAL_FORBIDDEN')

    def authorized(request):
        intent(request)
        if app.state.unhealthy:
            raise UploadError(503, 'SERVICE_UNHEALTHY')
        session = sessions.lookup(request.cookies.get(COOKIE, ''))
        if not session:
            raise UploadError(401, 'SESSION_REQUIRED')
        origin = request.headers.get('origin')
        if (origin and origin != session.origin) or (request.method not in ('GET','HEAD') and origin != session.origin):
            raise UploadError(403, 'ORIGIN_DENIED')
        return session

    def upload(session, upload_id):
        row = app.state.store.db.execute('SELECT * FROM uploads WHERE id=?',(upload_id,)).fetchone()
        if row is None or row['owner_id'] != session.principal:
            raise UploadError(404, 'RESOURCE_NOT_FOUND')
        return row

    def dataset(session, dataset_id):
        row = app.state.store.db.execute('SELECT upload_id FROM validation_datasets WHERE dataset_id=?',(dataset_id,)).fetchone()
        if row is None:
            raise UploadError(404, 'RESOURCE_NOT_FOUND')
        return upload(session, row['upload_id'])

    def internal(request, session, row, body=None):
        # Adapt authenticated server-side authority to existing domain-facing handlers.
        # No arbitrary client identity/header is forwarded and no HTTP proxy is used.
        headers=[(k,v) for k,v in request.scope['headers'] if k.lower() in
                 (b'content-type',b'upload-offset',b'x-chunk-sha256')]
        headers.append((b'upload-capability',app.state.store.capability(session.principal,row['id']).encode()))
        scope={**request.scope, 'headers':headers}
        if body is None:
            return Request(scope, request.receive)
        async def receive():
            return {'type':'http.request','body':body,'more_body':False}
        return Request(scope, receive)

    def checked(request, session, value):
        if sessions.lookup(request.cookies.get(COOKIE,'')) != session:
            raise UploadError(401,'SESSION_REQUIRED')
        return value

    def public_upload(value):
        allowed=('upload_id','filename','size_bytes','chunk_size_bytes','state','received_bytes',
                 'recorded_bytes','next_offset','durable','expires_at','cleanup_pending','error_code')
        return {k:value[k] for k in allowed if k in value}

    def public_analysis(value):
        from backend.analytics_result_contracts import Payload
        try:
            parsed=Payload.model_validate(value).model_dump(by_alias=True)
            fingerprint=value.get('request_fingerprint')
            if not isinstance(fingerprint,str) or not re.fullmatch('[0-9a-f]{64}',fingerprint):
                raise ValueError()
            parsed['request_fingerprint']=fingerprint
            if len(json.dumps(parsed,allow_nan=False).encode())>16*1024*1024:
                raise UploadError(413,'RESPONSE_LIMIT')
            return parsed
        except ValueError:
            raise UploadError(502,'INVALID_ANALYSIS_RESULT') from None

    async def limited_json(request, limit=65536):
        raw=bytearray()
        async for block in request.stream():
            raw.extend(block)
            if len(raw)>limit:raise UploadError(413,'REQUEST_TOO_LARGE')
        try:
            data=json.loads(raw)
            if not isinstance(data,dict):raise ValueError()
            return data
        except (ValueError,UnicodeError):raise UploadError(422,'INVALID_REQUEST') from None

    @app.post(PREFIX+'/session')
    async def create_session(request: Request):
        intent(request)
        origin=request.headers.get('origin')
        if origin not in origins:raise UploadError(403,'ORIGIN_DENIED')
        existing=sessions.lookup(request.cookies.get(COOKIE,''))
        if existing:
            if existing.origin!=origin:raise UploadError(403,'ORIGIN_DENIED')
            return JSONResponse({'state':'active','expires_at':existing.expires},headers={'Cache-Control':'no-store'})
        offered, record=sessions.create(origin)
        response=JSONResponse({'state':'active','expires_at':record.expires},headers={'Cache-Control':'no-store'})
        response.set_cookie(COOKIE,offered,httponly=True,secure=request.url.scheme=='https',
                            samesite='strict',path=PREFIX,max_age=sessions.lifetime)
        return response

    @app.get(PREFIX+'/session')
    async def session_status(request: Request):
        record=authorized(request)
        return {'state':'active','expires_at':record.expires}

    @app.delete(PREFIX+'/session')
    async def revoke_session(request: Request):
        authorized(request)
        sessions.revoke(request.cookies[COOKIE])
        response=Response(status_code=204)
        response.delete_cookie(COOKIE,path=PREFIX)
        return response

    @app.post(PREFIX+'/uploads',status_code=201)
    async def create(request: Request):
        session=authorized(request)
        try:meta=metadata_model.model_validate(await limited_json(request,4096))
        except ValueError:raise UploadError(422,'INVALID_METADATA') from None
        value=await run_in_threadpool(app.state.store.create,session.principal,meta.filename,meta.size_bytes,
                                      request.headers.get('idempotency-key',''))
        return checked(request,session,public_upload(value))

    @app.get(PREFIX+'/uploads/{upload_id}')
    async def status(upload_id: str, request: Request):
        session=authorized(request);row=upload(session,upload_id)
        value=await handlers['status'](upload_id,internal(request,session,row))
        return checked(request,session,public_upload(value))

    @app.get(PREFIX+'/uploads/{upload_id}/receipts')
    async def receipts(upload_id: str, request: Request, start: int=0, limit: int=128):
        session=authorized(request);row=upload(session,upload_id)
        return checked(request,session,await handlers['receipts'](upload_id,internal(request,session,row),start,limit))

    @app.put(PREFIX+'/uploads/{upload_id}/chunks/{index}')
    async def chunk(upload_id: str,index: int,request: Request):
        session=authorized(request);row=upload(session,upload_id)
        return checked(request,session,await handlers['chunk'](upload_id,index,internal(request,session,row)))

    @app.post(PREFIX+'/uploads/{upload_id}/complete')
    async def complete(upload_id: str,request: Request):
        session=authorized(request);row=upload(session,upload_id)
        return checked(request,session,public_upload(await handlers['complete'](upload_id,internal(request,session,row))))

    @app.delete(PREFIX+'/uploads/{upload_id}')
    async def cancel_upload(upload_id: str,request: Request):
        session=authorized(request);row=upload(session,upload_id)
        return checked(request,session,public_upload(await handlers['cancel'](upload_id,internal(request,session,row))))

    @app.post(PREFIX+'/uploads/{upload_id}/validate')
    async def validate(upload_id: str,request: Request,retry: bool=False):
        session=authorized(request);row=upload(session,upload_id)
        return checked(request,session,await handlers['validate'](upload_id,internal(request,session,row),retry))

    @app.get(PREFIX+'/uploads/{upload_id}/dataset')
    async def lifecycle(upload_id: str,request: Request):
        session=authorized(request);row=upload(session,upload_id)
        return checked(request,session,await handlers['lifecycle'](upload_id,internal(request,session,row)))

    @app.post(PREFIX+'/uploads/{upload_id}/dataset/cancel')
    async def cancel_validation(upload_id: str,request: Request):
        session=authorized(request);row=upload(session,upload_id)
        return checked(request,session,await handlers['cancel_validation'](upload_id,internal(request,session,row)))

    @app.get(PREFIX+'/datasets')
    async def datasets(request: Request):
        session=authorized(request)
        rows=app.state.store.db.execute('SELECT d.dataset_id,d.state,u.id AS upload_id FROM validation_datasets d JOIN uploads u ON u.id=d.upload_id WHERE u.owner_id=?',(session.principal,)).fetchall()
        return {'datasets':[dict(row) for row in rows]}

    @app.get(PREFIX+'/datasets/{dataset_id}/summary')
    async def summary(dataset_id: str,request: Request):
        session=authorized(request);row=dataset(session,dataset_id)
        return checked(request,session,await handlers['summary'](dataset_id,internal(request,session,row)))

    @app.post(PREFIX+'/datasets/{dataset_id}/analyze')
    async def analyze(dataset_id: str,request: Request):
        session=authorized(request);row=dataset(session,dataset_id)
        return checked(request,session,public_analysis(await handlers['analyze'](dataset_id,internal(request,session,row))))

    @app.get(PREFIX+'/datasets/{dataset_id}/results')
    async def results(dataset_id: str,request: Request):
        session=authorized(request);row=dataset(session,dataset_id)
        return checked(request,session,public_analysis(await handlers['results'](dataset_id,internal(request,session,row))))

    @app.get(PREFIX+'/datasets/{dataset_id}/configurations')
    async def configurations(dataset_id: str,request: Request):
        session=authorized(request);dataset(session,dataset_id)
        from core.image_quality import DEFAULT_IMAGE_CFG
        from core.label_audit import DEFAULT_LABEL_CFG
        return {'default':{'image_cfg':DEFAULT_IMAGE_CFG,'label_cfg':DEFAULT_LABEL_CFG}}

    @app.post(PREFIX+'/datasets/{dataset_id}/dashboard')
    async def dashboard(dataset_id: str,request: Request):
        session=authorized(request);row=dataset(session,dataset_id)
        response=await handlers['dashboard'](dataset_id,internal(request,session,row))
        checked(request,session,None)
        return response

    @app.get(PREFIX+'/datasets/{dataset_id}/preview')
    async def preview(dataset_id: str,request: Request,image_path: str,annotated: bool=False):
        session=authorized(request);row=dataset(session,dataset_id)
        response=await handlers['preview'](dataset_id,internal(request,session,row),image_path,annotated)
        checked(request,session,None)
        response.headers['Cache-Control']='no-store'
        response.headers['X-Content-Type-Options']='nosniff'
        return response

    @app.post(PREFIX+'/datasets/{dataset_id}/export')
    async def export(dataset_id: str,request: Request):
        session=authorized(request);row=dataset(session,dataset_id)
        body=await limited_json(request)
        table_key=body.pop('table_key',None)
        response=await handlers['dashboard'](dataset_id,internal(request,session,row,json.dumps(body).encode()))
        view=json.loads(response.body)
        if view['state']=='unsupported':raise UploadError(409,'EXPORT_UNAVAILABLE')
        tables=[t for t in view['tables'] if t['key']==table_key and t.get('export_filename')]
        if len(tables)!=1:raise UploadError(422,'INVALID_EXPORT')
        rows=tables[0]['rows']
        if len(rows)>100000:raise UploadError(413,'EXPORT_LIMIT')
        columns=list(rows[0]) if rows else []
        if any(set(r)!=set(columns) for r in rows):raise UploadError(502,'INVALID_EXPORT_SCHEMA')
        buffer=io.StringIO();writer=csv.writer(buffer);writer.writerow(columns)
        for item in rows:writer.writerow([csv_cell(item[k]) for k in columns])
        encoded=('\ufeff'+buffer.getvalue()).encode()
        if len(encoded)>8*1024*1024:raise UploadError(413,'EXPORT_LIMIT')
        checked(request,session,None)
        return Response(encoded,media_type='text/csv',headers={'Content-Disposition':'attachment; filename="dataset-export.csv"','Cache-Control':'no-store','X-Content-Type-Options':'nosniff'})

    @app.middleware('http')
    async def browser_cache_control(request,call_next):
        response=await call_next(request)
        if request.url.path.startswith(PREFIX):response.headers['Cache-Control']='no-store'
        return response

    if react_dist is not None:
        dist=Path(react_dist).resolve();no_links(dist);no_links(dist/'index.html')
        if not (dist/'index.html').is_file() or any((dist/n).exists() for n in ('package.json','.git','src')):
            raise ValueError('React build output required, never a source/private root')
        @app.get('/{spa_path:path}',include_in_schema=False)
        async def static(spa_path: str):
            if spa_path in ('api','analytics') or spa_path.startswith(('api/','analytics/','client')) or any(x.startswith('.') for x in spa_path.split('/')):
                return Response(status_code=404)
            target=(dist/spa_path).resolve()
            if not target.is_relative_to(dist) or target.suffix=='.map':return Response(status_code=404)
            no_links(target)
            if target.is_file():
                if target.name!='index.html' and target.suffix not in {'.js','.css','.svg','.png','.jpg','.jpeg','.ico','.webp','.woff','.woff2','.ttf'}:
                    return Response(status_code=404)
                return FileResponse(target,headers={'X-Content-Type-Options':'nosniff'})
            if Path(spa_path).suffix:return Response(status_code=404)
            return FileResponse(dist/'index.html',headers={'Cache-Control':'no-store'})
