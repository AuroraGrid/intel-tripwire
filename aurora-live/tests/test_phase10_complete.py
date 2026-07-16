import sys
import unittest
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

from phase10_benchmark import qualify, synthetic_incidents
from phase10_catalog import CHOKEPOINTS, HOTSPOTS, static_assets
from phase10_complete import dependency_graph, route_exposure
from phase10_geo import GeoIndex, GeoQuery


class Phase10CompleteTests(unittest.TestCase):
    def test_static_catalog_exceeds_chokepoint_and_hotspot_baselines(self):
        self.assertGreater(len(CHOKEPOINTS),13)
        self.assertGreater(len(HOTSPOTS),29)
        kinds={row['type'] for row in static_assets()}
        self.assertTrue({'chokepoint','hotspot','market'}<=kinds)

    def test_dependency_graph_links_assets_and_incidents(self):
        assets=static_assets()[:4]
        incidents=[{'id':'i1','title':'Test incident','severity':'high','location_name':'Japan'}]
        graph=dependency_graph(assets,incidents)
        self.assertGreaterEqual(graph['node_count'],6)
        self.assertGreaterEqual(graph['edge_count'],5)

    def test_route_exposure_scores_nearby_risk(self):
        assets=[{'id':'a','name':'Asset','type':'chokepoint','latitude':10,'longitude':10}]
        incidents=[{'id':'i','latitude':10.5,'longitude':10.5,'severity':'critical'}]
        result=route_exposure([(10,10)],assets,incidents,2)
        self.assertEqual(len(result['assets']),1)
        self.assertEqual(len(result['incidents']),1)
        self.assertGreater(result['risk_score'],0)

    def test_twenty_thousand_object_filter_cluster_heat(self):
        rows=synthetic_incidents(20000)
        index=GeoIndex(rows)
        query=GeoQuery(bbox=(-120,-60,120,70),zoom=5,limit=20000)
        self.assertGreater(len(index.filter(query)),1000)
        self.assertGreater(len(index.clusters(query)),100)
        self.assertGreater(len(index.heat(query)),100)

    def test_benchmark_performance_gate(self):
        result=qualify(objects=10000,iterations=3)
        self.assertTrue(result['performance_passed'])
        self.assertTrue(result['static_baseline_passed'])


if __name__=='__main__':unittest.main()
