from __future__ import annotations
import json
from datetime import datetime
from email.utils import format_datetime
from html import escape


def _date(value):
    try:
        return format_datetime(datetime.fromisoformat(str(value).replace('Z','+00:00')))
    except (TypeError,ValueError):
        return str(value or '')


def json_feed(incidents,base_url):
    items=[]
    for i in incidents:
        url=f"{base_url.rstrip('/')}/api/platform/incidents/{i['id']}"
        items.append({'id':i['id'],'url':url,'title':i['title'],'date_published':i['first_seen'],'date_modified':i['last_seen'],'content_text':f"{i['severity'].upper()} | {i['status']} | {i['grade']} | confidence {i['confidence']} | {i['action']}",'tags':[i['category'],i['severity'],i['status'],i['grade']]})
    return json.dumps({'version':'https://jsonfeed.org/version/1.1','title':'AURORA LIVE Incident Feed','home_page_url':base_url,'feed_url':f"{base_url.rstrip('/')}/api/platform/feed.json",'items':items},ensure_ascii=False).encode()


def rss_feed(incidents,base_url):
    items=[]
    for i in incidents:
        link=f"{base_url.rstrip('/')}/api/platform/incidents/{i['id']}";desc=f"{i['severity'].upper()} | {i['status']} | {i['grade']} | confidence {i['confidence']} | {i['action']}"
        items.append(f'<item><guid isPermaLink="false">{escape(i["id"])}</guid><title>{escape(i["title"])}</title><link>{escape(link)}</link><description>{escape(desc)}</description><pubDate>{escape(_date(i["last_seen"]))}</pubDate></item>')
    return ('<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel><title>AURORA LIVE Incident Feed</title><link>'+escape(base_url)+'</link><description>Evidence-first global incident assessments.</description>'+''.join(items)+'</channel></rss>').encode()
