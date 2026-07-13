from __future__ import annotations

import ipaddress
import secrets
import urllib.parse
from typing import Any

from storage import now, sid


class Operations:
    def __init__(self, store):
        self.store = store
        self.init()

    def init(self):
        with self.store.db() as c:
            c.executescript('''
            CREATE TABLE IF NOT EXISTS cases(id TEXT PRIMARY KEY,user_id TEXT,title TEXT,description TEXT,status TEXT,priority TEXT,created_at TEXT,updated_at TEXT);
            CREATE TABLE IF NOT EXISTS case_incidents(case_id TEXT,incident_id TEXT,added_at TEXT,PRIMARY KEY(case_id,incident_id));
            CREATE TABLE IF NOT EXISTS case_notes(id TEXT PRIMARY KEY,case_id TEXT,user_id TEXT,body TEXT,created_at TEXT);
            CREATE TABLE IF NOT EXISTS webhooks(id TEXT PRIMARY KEY,user_id TEXT,name TEXT,url TEXT,active INTEGER,created_at TEXT);
            CREATE TABLE IF NOT EXISTS deliveries(id TEXT PRIMARY KEY,alert_id TEXT,webhook_id TEXT,status TEXT,attempts INTEGER,last_error TEXT,delivered_at TEXT,updated_at TEXT,UNIQUE(alert_id,webhook_id));
            CREATE INDEX IF NOT EXISTS idx_cases_user ON cases(user_id,updated_at DESC);
            CREATE INDEX IF NOT EXISTS idx_deliveries_status ON deliveries(status,updated_at);
            ''')
            cols={r['name'] for r in c.execute('PRAGMA table_info(alerts)')}
            if 'acknowledged_at' not in cols:
                c.execute('ALTER TABLE alerts ADD COLUMN acknowledged_at TEXT')

    def incidents(self, query='', severity='', category='', status='', grade='', action='', min_confidence=0, limit=100, offset=0):
        sql='SELECT id FROM incidents WHERE confidence>=?'; args=[max(0,min(100,int(min_confidence)))]
        if query:
            sql+=' AND (lower(title) LIKE ? OR lower(payload) LIKE ?)'; needle='%'+query.lower()+'%'; args.extend([needle,needle])
        for col,val in (('severity',severity),('category',category),('status',status),('grade',grade),('action',action)):
            if val: sql+=f' AND {col}=?'; args.append(val)
        sql+=' ORDER BY last_seen DESC LIMIT ? OFFSET ?'; args.extend([max(1,min(500,int(limit))),max(0,int(offset))])
        with self.store.db() as c: rows=c.execute(sql,args).fetchall()
        return [self.store.incident(r['id'],False) for r in rows]

    def acknowledge_alert(self,user_id,alert_id):
        stamp=now()
        with self.store.db() as c: changed=c.execute('UPDATE alerts SET acknowledged_at=? WHERE id=? AND user_id=?',(stamp,alert_id,user_id)).rowcount
        if not changed: raise KeyError('alert not found')
        return {'id':alert_id,'acknowledged_at':stamp}

    def alerts(self,user_id,unacknowledged=False):
        sql='''SELECT a.*,w.name watchlist_name,i.title incident_title,i.severity,i.confidence,i.category,i.status,i.grade,i.action FROM alerts a JOIN watchlists w ON w.id=a.watchlist_id JOIN incidents i ON i.id=a.incident_id WHERE a.user_id=?'''
        if unacknowledged: sql+=' AND a.acknowledged_at IS NULL'
        sql+=' ORDER BY a.created_at DESC'
        with self.store.db() as c:return [dict(r) for r in c.execute(sql,(user_id,)).fetchall()]

    def _webhook_url(self,value):
        url=str(value or '').strip(); p=urllib.parse.urlparse(url)
        if p.scheme!='https' or not p.hostname: raise ValueError('webhook URL must use https')
        host=p.hostname.lower()
        if host in {'localhost','localhost.localdomain'} or host.endswith('.local'): raise ValueError('local webhook destinations are not allowed')
        try: ip=ipaddress.ip_address(host)
        except ValueError: ip=None
        if ip and (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved): raise ValueError('private webhook destinations are not allowed')
        return url

    def add_webhook(self,user_id,payload):
        name=str(payload.get('name','')).strip()
        if not name: raise ValueError('name required')
        url=self._webhook_url(payload.get('url','')); wid=sid('webhook',user_id,name,url,now(),secrets.token_hex(4)); created=now()
        with self.store.db() as c:c.execute('INSERT INTO webhooks VALUES(?,?,?,?,?,?)',(wid,user_id,name,url,1,created))
        return self.webhook(user_id,wid)

    def webhook(self,user_id,wid):
        with self.store.db() as c:r=c.execute('SELECT * FROM webhooks WHERE user_id=? AND id=?',(user_id,wid)).fetchone()
        if not r: raise KeyError('webhook not found')
        x=dict(r);x['active']=bool(x['active']);return x

    def webhooks(self,user_id):
        with self.store.db() as c:rows=c.execute('SELECT id FROM webhooks WHERE user_id=? ORDER BY created_at DESC',(user_id,)).fetchall()
        return [self.webhook(user_id,r['id']) for r in rows]

    def delete_webhook(self,user_id,wid):
        with self.store.db() as c:return c.execute('DELETE FROM webhooks WHERE user_id=? AND id=?',(user_id,wid)).rowcount>0

    def queue_deliveries(self,user_id,alert_id):
        with self.store.db() as c:
            hooks=c.execute('SELECT id FROM webhooks WHERE user_id=? AND active=1',(user_id,)).fetchall()
            for h in hooks:c.execute('INSERT OR IGNORE INTO deliveries VALUES(?,?,?,?,?,?,?,?)',(sid('delivery',alert_id,h['id']),alert_id,h['id'],'pending',0,None,None,now()))

    def pending_deliveries(self,user_id,limit=100):
        sql='''SELECT d.*,w.url,w.name webhook_name,a.user_id,a.watchlist_id,a.incident_id,i.title incident_title,i.severity,i.confidence,i.category,i.status,i.grade,i.action FROM deliveries d JOIN webhooks w ON w.id=d.webhook_id JOIN alerts a ON a.id=d.alert_id JOIN incidents i ON i.id=a.incident_id WHERE a.user_id=? AND d.status IN ('pending','failed') AND d.attempts<5 AND w.active=1 ORDER BY d.updated_at LIMIT ?'''
        with self.store.db() as c:return [dict(r) for r in c.execute(sql,(user_id,max(1,min(500,int(limit))))).fetchall()]

    def record_delivery(self,delivery_id,success,error=''):
        with self.store.db() as c:
            r=c.execute('SELECT attempts FROM deliveries WHERE id=?',(delivery_id,)).fetchone()
            if not r: raise KeyError('delivery not found')
            c.execute('UPDATE deliveries SET status=?,attempts=?,last_error=?,delivered_at=?,updated_at=? WHERE id=?',('delivered' if success else 'failed',int(r['attempts'])+1,None if success else error[:500],now() if success else None,now(),delivery_id))

    def create_case(self,user_id,payload):
        title=str(payload.get('title','')).strip()
        if not title: raise ValueError('title required')
        status=str(payload.get('status','open')).lower();priority=str(payload.get('priority','normal')).lower()
        if status not in {'open','monitoring','closed'}: raise ValueError('invalid case status')
        if priority not in {'low','normal','high','critical'}: raise ValueError('invalid case priority')
        cid=sid('case',user_id,title,now(),secrets.token_hex(4));stamp=now()
        with self.store.db() as c:c.execute('INSERT INTO cases VALUES(?,?,?,?,?,?,?,?)',(cid,user_id,title,str(payload.get('description','')).strip(),status,priority,stamp,stamp))
        return self.case(user_id,cid)

    def cases(self,user_id):
        with self.store.db() as c:rows=c.execute('SELECT id FROM cases WHERE user_id=? ORDER BY updated_at DESC',(user_id,)).fetchall()
        return [self.case(user_id,r['id']) for r in rows]

    def case(self,user_id,cid):
        with self.store.db() as c:
            r=c.execute('SELECT * FROM cases WHERE user_id=? AND id=?',(user_id,cid)).fetchone()
            incidents=c.execute('SELECT i.id,i.title,i.category,i.severity,i.status,i.grade,i.confidence,i.action,ci.added_at FROM case_incidents ci JOIN incidents i ON i.id=ci.incident_id WHERE ci.case_id=? ORDER BY ci.added_at DESC',(cid,)).fetchall()
            notes=c.execute('SELECT id,user_id,body,created_at FROM case_notes WHERE case_id=? ORDER BY created_at',(cid,)).fetchall()
        if not r: raise KeyError('case not found')
        x=dict(r);x['incidents']=[dict(v) for v in incidents];x['notes']=[dict(v) for v in notes];return x

    def add_case_incident(self,user_id,cid,iid):
        self.case(user_id,cid);self.store.incident(iid,False)
        with self.store.db() as c:c.execute('INSERT OR IGNORE INTO case_incidents VALUES(?,?,?)',(cid,iid,now()));c.execute('UPDATE cases SET updated_at=? WHERE id=?',(now(),cid))
        return self.case(user_id,cid)

    def remove_case_incident(self,user_id,cid,iid):
        self.case(user_id,cid)
        with self.store.db() as c:changed=c.execute('DELETE FROM case_incidents WHERE case_id=? AND incident_id=?',(cid,iid)).rowcount;c.execute('UPDATE cases SET updated_at=? WHERE id=?',(now(),cid))
        return changed>0

    def add_case_note(self,user_id,cid,body):
        self.case(user_id,cid);body=body.strip()
        if not body: raise ValueError('body required')
        nid=sid('case-note',cid,user_id,now(),secrets.token_hex(4));stamp=now()
        with self.store.db() as c:c.execute('INSERT INTO case_notes VALUES(?,?,?,?,?)',(nid,cid,user_id,body,stamp));c.execute('UPDATE cases SET updated_at=? WHERE id=?',(stamp,cid))
        return {'id':nid,'case_id':cid,'body':body,'created_at':stamp}

    def stats(self,user_id):
        with self.store.db() as c:return {'incidents':c.execute('SELECT COUNT(*) FROM incidents').fetchone()[0],'alerts_unacknowledged':c.execute('SELECT COUNT(*) FROM alerts WHERE user_id=? AND acknowledged_at IS NULL',(user_id,)).fetchone()[0],'watchlists':c.execute('SELECT COUNT(*) FROM watchlists WHERE user_id=?',(user_id,)).fetchone()[0],'cases':c.execute('SELECT COUNT(*) FROM cases WHERE user_id=?',(user_id,)).fetchone()[0],'pending_deliveries':c.execute("SELECT COUNT(*) FROM deliveries d JOIN alerts a ON a.id=d.alert_id WHERE a.user_id=? AND d.status IN ('pending','failed') AND d.attempts<5",(user_id,)).fetchone()[0]}
