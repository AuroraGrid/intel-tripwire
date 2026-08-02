from __future__ import annotations

import ipaddress
import json
import os
import re
import secrets
import sys
import threading
import time
import urllib.parse
import uuid
from pathlib import Path

from delivery import deliver_pending
from feeds import json_feed, rss_feed
from operations import Operations
from storage import Store, now

ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "static"
RID_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
STATUS = {200:"OK",201:"Created",204:"No Content",400:"Bad Request",401:"Unauthorized",403:"Forbidden",404:"Not Found",405:"Method Not Allowed",413:"Payload Too Large",429:"Too Many Requests",500:"Internal Server Error"}

class HTTPError(Exception):
    def __init__(self,status,code,message,headers=None):
        super().__init__(message); self.status=status; self.code=code; self.message=message; self.headers=headers or []

class RateLimiter:
    """Compatibility wrapper; prefers durable shared buckets when a store is attached."""
    def __init__(self,max_entries=10000,store=None):
        from durable_rate_limit import DurableRateLimiter
        self._impl=DurableRateLimiter(store=store,max_entries=max_entries)
    def check(self,key,limit,window):
        return self._impl.check(key,limit,window)

def csv_env(name,default=""):
    return [x.strip() for x in os.getenv(name,default).split(",") if x.strip()]

def configured_store(database=None):
    return Store(database or os.getenv("DATABASE_URL") or os.getenv("DATABASE_PATH","data/aurora-live.db"))

class PlatformApplication:
    def __init__(self,store=None):
        self.store=store or configured_store(); self.ops=Operations(self.store)
        self.max_body=max(1024,int(os.getenv("AURORA_MAX_BODY_BYTES","1000000")))
        self.allowed_hosts={x.lower() for x in csv_env("AURORA_ALLOWED_HOSTS","localhost,127.0.0.1,::1")}
        self.cors=os.getenv("AURORA_CORS_ORIGIN","").strip(); self.wildcard=os.getenv("AURORA_ALLOW_WILDCARD_CORS","0")=="1"
        self.proxies=[]
        for value in csv_env("AURORA_TRUSTED_PROXIES"):
            try:self.proxies.append(ipaddress.ip_network(value,strict=False))
            except ValueError as exc:raise RuntimeError(f"invalid trusted proxy: {value}") from exc
        self.auth_limit=max(0,int(os.getenv("AURORA_AUTH_RATE_LIMIT","10"))); self.write_limit=max(0,int(os.getenv("AURORA_WRITE_RATE_LIMIT","120")))
        self.window=max(1,int(os.getenv("AURORA_RATE_WINDOW_SECONDS","60")))
        # Shared across Gunicorn workers when backed by the application database.
        self.limiter=RateLimiter(int(os.getenv("AURORA_RATE_MAX_CLIENTS","10000")), store=self.store)
    def trusted(self,address):
        try:ip=ipaddress.ip_address(address)
        except ValueError:return False
        return any(ip in net for net in self.proxies)
    def client_ip(self,e):
        remote=str(e.get("REMOTE_ADDR") or "unknown")
        if self.trusted(remote):
            first=str(e.get("HTTP_X_FORWARDED_FOR") or "").split(",",1)[0].strip()
            try:
                if first:ipaddress.ip_address(first); return first
            except ValueError:pass
        return remote
    def origin(self,e):
        remote=str(e.get("REMOTE_ADDR") or ""); scheme=str(e.get("wsgi.url_scheme") or "http").lower(); host=str(e.get("HTTP_HOST") or e.get("SERVER_NAME") or "")
        if self.trusted(remote):
            proto=str(e.get("HTTP_X_FORWARDED_PROTO") or "").split(",",1)[0].strip().lower(); forwarded=str(e.get("HTTP_X_FORWARDED_HOST") or "").split(",",1)[0].strip()
            if proto in {"http","https"}:scheme=proto
            if forwarded:host=forwarded
        check=host.lower().strip()
        if check.startswith("[") and "]" in check:check=check[1:check.index("]")]
        elif check.count(":")==1:check=check.rsplit(":",1)[0]
        if not check or not self._host_allowed(check):raise HTTPError(400,"invalid_host","request Host is not allowed")
        return f"{scheme}://{host}"
    def _host_allowed(self, host):
        if "*" in self.allowed_hosts or host in self.allowed_hosts:
            return True
        # Open beta / public VPS: allow any Host when open access is enabled
        try:
            if self.store.open_access_enabled():
                return True
        except Exception:
            pass
        # Friend-share tunnels: random.trycloudflare.com / random.loca.lt
        for suffix, markers in (
            (".trycloudflare.com", ("trycloudflare.com", "*.trycloudflare.com")),
            (".loca.lt", ("loca.lt", "*.loca.lt")),
            (".localtunnel.me", ("localtunnel.me", "*.localtunnel.me")),
            (".vercel.app", ("vercel.app", "*.vercel.app")),
            (".onrender.com", ("onrender.com", "*.onrender.com")),
        ):
            if host.endswith(suffix) and any(m in self.allowed_hosts for m in markers):
                return True
        if host.endswith(".onrender.com"):
            return True
        # Public IP:port (OCI free tier)
        if host.replace(".", "").replace(":", "").isdigit() or (
            host.count(".") == 3 and ":" in host
        ):
            return True
        return False
    def cors_headers(self,e):
        origin=str(e.get("HTTP_ORIGIN") or "").strip(); headers=[("Vary","Origin")]
        if not origin:return headers
        cors=(self.cors or "").strip()
        # Open beta / friend share: never block browser Origin from public tunnels.
        open_access=False
        try:open_access=self.store.open_access_enabled()
        except Exception:open_access=False
        host_part=origin.split("://",1)[-1].split("/",1)[0].lower()
        tunnel_ok=(
            host_part.endswith(".trycloudflare.com")
            or host_part.endswith(".loca.lt")
            or host_part.endswith(".localtunnel.me")
            or host_part.endswith(".vercel.app")
            or host_part in {"127.0.0.1:8090","localhost:8090"}
            or host_part.startswith("127.0.0.1:")
            or host_part.startswith("localhost:")
        )
        # Open beta / public IP hosting (OCI free tier): never block browser Origin.
        if open_access or cors=="*":
            return headers+[("Access-Control-Allow-Origin",origin),("Access-Control-Allow-Credentials","true")]
        if open_access and tunnel_ok:
            return headers+[("Access-Control-Allow-Origin",origin),("Access-Control-Allow-Credentials","true")]
        if origin==cors:
            return headers+[("Access-Control-Allow-Origin",origin)]
        if self.wildcard and cors=="*":
            return headers+[("Access-Control-Allow-Origin","*")]
        # Configured tunnel wildcards in AURORA_CORS_ORIGIN (e.g. https://*.trycloudflare.com)
        for suffix in (".trycloudflare.com",".loca.lt",".localtunnel.me",".vercel.app"):
            if host_part.endswith(suffix) and (
                suffix.lstrip(".") in cors or f"*{suffix}" in cors or cors.endswith(suffix) or "*.trycloudflare.com" in cors
            ):
                return headers+[("Access-Control-Allow-Origin",origin)]
        if tunnel_ok and ("trycloudflare" in cors or "loca.lt" in cors or "*" in cors):
            return headers+[("Access-Control-Allow-Origin",origin)]
        raise HTTPError(403,"cors_origin_denied","request Origin is not allowed")
    def security_headers(self,e,rid,cache="no-store"):
        return [("Cache-Control",cache),("X-Request-ID",rid),("X-Content-Type-Options","nosniff"),("X-Frame-Options","DENY"),("Referrer-Policy","no-referrer"),("Permissions-Policy","camera=(), microphone=(), geolocation=()"),("Cross-Origin-Opener-Policy","same-origin"),("Content-Security-Policy","default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'"),*self.cors_headers(e)]
    def user(self,e):
        auth=str(e.get("HTTP_AUTHORIZATION") or ""); token=auth[7:].strip() if auth.lower().startswith("bearer ") else ""
        if token:
            user=self.store.auth(token)
            if user:return user
        # Open beta: no bearer required — act as first workspace user.
        if self.store.open_access_enabled():
            row=self.store.first_workspace_user()
            if row:
                from identity import CURRENT_WORKSPACE
                CURRENT_WORKSPACE.set(row["workspace_id"])
                return {
                    "id": row["id"],
                    "email": row["email"],
                    "role": "admin" if row.get("workspace_role") == "owner" else row.get("role"),
                    "workspace_id": row["workspace_id"],
                    "workspace_role": row.get("workspace_role"),
                    "permissions": self.store.identity.permissions(row.get("workspace_role") or "admin"),
                    "open_access": True,
                }
        raise HTTPError(401,"unauthorized","valid bearer token required",[("WWW-Authenticate","Bearer")])
    @staticmethod
    def role(user,*roles):
        if user.get("role") not in roles:raise HTTPError(403,"forbidden","insufficient role")
    def body(self,e):
        try:length=int(str(e.get("CONTENT_LENGTH") or "0"))
        except ValueError as exc:raise HTTPError(400,"invalid_content_length","invalid Content-Length") from exc
        if length<0:raise HTTPError(400,"invalid_content_length","invalid Content-Length")
        if length>self.max_body:raise HTTPError(413,"payload_too_large","request body too large")
        raw=e["wsgi.input"].read(length) if length else b""
        if not raw:return {}
        try:value=json.loads(raw)
        except Exception as exc:raise HTTPError(400,"invalid_json","request body must contain valid JSON") from exc
        if not isinstance(value,dict):raise HTTPError(400,"invalid_json_type","request JSON must be an object")
        return value
    def rate_headers(self,e,method,path):
        if method not in {"POST","DELETE"}:return []
        limit=self.auth_limit if path in {"/api/platform/users","/api/platform/login"} else self.write_limit
        return self.limiter.check(("auth:" if path in {"/api/platform/users","/api/platform/login"} else "write:")+self.client_ip(e),limit,self.window) or []
    @staticmethod
    def json(value):return json.dumps(value,ensure_ascii=False,separators=(",",":")).encode()
    def dispatch(self,e):
        method=str(e.get("REQUEST_METHOD") or "GET").upper(); path=str(e.get("PATH_INFO") or "/"); parts=[x for x in path.split("/") if x]; q=urllib.parse.parse_qs(str(e.get("QUERY_STRING") or ""),keep_blank_values=True); v=lambda n,d="":(q.get(n)or[d])[0]; base=self.origin(e); rate=self.rate_headers(e,method,path)
        if method=="OPTIONS":return 204,b"","text/plain","no-store",rate+[("Access-Control-Allow-Headers","Authorization,Content-Type,X-Bootstrap-Secret,X-Request-ID"),("Access-Control-Allow-Methods","GET,POST,DELETE,OPTIONS"),("Access-Control-Max-Age","600")]
        if method=="GET" and path in {"/platform","/platform/"}:
            try:return 200,(STATIC/"platform.html").read_bytes(),"text/html; charset=utf-8","public, max-age=300",rate
            except FileNotFoundError:raise HTTPError(404,"not_found","dashboard not found")
        if method=="GET" and path=="/api/platform/live":return 200,self.json({"status":"alive","time":now(),"open_access":self.store.open_access_enabled()}),"application/json; charset=utf-8","no-store",rate
        if method=="GET" and path in {"/api/platform/ready","/api/platform/health"}:
            status="ready" if path.endswith("ready") else "ok"; return 200,self.json({"status":status,"time":now(),"users":self.store.users(),"database":self.store.backend,"open_access":self.store.open_access_enabled()}),"application/json; charset=utf-8","no-store",rate
        if method=="GET" and path=="/api/platform/open-session":
            if not self.store.open_access_enabled():
                raise HTTPError(403,"forbidden","open access disabled")
            result=self.store.issue_open_session(name="open-access")
            if not result:
                raise HTTPError(503,"unavailable","no workspace user configured")
            return 200,self.json(result),"application/json; charset=utf-8","no-store",rate
        if method=="POST" and path=="/api/platform/login":
            p=self.body(e)
            result=self.store.login_with_password(p.get("password",""))
            if not result:
                raise HTTPError(401,"unauthorized","invalid password")
            return 200,self.json(result),"application/json; charset=utf-8","no-store",rate
        if method=="POST" and path=="/api/platform/users":
            p=self.body(e); secret=os.getenv("AURORA_BOOTSTRAP_SECRET","") or ""; supplied=str(e.get("HTTP_X_BOOTSTRAP_SECRET") or "")
            # Bootstrap secret is required for every user create, including the first administrator.
            if not secret or not secrets.compare_digest(secret, supplied):
                raise HTTPError(403,"forbidden","valid bootstrap secret required")
            user,token=self.store.create_user(p.get("email",""),p.get("role","analyst")); return 201,self.json({"user":user,"token":token,"warning":"store this token now"}),"application/json; charset=utf-8","no-store",rate
        user=self.user(e); uid=user["id"]
        if method=="GET":
            if path=="/api/platform/me":data=user
            elif path=="/api/platform/stats":data=self.ops.stats(uid)
            elif path=="/api/platform/watchlists":data={"watchlists":self.store.watchlists(uid)}
            elif path=="/api/platform/alerts":data={"alerts":self.ops.alerts(uid,v("unacknowledged","0").lower() in {"1","true","yes"})}
            elif path=="/api/platform/webhooks":data={"webhooks":self.ops.webhooks(uid)}
            elif path=="/api/platform/cases":data={"cases":self.ops.cases(uid)}
            elif path=="/api/platform/incidents":data={"incidents":self.ops.incidents(v("query"),v("severity"),v("category"),v("status"),v("grade"),v("action"),int(v("min_confidence","0")),int(v("limit","100")),int(v("offset","0")))}
            elif path in {"/api/platform/feed.json","/api/platform/feed.rss"}:
                items=self.ops.incidents(limit=100); feed=json_feed(items,base) if path.endswith(".json") else rss_feed(items,base); ctype="application/feed+json; charset=utf-8" if path.endswith(".json") else "application/rss+xml; charset=utf-8"; return 200,feed,ctype,"no-store",rate
            elif len(parts)>=4 and parts[:3]==["api","platform","incidents"]:
                iid=parts[3]
                if len(parts)==4:data=self.store.incident(iid)
                elif len(parts)==5 and parts[4]=="timeline":data={"timeline":self.store.timeline(iid)}
                elif len(parts)==5 and parts[4]=="graph":data=self.store.graph(iid)
                else:raise HTTPError(404,"not_found","route not found")
            elif len(parts)==4 and parts[:3]==["api","platform","cases"]:data=self.ops.case(uid,parts[3])
            else:raise HTTPError(404,"not_found","route not found")
            return 200,self.json(data),"application/json; charset=utf-8","no-store",rate
        if method=="POST":
            p=self.body(e)
            if path=="/api/platform/watchlists":status,data=201,self.store.add_watchlist(uid,p)
            elif path=="/api/platform/webhooks":status,data=201,self.ops.add_webhook(uid,p)
            elif path=="/api/platform/cases":status,data=201,self.ops.create_case(uid,p)
            elif path=="/api/platform/ingest":
                self.role(user,"admin"); before={a["id"] for a in self.store.alerts(uid)}; data=self.store.ingest(p)
                for alert in self.store.alerts(uid):
                    if alert["id"] not in before:self.ops.queue_deliveries(uid,alert["id"])
                status=200
            elif path=="/api/platform/refresh":self.role(user,"admin"); from app import AGGREGATOR; status,data=200,self.store.ingest(AGGREGATOR.collect(force=True))
            elif path=="/api/platform/deliveries/run":self.role(user,"analyst","admin"); status,data=200,deliver_pending(self.ops,uid)
            elif len(parts)==5 and parts[:3]==["api","platform","incidents"] and parts[4]=="notes":self.role(user,"analyst","admin"); status,data=201,self.store.add_note(parts[3],uid,p.get("body",""))
            elif len(parts)==5 and parts[:3]==["api","platform","alerts"] and parts[4]=="ack":status,data=200,self.ops.acknowledge_alert(uid,parts[3])
            elif len(parts)==5 and parts[:3]==["api","platform","cases"] and parts[4]=="incidents":status,data=200,self.ops.add_case_incident(uid,parts[3],p.get("incident_id",""))
            elif len(parts)==5 and parts[:3]==["api","platform","cases"] and parts[4]=="notes":status,data=201,self.ops.add_case_note(uid,parts[3],p.get("body",""))
            else:raise HTTPError(404,"not_found","route not found")
            return status,self.json(data),"application/json; charset=utf-8","no-store",rate
        if method=="DELETE":
            if len(parts)==4 and parts[:3]==["api","platform","watchlists"]:data={"deleted":self.store.delete_watchlist(uid,parts[3])}
            elif len(parts)==4 and parts[:3]==["api","platform","webhooks"]:data={"deleted":self.ops.delete_webhook(uid,parts[3])}
            elif len(parts)==6 and parts[:3]==["api","platform","cases"] and parts[4]=="incidents":data={"deleted":self.ops.remove_case_incident(uid,parts[3],parts[5])}
            else:raise HTTPError(404,"not_found","route not found")
            return 200,self.json(data),"application/json; charset=utf-8","no-store",rate
        raise HTTPError(405,"method_not_allowed","method not allowed",[("Allow","GET,POST,DELETE,OPTIONS")])
    def __call__(self,e,start_response):
        supplied=str(e.get("HTTP_X_REQUEST_ID") or ""); rid=supplied if RID_RE.fullmatch(supplied) else uuid.uuid4().hex
        try:status,body,ctype,cache,extra=self.dispatch(e); headers=[("Content-Type",ctype),*self.security_headers(e,rid,cache),*extra]
        except HTTPError as exc:
            status=exc.status; body=self.json({"error":{"code":exc.code,"message":exc.message},"request_id":rid})
            try:headers=[("Content-Type","application/json; charset=utf-8"),*self.security_headers(e,rid),*exc.headers]
            except HTTPError:headers=[("Content-Type","application/json; charset=utf-8"),("Cache-Control","no-store"),("X-Request-ID",rid),*exc.headers]
        except KeyError as exc:status=404; body=self.json({"error":{"code":"not_found","message":str(exc).strip("'") or "resource not found"},"request_id":rid}); headers=[("Content-Type","application/json; charset=utf-8"),*self.security_headers(e,rid)]
        except ValueError as exc:status=400; body=self.json({"error":{"code":"bad_request","message":str(exc)},"request_id":rid}); headers=[("Content-Type","application/json; charset=utf-8"),*self.security_headers(e,rid)]
        except Exception as exc:status=500; body=self.json({"error":{"code":"internal_error","message":"internal server error"},"request_id":rid}); headers=[("Content-Type","application/json; charset=utf-8"),("Cache-Control","no-store"),("X-Request-ID",rid)]; print(json.dumps({"level":"error","request_id":rid,"error":type(exc).__name__,"message":str(exc)}),file=sys.stderr,flush=True)
        headers.append(("Content-Length",str(len(body)))); start_response(f"{status} {STATUS.get(status,'Unknown')}",headers); return [body]

def create_application(database=None,store=None):return PlatformApplication(store or configured_store(database))

application=create_application()
