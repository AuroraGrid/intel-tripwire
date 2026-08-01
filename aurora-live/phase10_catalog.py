from __future__ import annotations

import json
import threading
import time
import urllib.parse
import urllib.request
from collections import Counter
from typing import Any, Callable

import app

NATURAL_EARTH_URL = "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/ne_110m_admin_0_countries.geojson"
WORLD_BANK_URL = "https://api.worldbank.org/v2/country?format=json&per_page=400"
CABLES_URL = "https://www.submarinecablemap.com/api/v3/cable/all.json"
LANDINGS_URL = "https://www.submarinecablemap.com/api/v3/landing-point/all.json"
OVERPASS_URL = "https://overpass-api.de/api/interpreter"

CHOKEPOINTS = [
    ("Strait of Hormuz",26.56,56.25),("Strait of Malacca",2.5,101.4),("Bab el-Mandeb",12.58,43.33),
    ("Suez Canal",30.45,32.35),("Panama Canal",9.08,-79.68),("Turkish Straits",41.12,29.05),
    ("Strait of Gibraltar",35.96,-5.61),("Danish Straits",55.65,12.7),("English Channel",50.6,0.5),
    ("Taiwan Strait",24.0,119.5),("Lombok Strait",-8.5,115.8),("Sunda Strait",-6.0,105.8),
    ("Cape of Good Hope",-34.36,18.47),("Cape Horn",-55.98,-67.29),("Mozambique Channel",-17.0,41.0),
    ("Bosporus",41.12,29.05),("Dardanelles",40.2,26.4),("Korea Strait",34.4,129.2),
    ("Tsushima Strait",34.2,129.3),("Bering Strait",65.8,-168.9)
]

HOTSPOTS = [
    ("Ukraine",48.4,35.0),("Gaza",31.4,34.4),("Taiwan",23.8,121.0),("Korean Peninsula",38.2,127.0),
    ("South China Sea",14.0,114.0),("Kashmir",34.5,75.0),("Red Sea",18.0,40.0),("Sudan",15.5,32.5),
    ("Sahel",15.0,2.0),("Horn of Africa",8.0,45.0),("Syria",35.0,38.0),("Iraq",33.0,44.0),
    ("Iran",32.0,53.0),("Afghanistan",33.0,65.0),("Myanmar",21.0,96.0),("Venezuela-Guyana",7.0,-61.0),
    ("Arctic",75.0,30.0),("Eastern Mediterranean",34.0,30.0),("Balkans",43.0,20.0),("Caucasus",42.0,44.0),
    ("Great Lakes Africa",-2.0,29.0),("Haiti",19.0,-72.4),("Pakistan-Afghanistan",31.5,69.5),
    ("Persian Gulf",27.0,51.0),("Baltic Sea",57.5,20.0),("Black Sea",43.0,34.0),("Libya",27.0,17.0),
    ("Yemen",15.5,47.5),("Somalia",6.0,46.0),("Nigeria",9.0,8.0),("Ethiopia",9.0,40.0),
    ("DR Congo",-3.0,23.0),("Mozambique",-18.0,35.0),("Western Sahara",24.0,-13.0),
    ("Central African Republic",6.5,20.5),("Mali",17.0,-4.0),("Burkina Faso",12.3,-1.7),
    ("Niger",17.5,9.0),("Chad",15.0,19.0),("South Sudan",7.0,30.0)
]

MARKETS = [
    ("S&P 500","SPX",40.71,-74.01),("Nasdaq 100","NDX",40.71,-74.01),("Dow Jones","DJI",40.71,-74.01),
    ("FTSE 100","FTSE",51.51,-0.09),("DAX","DAX",50.11,8.68),("CAC 40","CAC",48.87,2.34),
    ("Nikkei 225","N225",35.68,139.76),("Hang Seng","HSI",22.28,114.16),("Shanghai Composite","SSEC",31.23,121.47),
    ("Sensex","SENSEX",19.08,72.88),("ASX 200","AXJO",-33.87,151.21),("TSX","GSPTSE",43.65,-79.38),
    ("Bovespa","BVSP",-23.55,-46.63),("WTI Crude","CL",29.76,-95.37),("Brent Crude","BZ",51.51,-0.09),
    ("Gold","GC",40.71,-74.01),("Copper","HG",41.88,-87.63),("Natural Gas","NG",29.76,-95.37),
    ("Bitcoin","BTC",0.0,0.0),("Ether","ETH",0.0,0.0),("EUR/USD","EURUSD",50.11,8.68),
    ("USD/JPY","USDJPY",35.68,139.76),("US 10Y","US10Y",38.9,-77.04),("VIX","VIX",41.88,-87.63)
]

class Cache:
    def __init__(self):
        self.lock=threading.Lock(); self.data={}
    def get(self,key,ttl,loader):
        now=time.time()
        with self.lock:
            row=self.data.get(key)
            if row and now-row[0]<ttl:return row[1]
        value=loader()
        with self.lock:self.data[key]=(now,value)
        return value

CACHE=Cache()

def _json(url:str,method:str="GET",body:bytes|None=None)->Any:
    req=urllib.request.Request(url,data=body,method=method,headers={"User-Agent":"AURORA-LIVE/1.0 (+mailto:hr185882@gmail.com)","Accept":"application/json","Content-Type":"application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req,timeout=8) as response:return json.loads(response.read().decode("utf-8","replace"))

def _asset(kind:str,name:str,lat:float,lon:float,**extra)->dict[str,Any]:
    return {"id":app.stable_id(kind,name),"type":kind,"name":name,"latitude":lat,"longitude":lon,**extra}

def static_assets()->list[dict[str,Any]]:
    rows=[_asset("chokepoint",n,a,b) for n,a,b in CHOKEPOINTS]
    rows += [_asset("hotspot",n,a,b) for n,a,b in HOTSPOTS]
    rows += [_asset("market",n,a,b,symbol=s) for n,s,a,b in MARKETS]
    return rows

def country_features()->dict[str,Any]:
    return CACHE.get("countries",86400,lambda:_json(NATURAL_EARTH_URL))

def world_bank_countries()->list[dict[str,Any]]:
    def load():
        data=_json(WORLD_BANK_URL); rows=[]
        for item in (data[1] if isinstance(data,list) and len(data)>1 else []):
            if item.get("region",{}).get("id") in {"NA",None}:continue
            lat=item.get("latitude");lon=item.get("longitude")
            try:lat=float(lat);lon=float(lon)
            except (TypeError,ValueError):continue
            rows.append({"id":item.get("id"),"iso2":item.get("iso2Code"),"name":item.get("name"),"region":item.get("region",{}).get("value"),"income":item.get("incomeLevel",{}).get("value"),"capital":item.get("capitalCity"),"latitude":lat,"longitude":lon})
        return rows
    return CACHE.get("worldbank",86400,load)

def submarine_cables()->dict[str,Any]:
    def load():
        cables=_json(CABLES_URL); landings=_json(LANDINGS_URL)
        return {"cables":cables if isinstance(cables,list) else cables.get("cables",[]),"landing_points":landings if isinstance(landings,list) else landings.get("landing_points",[])}
    return CACHE.get("cables",21600,load)

def overpass_assets(kind:str)->list[dict[str,Any]]:
    queries={
        "datacenter":"[out:json][timeout:12];(node[\"telecom\"=\"data_center\"];way[\"telecom\"=\"data_center\"];relation[\"telecom\"=\"data_center\"];node[\"man_made\"=\"data_centre\"];way[\"man_made\"=\"data_centre\"];);out center tags;",
        "pipeline":"[out:json][timeout:12];(way[\"man_made\"=\"pipeline\"][\"substance\"~\"gas|oil|lng\",i];relation[\"man_made\"=\"pipeline\"][\"substance\"~\"gas|oil|lng\",i];);out center tags;",
        "lng":"[out:json][timeout:12];(node[\"industrial\"=\"terminal\"][\"product\"~\"lng|natural_gas\",i];way[\"industrial\"=\"terminal\"][\"product\"~\"lng|natural_gas\",i];);out center tags;"
    }
    if kind not in queries:raise ValueError("unsupported infrastructure type")
    def load():
        body=urllib.parse.urlencode({"data":queries[kind]}).encode(); data=_json(OVERPASS_URL,"POST",body);rows=[]
        for item in data.get("elements",[]):
            center=item.get("center") or item
            try:lat=float(center["lat"]);lon=float(center["lon"])
            except (KeyError,TypeError,ValueError):continue
            tags=item.get("tags") or {};name=tags.get("name") or f"{kind.title()} {item.get('id')}"
            rows.append(_asset(kind,name,lat,lon,osm_id=item.get("id"),tags=tags))
        return rows
    return CACHE.get("overpass:"+kind,21600,load)

def catalog_summary(extra:dict[str,int]|None=None)->dict[str,Any]:
    counts=Counter(row["type"] for row in static_assets()); counts.update(extra or {})
    return {"counts":dict(counts),"total":sum(counts.values()),"baselines":{"chokepoints":13,"cables":86,"pipeline_lng":88,"datacenters":313,"hotspots":29,"markets":92,"countries":196}}
