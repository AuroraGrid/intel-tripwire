import tempfile,unittest,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from storage import Store
E={'id':'i1','title':'Major port outage','category':'infrastructure','severity':'high','k_align_status':'PLAUSIBLE','confidence_grade':'G2','confidence_score':76,'action_state':'PREPARE','evidence':[{'id':'e1','source_family':'port.example','title':'Official notice','url':'https://port.example/1','official':True},{'id':'e2','source_family':'news.example','title':'Report','url':'https://news.example/1'}]}
class T(unittest.TestCase):
 def setUp(self):self.t=tempfile.TemporaryDirectory();self.s=Store(Path(self.t.name)/'x.db');self.u,self.token=self.s.create_user('a@b.com','admin')
 def tearDown(self):self.t.cleanup()
 def test_auth(self):self.assertEqual(self.s.auth(self.token)['email'],'a@b.com');self.assertIsNone(self.s.auth('x'))
 def test_ingest_graph_timeline(self):
  r=self.s.ingest({'events':[E]});self.assertEqual(r['created'],1);self.assertEqual(len(self.s.incident('i1')['evidence']),2);self.assertEqual(len(self.s.graph('i1')['edges']),4);self.assertEqual(self.s.timeline('i1')[0]['event_type'],'DETECTED')
 def test_watch_alert_once(self):
  self.s.add_watchlist(self.u['id'],{'name':'Ports','query':'port','categories':['infrastructure'],'severities':['high'],'min_confidence':70});self.assertEqual(self.s.ingest({'events':[E]})['alerts_created'],1);self.assertEqual(self.s.ingest({'events':[E]})['alerts_created'],0)
 def test_change_and_note(self):
  self.s.ingest({'events':[E]});self.s.ingest({'events':[dict(E,severity='critical',confidence_score=95)]});self.s.add_note('i1',self.u['id'],'Verify rail impact');self.assertEqual([x['event_type'] for x in self.s.timeline('i1')],['DETECTED','ASSESSMENT_CHANGED','ANALYST_NOTE'])
if __name__=='__main__':unittest.main()
