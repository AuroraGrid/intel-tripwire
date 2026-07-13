import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from platform_wsgi import create_application
from storage import Store


def request(app,path="/api/platform/live",method="GET",body=None,headers=None,remote="127.0.0.1",host="localhost"):
    raw=b"" if body is None else (body if isinstance(body,bytes) else json.dumps(body).encode())
    environ={"REQUEST_METHOD":method,"PATH_INFO":path.split("?",1)[0],"QUERY_STRING":path.split("?",1)[1] if "?" in path else "","SERVER_NAME":"localhost","SERVER_PORT":"80","SERVER_PROTOCOL":"HTTP/1.1","wsgi.version":(1,0),"wsgi.url_scheme":"http","wsgi.input":io.BytesIO(raw),"wsgi.errors":io.StringIO(),"wsgi.multithread":False,"wsgi.multiprocess":False,"wsgi.run_once":False,"CONTENT_LENGTH":str(len(raw)),"REMOTE_ADDR":remote,"HTTP_HOST":host}
    for key,value in (headers or {}).items():environ["HTTP_"+key.upper().replace("-","_")]=value
    captured={}
    def start(status,response_headers):captured.update(status=status,headers=dict(response_headers))
    result=b"".join(app(environ,start)); captured["code"]=int(captured["status"].split()[0]); captured["body"]=result
    try:captured["json"]=json.loads(result) if result else None
    except json.JSONDecodeError:captured["json"]=None
    return captured

class WSGITests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.store=Store(Path(self.temp.name)/"test.db")
        self.admin,self.admin_token=self.store.create_user("admin@example.com","admin")
        self.viewer,self.viewer_token=self.store.create_user("viewer@example.com","viewer")
    def tearDown(self):self.temp.cleanup()
    @contextmanager
    def app(self,**values):
        defaults={"AURORA_ALLOWED_HOSTS":"localhost,public.example","AURORA_CORS_ORIGIN":"https://console.example","AURORA_AUTH_RATE_LIMIT":"2","AURORA_WRITE_RATE_LIMIT":"2","AURORA_RATE_WINDOW_SECONDS":"60"}; defaults.update(values)
        with patch.dict(os.environ,defaults,clear=False):yield create_application(store=self.store)
    def test_request_id_and_security_headers(self):
        with self.app() as app:r=request(app,headers={"X-Request-ID":"safe-id_1"})
        self.assertEqual(r["code"],200); self.assertEqual(r["headers"]["X-Request-ID"],"safe-id_1"); self.assertEqual(r["headers"]["X-Frame-Options"],"DENY")
    def test_unauthorized_and_forbidden_are_distinct(self):
        with self.app() as app:
            missing=request(app,"/api/platform/stats"); forbidden=request(app,"/api/platform/ingest","POST",{"events":[]},{"Authorization":"Bearer "+self.viewer_token})
        self.assertEqual(missing["code"],401); self.assertEqual(missing["json"]["error"]["code"],"unauthorized"); self.assertEqual(forbidden["code"],403); self.assertEqual(forbidden["json"]["error"]["code"],"forbidden")
    def test_invalid_json_and_oversized_body(self):
        with self.app(AURORA_MAX_BODY_BYTES="1024") as app:
            invalid=request(app,"/api/platform/cases","POST",b"{",{"Authorization":"Bearer "+self.admin_token}); large=request(app,"/api/platform/cases","POST",b"x"*1025,{"Authorization":"Bearer "+self.admin_token})
        self.assertEqual(invalid["code"],400); self.assertEqual(invalid["json"]["error"]["code"],"invalid_json"); self.assertEqual(large["code"],413)
    def test_host_cors_and_proxy_trust(self):
        with self.app(AURORA_TRUSTED_PROXIES="10.0.0.0/8") as app:
            bad_host=request(app,host="evil.example"); bad_origin=request(app,headers={"Origin":"https://evil.example"})
            ignored=request(app,"/api/platform/feed.json",headers={"Authorization":"Bearer "+self.admin_token,"X-Forwarded-Host":"public.example","X-Forwarded-Proto":"https"},remote="127.0.0.1")
            trusted=request(app,"/api/platform/feed.json",headers={"Authorization":"Bearer "+self.admin_token,"X-Forwarded-Host":"public.example","X-Forwarded-Proto":"https"},remote="10.1.2.3")
        self.assertEqual(bad_host["code"],400); self.assertEqual(bad_origin["code"],403); self.assertIn(b"http://localhost/api/platform/feed.json",ignored["body"]); self.assertIn(b"https://public.example/api/platform/feed.json",trusted["body"])
    def test_write_rate_limit(self):
        headers={"Authorization":"Bearer "+self.admin_token}
        with self.app(AURORA_WRITE_RATE_LIMIT="1") as app:
            first=request(app,"/api/platform/cases","POST",{"title":"One"},headers); second=request(app,"/api/platform/cases","POST",{"title":"Two"},headers)
        self.assertEqual(first["code"],201); self.assertEqual(second["code"],429); self.assertIn("Retry-After",second["headers"])
    def test_readiness_and_liveness(self):
        with self.app() as app:live=request(app,"/api/platform/live"); ready=request(app,"/api/platform/ready")
        self.assertEqual(live["json"]["status"],"alive"); self.assertEqual(ready["json"]["status"],"ready")

if __name__=="__main__":unittest.main()
