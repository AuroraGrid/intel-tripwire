from __future__ import annotations

import time
import urllib.parse
import uuid
from collections import defaultdict
from typing import Any

from phase10_assets import all_static_assets
from phase10_catalog import country_features, overpass_assets, submarine_cables, world_bank_countries
from phase10_wsgi import Phase10Application
from platform_wsgi import HTTPError, RID_RE

BASELINES={"chokepoint":13,"cable":86,"pipeline_lng":88,"datacenter":313,"hotspot":29,"market":92,"country":196}

def _value(query:dict[str,list[str]],name:str,default:str="")->str:return (query.get(name)or[default])[0]

def _asset_filter(rows:list[dict[str,Any]],query:dict[str,list[str]])->list[dict[str,Any]]:
    kinds={v.strip() for v in _value(query,"types").split(",") if v.strip()};west=float(_value(query,"west","-180"));east=float(_value(query,"east","180"));south=float(_value(query,"south","-90"));north=float(_value(query,"north","90"));result=[]
    for row in rows:
        if row.get("latitude") is None or row.get("longitude") is None:continue
        lat=float(row["latitude"]);lon=float(row["longitude"])
        if kinds and row.get("type") not in kinds:continue
        if not south<=lat<=north:continue
        if west<=east and not west<=lon<=east:continue
        if west>east and not(lon>=west or lon<=east):continue
        result.append(row)
    return result[:min(20000,max(1,int(_value(query,"limit","10000"))))]

def dependency_graph(assets:list[dict[str,Any]],incidents:list[dict[str,Any]])->dict[str,Any]:
    nodes=[];edges=[];regions={}
    def region(label):
        rid="region:"+label.lower().replace(" ","-")
        if rid not in regions:regions[rid]={"id":rid,"kind":"region","label":label};nodes.append(regions[rid])
        return rid
    for asset in assets:
        nodes.append({"id":asset["id"],"kind":asset["type"],"label":asset["name"],"latitude":asset.get("latitude"),"longitude":asset.get("longitude")});edges.append({"source":asset["id"],"target":region(str(asset.get("country")or asset.get("region")or"global")),"type":"located_in","weight":1})
    for incident in incidents[:1000]:
        iid=str(incident.get("id")or"")
        if not iid:continue
        nid="incident:"+iid;nodes.append({"id":nid,"kind":"incident","label":incident.get("title")or iid,"severity":incident.get("severity")});edges.append({"source":nid,"target":region(str(incident.get("country")or incident.get("location_name")or"global")),"type":"affects","weight":{"critical":8,"high":4,"medium":2}.get(str(incident.get("severity")),1)})
    return {"nodes":nodes,"edges":edges,"node_count":len(nodes),"edge_count":len(edges)}

def route_exposure(points:list[tuple[float,float]],assets:list[dict[str,Any]],incidents:list[dict[str,Any]],radius:float=5)->dict[str,Any]:
    def near(lat,lon):return min(((lat-a)**2+(lon-b)**2)**.5 for a,b in points) if points else 999
    exposed_assets=[r for r in assets if near(float(r.get("latitude",0)),float(r.get("longitude",0)))<=radius];exposed_incidents=[]
    for row in incidents:
        try:lat=float(row.get("latitude"));lon=float(row.get("longitude"))
        except(TypeError,ValueError):continue
        if near(lat,lon)<=radius:exposed_incidents.append(row)
    score=min(100,len(exposed_assets)*2+sum({"critical":12,"high":6,"medium":2}.get(str(r.get("severity")),1) for r in exposed_incidents))
    return {"risk_score":score,"risk_band":"critical" if score>=80 else "high" if score>=50 else "elevated" if score>=25 else "normal","assets":exposed_assets[:500],"incidents":exposed_incidents[:500]}

class Phase10CompleteApplication(Phase10Application):
    def _all_assets(self,query):
        rows=all_static_assets();counts=defaultdict(int);errors=[]
        for row in rows:counts[row["type"]]+=1
        # Default is static-only. Live OSM/cable pulls are opt-in via ?live=...
        # so the map does not hang Gunicorn waiting on Overpass.
        requested={x.strip() for x in _value(query,"live","").split(",") if x.strip()}
        if "cables" in requested:
            try:
                data=submarine_cables()
                for cable in data.get("cables",[]):
                    row={"id":str(cable.get("id")or cable.get("slug")or cable.get("name")),"type":"cable","name":cable.get("name")or cable.get("id"),"raw":cable};rows.append(row);counts["cable"]+=1
            except Exception as exc:errors.append("cables: "+str(exc))
        for kind in requested&{"datacenter","pipeline","lng"}:
            try:found=overpass_assets(kind);rows.extend(found);counts[kind]+=len(found)
            except Exception as exc:errors.append(kind+": "+str(exc))
        return rows,dict(counts),errors
    def _complete(self,path,query):
        started=time.perf_counter();incidents=self._incidents(query)
        if path=="/api/platform/geo/countries":return {"geojson":country_features(),"dossiers":world_bank_countries()}
        assets,counts,errors=self._all_assets(query)
        if path=="/api/platform/geo/assets":data={"assets":_asset_filter(assets,query),"counts":counts,"errors":errors}
        elif path=="/api/platform/geo/catalog":
            countries=0
            try:countries=len(world_bank_countries())
            except Exception as exc:errors.append("countries: "+str(exc))
            data={"counts":counts|{"country":countries},"total":sum(counts.values())+countries,"baselines":BASELINES,"passed":{"chokepoint":counts.get("chokepoint",0)>13,"cable":counts.get("cable",0)>86,"pipeline_lng":counts.get("pipeline",0)+counts.get("lng",0)>88,"datacenter":counts.get("datacenter",0)>313,"hotspot":counts.get("hotspot",0)>29,"market":counts.get("market",0)>92,"country":countries>=196},"errors":errors}
        elif path=="/api/platform/geo/dependencies":data=dependency_graph(_asset_filter(assets,query),incidents)
        elif path=="/api/platform/geo/route-risk":
            points=[]
            for pair in _value(query,"points").split(";"):
                if pair.strip():lat,lon=pair.split(",",1);points.append((float(lat),float(lon)))
            data=route_exposure(points,_asset_filter(assets,query),incidents,float(_value(query,"radius","5")))
        else:raise HTTPError(404,"not_found","route not found")
        data["elapsed_ms"]=round((time.perf_counter()-started)*1000,3);return data
    def __call__(self,environ,start_response):
        path=str(environ.get("PATH_INFO")or"")
        if path not in {"/api/platform/geo/countries","/api/platform/geo/assets","/api/platform/geo/catalog","/api/platform/geo/dependencies","/api/platform/geo/route-risk"}:return super().__call__(environ,start_response)
        rid=str(environ.get("HTTP_X_REQUEST_ID")or"");rid=rid if RID_RE.fullmatch(rid) else uuid.uuid4().hex
        try:
            if str(environ.get("REQUEST_METHOD")or"GET").upper()!="GET":raise HTTPError(405,"method_not_allowed","method not allowed",[("Allow","GET")])
            self._user(environ);query=urllib.parse.parse_qs(str(environ.get("QUERY_STRING")or""),keep_blank_values=True);return self._response(environ,start_response,200,self._complete(path,query),rid)
        except HTTPError as exc:return self._error(environ,start_response,rid,exc)
        except ValueError as exc:return self._error(environ,start_response,rid,HTTPError(400,"bad_request",str(exc)))
        except Exception:return self._error(environ,start_response,rid,HTTPError(500,"internal_error","internal server error"))

application=Phase10CompleteApplication()
