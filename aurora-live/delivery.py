from __future__ import annotations
import hashlib,hmac,json,os,urllib.request


def deliver_pending(operations,user_id,opener=urllib.request.urlopen,timeout=8):
    rows=operations.pending_deliveries(user_id);delivered=failed=0;secret=os.getenv('AURORA_WEBHOOK_SECRET','')
    for row in rows:
        payload={'type':'aurora.alert','alert':{'id':row['alert_id'],'watchlist_id':row['watchlist_id'],'incident_id':row['incident_id'],'incident_title':row['incident_title'],'category':row['category'],'severity':row['severity'],'confidence':row['confidence'],'status':row['status'],'grade':row['grade'],'action':row['action']}}
        body=json.dumps(payload,separators=(',',':'),ensure_ascii=False).encode();headers={'Content-Type':'application/json','User-Agent':'AuroraLiveWebhook/0.2'}
        if secret:headers['X-Aurora-Signature']='sha256='+hmac.new(secret.encode(),body,hashlib.sha256).hexdigest()
        req=urllib.request.Request(row['url'],data=body,headers=headers,method='POST')
        try:
            with opener(req,timeout=timeout) as response:status=int(getattr(response,'status',200))
            if 200<=status<300:operations.record_delivery(row['id'],True);delivered+=1
            else:operations.record_delivery(row['id'],False,f'HTTP {status}');failed+=1
        except Exception as exc:operations.record_delivery(row['id'],False,str(exc));failed+=1
    return {'attempted':len(rows),'delivered':delivered,'failed':failed}
