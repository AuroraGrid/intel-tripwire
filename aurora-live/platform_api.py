from __future__ import annotations
import argparse, json, os, urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from storage import Store, now

STORE=Store(os.getenv('DATABASE_PATH','data/aurora-live.db'))

def bearer(h):
    a=h.get('Authorization','');return a[7:].strip() if a.lower().startswith('bearer ') else ''

class Handler(BaseHTTPRequestHandler):
    def send_json(self,code,data):
        b=json.dumps(data,ensure_ascii=False).encode();self.send_response(code);self.send_header('Content-Type','application/json');self.send_header('Cache-Control','no-store');self.send_header('Access-Control-Allow-Origin',os.getenv('AURORA_CORS_ORIGIN','*'));self.send_header('Access-Control-Allow-Headers','Authorization,Content-Type,X-Bootstrap-Secret');self.send_header('Content-Length',str(len(b)));self.end_headers();self.wfile.write(b)
    def body(self):
        n=int(self.headers.get('Content-Length','0'));return json.loads(self.rfile.read(n) or b'{}')
    def user(self):
        u=STORE.auth(bearer(self.headers))
        if not u:raise PermissionError('valid bearer token required')
        return u
    def route(self):
        p=urllib.parse.urlparse(self.path);return p.path,urllib.parse.parse_qs(p.query),[x for x in p.path.split('/') if x]
    def do_OPTIONS(self):self.send_json(204,{})
    def do_GET(self):
        try:
            path,q,parts=self.route()
            if path=='/api/platform/health':return self.send_json(200,{'status':'ok','time':now(),'users':STORE.users()})
            u=self.user()
            if path=='/api/platform/me':return self.send_json(200,u)
            if path=='/api/platform/watchlists':return self.send_json(200,{'watchlists':STORE.watchlists(u['id'])})
            if path=='/api/platform/incidents':return self.send_json(200,{'incidents':STORE.incidents((q.get('query')or[''])[0],(q.get('severity')or[''])[0],(q.get('limit')or[100])[0])})
            if path=='/api/platform/alerts':return self.send_json(200,{'alerts':STORE.alerts(u['id'])})
            if len(parts)>=4 and parts[:3]==['api','platform','incidents']:
                iid=parts[3]
                if len(parts)==4:return self.send_json(200,STORE.incident(iid))
                if parts[4]=='timeline':return self.send_json(200,{'timeline':STORE.timeline(iid)})
                if parts[4]=='graph':return self.send_json(200,STORE.graph(iid))
            self.send_json(404,{'error':'not found'})
        except PermissionError as e:self.send_json(401,{'error':str(e)})
        except KeyError as e:self.send_json(404,{'error':str(e)})
        except Exception as e:self.send_json(400,{'error':str(e)})
    def do_POST(self):
        try:
            path,q,parts=self.route();p=self.body()
            if path=='/api/platform/users':
                secret=os.getenv('AURORA_BOOTSTRAP_SECRET');supplied=self.headers.get('X-Bootstrap-Secret','')
                if STORE.users()>0 and (not secret or supplied!=secret):raise PermissionError('bootstrap secret required')
                u,t=STORE.create_user(p.get('email',''),p.get('role','analyst'));return self.send_json(201,{'user':u,'token':t,'warning':'store this token now'})
            u=self.user()
            if path=='/api/platform/watchlists':return self.send_json(201,STORE.add_watchlist(u['id'],p))
            if path=='/api/platform/ingest':return self.send_json(200,STORE.ingest(p))
            if path=='/api/platform/refresh':
                from app import AGGREGATOR
                return self.send_json(200,STORE.ingest(AGGREGATOR.collect(force=True)))
            if len(parts)==5 and parts[:3]==['api','platform','incidents'] and parts[4]=='notes':return self.send_json(201,STORE.add_note(parts[3],u['id'],p.get('body','')))
            self.send_json(404,{'error':'not found'})
        except PermissionError as e:self.send_json(401,{'error':str(e)})
        except Exception as e:self.send_json(400,{'error':str(e)})
    def do_DELETE(self):
        try:
            path,q,parts=self.route();u=self.user()
            if len(parts)==4 and parts[:3]==['api','platform','watchlists']:return self.send_json(200,{'deleted':STORE.delete_watchlist(u['id'],parts[3])})
            self.send_json(404,{'error':'not found'})
        except PermissionError as e:self.send_json(401,{'error':str(e)})
    def log_message(self,*a):
        if os.getenv('AURORA_QUIET')!='1':super().log_message(*a)

def main():
    p=argparse.ArgumentParser();p.add_argument('--host',default='127.0.0.1');p.add_argument('--port',type=int,default=8090);p.add_argument('--database');a=p.parse_args();global STORE
    if a.database:STORE=Store(a.database)
    s=ThreadingHTTPServer((a.host,a.port),Handler);print(f'AURORA platform at http://{a.host}:{a.port}')
    try:s.serve_forever()
    except KeyboardInterrupt:pass
    finally:s.server_close()
if __name__=='__main__':main()
