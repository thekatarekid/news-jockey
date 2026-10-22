"""Retain public publisher caption text, without downloading or transcribing media."""
import json
import re
from bs4 import BeautifulSoup
from .network import fetch

def caption_text(raw):
    if len(raw)>512*1024: raise ValueError('Caption file exceeds 512 KB.')
    text=raw.decode('utf-8-sig',errors='replace').replace('\r','')
    if not re.search(r'\d{2}:\d{2}(?::\d{2})?[.,]\d+\s*-->',text):
        raise ValueError('No VTT/SRT caption cues found.')
    cues=[]
    for block in re.split(r'\n\s*\n',text):
        lines=block.splitlines()
        timing=next((i for i,line in enumerate(lines) if '-->' in line),None)
        if timing is None: continue
        value=BeautifulSoup(' '.join(lines[timing+1:]),'html.parser').get_text(' ',strip=True)
        value=re.sub(r'<\d{2}:\d{2}(?::\d{2})?\.\d+>','',value)
        if value and (not cues or value!=cues[-1]): cues.append(value)
    return '\n\n'.join(cues)

def capture_captions(result):
    if result.get('transcript') or result.get('extraction_status') in ('blocked','paywalled'):
        return
    media=json.loads(result.get('media_json') or '[]')
    tracks=[x for x in media if x.get('kind')=='captions' and
            (not x.get('language') or x['language'].lower().startswith('en'))]
    # One public English/default track per item; a caption failure never fails its article.
    if not tracks: return
    track=tracks[0]
    try:
        _,headers,raw,resolved=fetch(track['url'])
        value=caption_text(raw)
        if not value: raise ValueError('Caption cues contain no readable text.')
        track.update(transcript=value,fetch_status='available',resolved_url=resolved)
        result.update(transcript=value,transcript_status='available')
    except Exception as exc:
        track.update(fetch_status='failed',fetch_error=str(exc)[:300])
        result['transcript_status']='referenced'
    result['media_json']=json.dumps(media)
