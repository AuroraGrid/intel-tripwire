from __future__ import annotations
import hashlib,json,os,secrets
from datetime import datetime,timezone
from pathlib import Path
from database import Database,DatabaseIntegrityError

def now():return datetime.now(timezone.utc).isoformat().replace('+00:00','Z')
def sid(*x):return hashlib.sha256('|'.join(map(str,x)).encode()).hexdigest()[:24]
def dumps(x):return json.dumps(x,ensure_ascii=False,separators=(',',':'),sort_keys=True)
def loads(x,d):
 try:return json.loads(x) if x else d
 except (json.JSONDecodeError,TypeError):return d

class Store:
 def __init__(self,path='aurora-live.db',database_url=None):
  self.database=Database(database_url or os.getenv('DATABASE_URL') or str(path));self.backend=self.database.backend;self.path=self.database.path;self.init()
 def db(self):return self.database.connection()
 def init(self):
  tid='INTEGER PRIMARY KEY AUTOINCREMENT' if self.backend=='sqlite' else 'BIGSERIAL PRIMARY KEY'
  with self.db() as c:c.executescript(f'''
  CREATE TABLE IF NOT EXISTS users(id TEXT PRIMARY KEY,email TEXT UNIQUE,token_hash TEXT UNIQUE,role TEXT,created_at TEXT);
  CREATE TABLE IF NOT EXISTS watchlists(id TEXT PRIMARY KEY,user_id TEXT REFERENCES users(id) ON DELETE CASCADE,name TEXT,query TEXT,categories TEXT,severities TEXT,min_confidence INTEGER,created_at TEXT);
  CREATE TABLE IF NOT EXISTS incidents(id TEXT PRIMARY KEY,title TEXT,category TEXT,severity TEXT,status TEXT,grade TEXT,confidence INTEGER,action TEXT,first_seen TEXT,last_seen TEXT,payload TEXT);
  CREATE TABLE IF NOT EXISTS evidence(id TEXT PRIMARY KEY,incident_id TEXT REFERENCES incidents(id) ON DELETE CASCADE,source_family TEXT,title TEXT,url TEXT,official INTEGER,published_at TEXT,payload TEXT);
  CREATE TABLE IF NOT EXISTS timeline(id {tid},incident_id TEXT REFERENCES incidents(id) ON DELETE CASCADE,event_type TEXT,summary TEXT,created_at TEXT,payload TEXT);
  CREATE TABLE IF NOT EXISTS alerts(id TEXT PRIMARY KEY,user_id TEXT,watchlist_id TEXT,incident_id TEXT,created_at TEXT,UNIQUE(watchlist_id,incident_id));
  CREATE TABLE IF NOT EXISTS notes(id TEXT PRIMARY KEY,incident_id TEXT,user_id TEXT,body TEXT,created_at TEXT);
  ''')
 def create_user(self,email,role='analyst'):
  email=email.strip().lower()
  if '@' not in email:raise ValueError('valid email required')
  if role not in {'viewer','analyst','admin'}:raise ValueError('invalid role')
  token=secrets.token_urlsafe(32);uid=sid('user',email);created=now()
  with self.db() as c:c.execute('INSERT INTO users(id,email,token_hash,role,created_at) VALUES(?,?,?,?,?)',(uid,email,hashlib.sha256(token.encode()).hexdigest(),role,created))
  return {'id':uid,'email':email,'role':role,'created_at':created},token
 def auth(self,token):
  if not token:return None
  with self.db() as c:r=c.execute('SELECT id,email,role,created_at FROM users WHERE token_hash=?',(hashlib.sha256(token.encode()).hexdigest(),)).fetchone()
  return dict(r) if r else None
 def users(self):
  with self.db() as c:return c.execute('SELECT COUNT(*) FROM users').fetchone()[0]
 def add_watchlist(self,user,p):
  name=str(p.get('name','')).strip();query=str(p.get('query','')).strip();conf=max(0,min(100,int(p.get('min_confidence',0))))
  if not name:raise ValueError('name required')
  cats=sorted(set(p.get('categories') or []));sevs=sorted(set(p.get('severities') or []));wid=sid('watch',user,name,now(),secrets.token_hex(4));created=now()
  with self.db() as c:c.execute('INSERT INTO watchlists(id,user_id,name,query,categories,severities,min_confidence,created_at) VALUES(?,?,?,?,?,?,?,?)',(wid,user,name,query,dumps(cats),dumps(sevs),conf,created))
  return next(x for x in self.watchlists(user) if x['id']==wid)
 def watchlists(self,user):
  with self.db() as c:rows=c.execute('SELECT * FROM watchlists WHERE user_id=? ORDER BY created_at DESC',(user,)).fetchall()
  out=[]
  for r in rows:
   x=dict(r);x['categories']=loads(x.pop('categories'),[]);x['severities']=loads(x.pop('severities'),[]);out.append(x)
  return out
 def delete_watchlist(self,user,wid):
  with self.db() as c:return c.execute('DELETE FROM watchlists WHERE user_id=? AND id=?',(user,wid)).rowcount>0
 def ingest(self,payload):
  created=updated=0;ids=[]
  for e in payload.get('events',[]):
   iid=str(e.get('id') or e.get('claim_id') or sid(e.get('title'),e.get('published_at')));ids.append(iid)
   with self.db() as c:old=c.execute('SELECT payload,first_seen FROM incidents WHERE id=?',(iid,)).fetchone()
   changed=bool(old and any(loads(old['payload'],{}).get(k)!=e.get(k) for k in ('title','severity','k_align_status','confidence_score','action_state')));first=old['first_seen'] if old else now();seen=now()
   with self.db() as c:
    c.execute('''INSERT INTO incidents(id,title,category,severity,status,grade,confidence,action,first_seen,last_seen,payload) VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET title=excluded.title,category=excluded.category,severity=excluded.severity,status=excluded.status,grade=excluded.grade,confidence=excluded.confidence,action=excluded.action,last_seen=excluded.last_seen,payload=excluded.payload''',(iid,e.get('title','Untitled'),e.get('category','world'),e.get('severity','low'),e.get('k_align_status','NOT_PROVEN'),e.get('confidence_grade','G1'),int(e.get('confidence_score',0)),e.get('action_state','MONITOR'),first,seen,dumps(e)))
    for v in e.get('evidence',[]):
     eid=str(v.get('id') or sid(iid,v.get('url'),v.get('title')));c.execute('''INSERT INTO evidence(id,incident_id,source_family,title,url,official,published_at,payload) VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload''',(eid,iid,v.get('source_family','unknown'),v.get('title',''),v.get('url',''),int(bool(v.get('official'))),v.get('published_at'),dumps(v)))
    if not old:c.execute('INSERT INTO timeline(incident_id,event_type,summary,created_at,payload) VALUES(?,?,?,?,?)',(iid,'DETECTED','Incident first detected',seen,'{}'))
    elif changed:c.execute('INSERT INTO timeline(incident_id,event_type,summary,created_at,payload) VALUES(?,?,?,?,?)',(iid,'ASSESSMENT_CHANGED','Material assessment changed',seen,dumps(e)))
   created+=int(not old);updated+=int(changed)
  return {'ingested':len(ids),'created':created,'updated':updated,'alerts_created':self.match(ids),'incident_ids':ids}
 def incident(self,iid,with_evidence=True):
  with self.db() as c:r=c.execute('SELECT * FROM incidents WHERE id=?',(iid,)).fetchone()
  if not r:raise KeyError('incident not found')
  x=dict(r);x['payload']=loads(x.pop('payload'),{})
  if with_evidence:
   with self.db() as c:rows=c.execute('SELECT * FROM evidence WHERE incident_id=? ORDER BY published_at DESC',(iid,)).fetchall()
   x['evidence']=[dict(v) for v in rows]
  return x
 def incidents(self,q='',severity='',limit=100):
  sql='SELECT id FROM incidents WHERE 1=1';args=[]
  if q:sql+=' AND lower(title) LIKE ?';args.append('%'+q.lower()+'%')
  if severity:sql+=' AND severity=?';args.append(severity)
  sql+=' ORDER BY last_seen DESC LIMIT ?';args.append(max(1,min(500,int(limit))))
  with self.db() as c:rows=c.execute(sql,args).fetchall()
  return [self.incident(r['id'],False) for r in rows]
 def timeline(self,iid):
  with self.db() as c:return [dict(r) for r in c.execute('SELECT * FROM timeline WHERE incident_id=? ORDER BY id',(iid,)).fetchall()]
 def graph(self,iid):
  i=self.incident(iid);nodes=[{'id':iid,'type':'incident','label':i['title']}];edges=[];fam=set()
  for e in i['evidence']:
   f='source:'+e['source_family'];n='evidence:'+e['id']
   if f not in fam:nodes.append({'id':f,'type':'source','label':e['source_family']});fam.add(f)
   nodes.append({'id':n,'type':'evidence','label':e['title'],'url':e['url'],'official':bool(e['official'])});edges += [{'from':f,'to':n,'type':'PUBLISHED'},{'from':n,'to':iid,'type':'SUPPORTS'}]
  return {'incident_id':iid,'nodes':nodes,'edges':edges}
 def add_note(self,iid,user,body):
  body=body.strip()
  if not body:raise ValueError('body required')
  nid=sid('note',iid,user,now(),secrets.token_hex(4));created=now()
  with self.db() as c:c.execute('INSERT INTO notes(id,incident_id,user_id,body,created_at) VALUES(?,?,?,?,?)',(nid,iid,user,body,created));c.execute('INSERT INTO timeline(incident_id,event_type,summary,created_at,payload) VALUES(?,?,?,?,?)',(iid,'ANALYST_NOTE',body[:240],created,dumps({'note_id':nid})))
  return {'id':nid,'incident_id':iid,'body':body,'created_at':created}
 def match(self,ids):
  made=0
  with self.db() as c:ws=c.execute('SELECT * FROM watchlists').fetchall()
  for w in ws:
   cats=loads(w['categories'],[]);sevs=loads(w['severities'],[]);terms=[x.strip().lower() for x in w['query'].replace(' OR ','|').split('|') if x.strip()]
   for iid in ids:
    i=self.incident(iid,False);hay=(i['title']+' '+dumps(i['payload'])).lower()
    if i['confidence']<w['min_confidence'] or (cats and i['category'] not in cats) or (sevs and i['severity'] not in sevs) or (terms and not any(t in hay for t in terms)):continue
    try:
     with self.db() as c:c.execute('INSERT INTO alerts(id,user_id,watchlist_id,incident_id,created_at) VALUES(?,?,?,?,?)',(sid('alert',w['id'],iid),w['user_id'],w['id'],iid,now()))
     made+=1
    except DatabaseIntegrityError:pass
  return made
 def alerts(self,user):
  with self.db() as c:return [dict(r) for r in c.execute('''SELECT a.*,w.name watchlist_name,i.title incident_title,i.severity,i.confidence FROM alerts a JOIN watchlists w ON w.id=a.watchlist_id JOIN incidents i ON i.id=a.incident_id WHERE a.user_id=? ORDER BY a.created_at DESC''',(user,)).fetchall()]
