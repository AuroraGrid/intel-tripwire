import json,tempfile,unittest,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from delivery import deliver_pending
from feeds import json_feed,rss_feed
from operations import Operations
from storage import Store

E={'id':'i1','title':'Major port outage','category':'infrastructure','severity':'high','k_align_status':'PLAUSIBLE','confidence_grade':'G2','confidence_score':76,'action_state':'PREPARE','evidence':[{'id':'e1','source_family':'port.example','title':'Official notice','url':'https://port.example/1','official':True},{'id':'e2','source_family':'news.example','title':'Report','url':'https://news.example/1'}]}
class Response:
 status=204
 def __enter__(self):return self
 def __exit__(self,*a):return False
class T(unittest.TestCase):
 def setUp(self):self.t=tempfile.TemporaryDirectory();self.s=Store(Path(self.t.name)/'x.db');self.o=Operations(self.s);self.u,self.token=self.s.create_user('a@b.com','admin')
 def tearDown(self):self.t.cleanup()
 def test_auth(self):self.assertEqual(self.s.auth(self.token)['email'],'a@b.com');self.assertIsNone(self.s.auth('x'))
 def test_ingest_graph_timeline_filters(self):
  r=self.s.ingest({'events':[E]});self.assertEqual(r['created'],1);self.assertEqual(len(self.s.incident('i1')['evidence']),2);self.assertEqual(len(self.s.graph('i1')['edges']),4);self.assertEqual(self.s.timeline('i1')[0]['event_type'],'DETECTED');self.assertEqual(len(self.o.incidents(category='infrastructure',min_confidence=75)),1);self.assertEqual(self.o.incidents(category='conflict'),[])
 def test_watch_alert_ack_once(self):
  self.s.add_watchlist(self.u['id'],{'name':'Ports','query':'port','categories':['infrastructure'],'severities':['high'],'min_confidence':70});self.assertEqual(self.s.ingest({'events':[E]})['alerts_created'],1);self.assertEqual(self.s.ingest({'events':[E]})['alerts_created'],0);a=self.o.alerts(self.u['id'])[0];self.o.acknowledge_alert(self.u['id'],a['id']);self.assertEqual(self.o.alerts(self.u['id'],True),[])
 def test_change_note_case(self):
  self.s.ingest({'events':[E]});self.s.ingest({'events':[dict(E,severity='critical',confidence_score=95)]});self.s.add_note('i1',self.u['id'],'Verify rail impact');c=self.o.create_case(self.u['id'],{'title':'Port disruption','priority':'high'});self.o.add_case_incident(self.u['id'],c['id'],'i1');self.o.add_case_note(self.u['id'],c['id'],'Contact logistics desk');c=self.o.case(self.u['id'],c['id']);self.assertEqual(len(c['incidents']),1);self.assertEqual(len(c['notes']),1);self.assertEqual([x['event_type'] for x in self.s.timeline('i1')],['DETECTED','ASSESSMENT_CHANGED','ANALYST_NOTE'])
 def test_webhook_delivery_guard(self):
  with self.assertRaises(ValueError):self.o.add_webhook(self.u['id'],{'name':'bad','url':'http://127.0.0.1/x'})
  self.o.add_webhook(self.u['id'],{'name':'Ops','url':'https://hooks.example.com/aurora'});self.s.add_watchlist(self.u['id'],{'name':'Ports','query':'port'});self.s.ingest({'events':[E]});a=self.s.alerts(self.u['id'])[0];self.o.queue_deliveries(self.u['id'],a['id']);seen={}
  def opener(req,timeout=0):seen['body']=json.loads(req.data.decode());return Response()
  self.assertEqual(deliver_pending(self.o,self.u['id'],opener=opener),{'attempted':1,'delivered':1,'failed':0});self.assertEqual(seen['body']['type'],'aurora.alert');self.assertEqual(self.o.pending_deliveries(self.u['id']),[])
 def test_feeds_stats(self):
  self.s.ingest({'events':[E]});items=self.o.incidents();self.assertEqual(json.loads(json_feed(items,'https://a.example'))['items'][0]['id'],'i1');self.assertIn('<rss version="2.0">',rss_feed(items,'https://a.example').decode());self.assertEqual(self.o.stats(self.u['id'])['incidents'],1)
if __name__=='__main__':unittest.main()
