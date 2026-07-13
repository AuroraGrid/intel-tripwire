from __future__ import annotations
import argparse,json,os,urllib.parse
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from delivery import deliver_pending
from feeds import json_feed,rss_feed
from operations import Operations
from storage import Store,now

STORE=Store(os.getenv('DATABASE_PATH','data/aurora-live.db'));OPS=Operations(STORE)

def bearer(h):
    a=h.get('Authorization','');return a[7:].strip() if a.lower().startswith('bearer ') else ''

class Handler(BaseHTTPRequestHandler):
    def send_bytes(self,code,body,ctype):
        self.send_response(code);self.send_header('Content-Type',ctype);self.send_header('Cache-Control','no-store');self.send_header('Access-Control-Allow-Origin',os.getenv('AURORA_CORS_ORIGIN','*'));self.send_header('Access-Control-Allow-Headers','Authorization,Content-Type,X-Bootstrap-Secret');self.send_header('Access-Control-Allow-Methods','GET,POST,DELETE,OPTIONS');self.send_header('Content-Length',str(len(body)));self.end_headers()
        if body:self.wfile.write(body)
    def send_json(self,code,data):self.send_bytes(code,json.dumps(data,ensure_ascii=False).encode(),'application/json; charset=utf-8')
    def body(self):
        n=int(self.headers.get('Content-Length','0'))
        if n>1000000:raise ValueError('request body too large')
        return json.loads(self.rfile.read(n) or b'{}')
    def user(self):
        u=STORE.auth(bearer(self.headers))
        if not u:raise PermissionError('valid bearer token required')
        return u
    def role(self,u,*roles):
        if u.get('role') not in roles:raise PermissionError('insufficient role')
    def route(self):
        p=urllib.parse.urlparse(self.path);return p.path,urllib.parse.parse_qs(p.query),[x for x in p.path.split('/') if x]
    def base(self):return f"{self.headers.get('X-Forwarded-Proto','http')}://{self.headers.get('X-Forwarded-Host') or self.headers.get('Host','127.0.0.1')}"
    def do_OPTIONS(self):self.send_bytes(204,b'','text/plain')
    def do_GET(self):
        try:
            path,q,parts=self.route()
            if path=='/api/platform/health':return self.send_json(200,{'status':'ok','time':now(),'users':STORE.users()})
            u=self.user();uid=u['id'];v=lambda n,d='':(q.get(n)or[d])[0]
            if path=='/api/platform/me':return self.send_json(200,u)
            if path=='/api/platform/stats':return self.send_json(200,OPS.stats(uid))
            if path=='/api/platform/watchlists':return self.send_json(200,{'watchlists':STORE.watchlists(uid)})
            if path=='/api/platform/alerts':return self.send_json(200,{'alerts':OPS.alerts(uid,v('unacknowledged','0').lower() in {'1','true','yes'})})
            if path=='/api/platform/webhooks':return self.send_json(200,{'webhooks':OPS.webhooks(uid)})
            if path=='/api/platform/cases':return self.send_json(200,{'cases':OPS.cases(uid)})
            if path=='/api/platform/incidents':return self.send_json(200,{'incidents':OPS.incidents(v('query'),v('severity'),v('category'),v('status'),v('grade'),v('action'),int(v('min_confidence','0')),int(v('limit','100')),int(v('offset','0')))})
            if path in {'/api/platform/feed.json','/api/platform/feed.rss'}:
                items=OPS.incidents(limit=100)
                return self.send_bytes(200,json_feed(items,self.base()) if path.endswith('.json') else rss_feed(items,self.base()),'application/feed+json; charset=utf-8' if path.endswith('.json') else 'application/rss+xml; charset=utf-8')
            if len(parts)>=4 and parts[:3]==['api','platform','incidents']:
                iid=parts[3]
                if len(parts)==4:return self.send_json(200,STORE.incident(iid))
                if parts[4]=='timeline':return self.send_json(200,{'timeline':STORE.timeline(iid)})
                if parts[4]=='graph':return self.send_json(200,STORE.graph(iid))
            if len(parts)==4 and parts[:3]==['api','platform','cases']:return self.send_json(200,OPS.case(uid,parts[3]))
            return self.send_json(404,{'error':'not found'})
        except PermissionError as e:return self.send_json(401,{'error':str(e)})
        except KeyError as e:return self.send_json(404,{'error':str(e)})
        except Exception as e:return self.send_json(400,{'error':str(e)})
    def do_POST(self):
        try:
            path,q,parts=self.route();p=self.body()
            if path=='/api/platform/users':
                secret=os.getenv('AURORA_BOOTSTRAP_SECRET');supplied=self.headers.get('X-Bootstrap-Secret','')
                if STORE.users()>0 and (not secret or supplied!=secret):raise PermissionError('bootstrap secret required')
                u,t=STORE.create_user(p.get('email',''),p.get('role','analyst'));return self.send_json(201,{'user':u,'token':t,'warning':'store this token now'})
            u=self.user();uid=u['id']
            if path=='/api/platform/watchlists':return self.send_json(201,STORE.add_watchlist(uid,p))
            if path=='/api/platform/webhooks':return self.send_json(201,OPS.add_webhook(uid,p))
            if path=='/api/platform/cases':return self.send_json(201,OPS.create_case(uid,p))
            if path=='/api/platform/ingest':
                self.role(u,'admin');before={a['id'] for a in STORE.alerts(uid)};result=STORE.ingest(p)
                for a in STORE.alerts(uid):
                    if a['id'] not in before:OPS.queue_deliveries(uid,a['id'])
                return self.send_json(200,result)
            if path=='/api/platform/refresh':
                self.role(u,'admin');from app import AGGREGATOR;return self.send_json(200,STORE.ingest(AGGREGATOR.collect(force=True)))
            if path=='/api/platform/deliveries/run':self.role(u,'analyst','admin');return self.send_json(200,deliver_pending(OPS,uid))
            if len(parts)==5 and parts[:3]==['api','platform','incidents'] and parts[4]=='notes':self.role(u,'analyst','admin');return self.send_json(201,STORE.add_note(parts[3],uid,p.get('body','')))
            if len(parts)==5 and parts[:3]==['api','platform','alerts'] and parts[4]=='ack':return self.send_json(200,OPS.acknowledge_alert(uid,parts[3]))
            if len(parts)==5 and parts[:3]==['api','platform','cases'] and parts[4]=='incidents':return self.send_json(200,OPS.add_case_incident(uid,parts[3],p.get('incident_id','')))
            if len(parts)==5 and parts[:3]==['api','platform','cases'] and parts[4]=='notes':return self.send_json(201,OPS.add_case_note(uid,parts[3],p.get('body','')))
            return self.send_json(404,{'error':'not found'})
        except PermissionError as e:return self.send_json(401,{'error':str(e)})
        except KeyError as e:return self.send_json(404,{'error':str(e)})
        except Exception as e:return self.send_json(400,{'error':str(e)})
    def do_DELETE(self):
        try:
            path,q,parts=self.route();u=self.user();uid=u['id']
            if len(parts)==4 and parts[:3]==['api','platform','watchlists']:return self.send_json(200,{'deleted':STORE.delete_watchlist(uid,parts[3])})
            if len(parts)==4 and parts[:3]==['api','platform','webhooks']:return self.send_json(200,{'deleted':OPS.delete_webhook(uid,parts[3])})
            if len(parts)==6 and parts[:3]==['api','platform','cases'] and parts[4]=='incidents':return self.send_json(200,{'deleted':OPS.remove_case_incident(uid,parts[3],parts[5])})
            return self.send_json(404,{'error':'not found'})
        except PermissionError as e:return self.send_json(401,{'error':str(e)})
        except KeyError as e:return self.send_json(404,{'error':str(e)})
        except Exception as e:return self.send_json(400,{'error':str(e)})
    def log_message(self,*a):
        if os.getenv('AURORA_QUIET')!='1':super().log_message(*a)

def main():
    p=argparse.ArgumentParser();p.add_argument('--host',default='127.0.0.1');p.add_argument('--port',type=int,default=8090);p.add_argument('--database');a=p.parse_args();global STORE,OPS
    if a.database:STORE=Store(a.database);OPS=Operations(STORE)
    s=ThreadingHTTPServer((a.host,a.port),Handler);print(f'AURORA platform at http://{a.host}:{a.port}')
    try:s.serve_forever()
    except KeyboardInterrupt:pass
    finally:s.server_close()
if __name__=='__main__':main()
