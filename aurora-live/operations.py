from __future__ import annotations
import hashlib,json,secrets,socket,sqlite3,urllib.parse
from datetime import datetime,timezone


def now(): return datetime.now(timezone.utc).isoformat().replace('+00:00','Z')
def sid(*x): return hashlib.sha256('|'.join(map(str,x)).encode()).hexdigest()[:24]
def dumps(x): return json.dumps(x,ensure_ascii=False,separators=(',',':'),sort_keys=True)

class Operations:
 def __init__(self,store): self.store=store; self.init()
 def init(self):
  with self.store.db() as c:c.executescript('''
  CREATE TABLE IF NOT EXISTS cases(id TEXT PRIMARY KEY,user_id TEXT,title TEXT,status TEXT,priority TEXT,created_at TEXT,updated