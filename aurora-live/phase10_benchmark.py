from __future__ import annotations

import argparse,json,random,time
from collections import Counter
from pathlib import Path

from phase10_assets import all_static_assets
from phase10_catalog import world_bank_countries
from phase10_geo import GeoIndex,GeoQuery

def synthetic_incidents(count:int)->list[dict]:
    rng=random.Random(1010);sev=("low","medium","high","critical");cat=("conflict","disaster","cyber","infrastructure","health")
    return [{"id":f"synthetic-{i}","latitude":rng.uniform(-85,85),"longitude":rng.uniform(-180,180),"severity":sev[i%4],"category":cat[i%5],"published_at":"2026-07-16T00:00:00Z"} for i in range(count)]

def qualify(objects:int=20000,iterations:int=25)->dict:
    rows=synthetic_incidents(objects);index=GeoIndex(rows);query=GeoQuery(bbox=(-130,-60,150,75),zoom=5,limit=objects);samples=[]
    for _ in range(iterations):
        started=time.perf_counter();filtered=index.filter(query);clusters=index.clusters(query);index.heat(query);samples.append((time.perf_counter()-started)*1000)
    samples.sort();p95=samples[min(len(samples)-1,int(len(samples)*.95))]
    try:countries=len(world_bank_countries());country_error=None
    except Exception as exc:countries=0;country_error=str(exc)
    counts=dict(Counter(row['type'] for row in all_static_assets()))
    result={"schema_version":"1.1","objects":objects,"iterations":iterations,"filtered":len(filtered),"clusters":len(clusters),"median_ms":round(samples[len(samples)//2],3),"p95_ms":round(p95,3),"countries":countries,"country_error":country_error,"static_counts":counts}
    result["performance_passed"]=objects>=10000 and p95<1000
    result["country_gate_passed"]=countries>=196
    result["static_baseline_passed"]=counts.get("chokepoint",0)>13 and counts.get("hotspot",0)>29 and counts.get("market",0)>92
    result["passed"]=result["performance_passed"] and result["country_gate_passed"] and result["static_baseline_passed"]
    return result

def main()->None:
    p=argparse.ArgumentParser();p.add_argument("--objects",type=int,default=20000);p.add_argument("--iterations",type=int,default=25);p.add_argument("--output",default="");a=p.parse_args();result=qualify(a.objects,a.iterations);text=json.dumps(result,indent=2,sort_keys=True);print(text)
    if a.output:Path(a.output).write_text(text,encoding="utf-8")
    raise SystemExit(0 if result["passed"] else 1)

if __name__=="__main__":main()
