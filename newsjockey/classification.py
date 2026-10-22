"""Evidence-preserving free baseline and validation of structured model decisions."""
import hashlib
import json
import re
import unicodedata
from collections import Counter
from datetime import datetime
from .storage import CONTENT_TYPES

CATEGORY_TERMS = {
 'Politics & Government': 'election|senate|congress|president|governor|legislation|republican|democrat|white house|ballot',
 'Business & Economy': 'economy|economic|company|companies|merger|acquisition|inflation|tariff|earnings|revenue|business',
 'Finance & Markets': 'stock|stocks|shares|investor|investors|bond|bonds|federal reserve|interest rate|nasdaq|dividend|etf',
 'Technology': 'software|artificial intelligence|ai|apple|google|microsoft|cybersecurity|iphone|chip|chips|openai|computer',
 'Science': 'scientist|scientists|research study|physics|astronomy|nasa|spacecraft|planet|quantum|telescope',
 'Health': 'health|hospital|vaccine|cancer|patient|patients|disease|medical|medicine|fda|dementia',
 'Entertainment & Culture': 'movie|film|actor|actress|music|singer|concert|hollywood|television|netflix|album|cinema',
 'Sports': 'nfl|nba|mlb|nhl|football|basketball|baseball|soccer|tennis|olympics|touchdown|quarterback|playoffs|athlete',
 'World & Diplomacy': 'diplomacy|diplomatic|foreign minister|united nations|ceasefire|treaty|israel|ukraine|iran|nato|war',
 'Crime & Justice': 'court|judge|ruling|lawsuit|arrest|arrested|murder|police|trial|convicted|indicted|appeal|prosecutor',
 'Climate & Environment': 'climate|carbon|emissions|global warming|pollution|biodiversity|conservation|renewable',
 'Weather & Disasters': 'hurricane|earthquake|flood|flooding|wildfire|tornado|storm|drought|tsunami|evacuation',
 'Education': 'school|schools|college|university|student|students|teacher|teachers|tuition|campus',
 'Labor': 'union|strike|workers|layoffs|wages|employment|unemployment|collective bargaining',
 'Housing': 'housing|mortgage|rent|rental|home prices|real estate|homelessness',
 'Transportation': 'airline|airport|railway|railroad|transit|traffic|automaker|electric vehicle|highway|robotaxi',
 'Lifestyle': 'recipe|recipes|travel|fashion|fitness|wellness|cooking|shopping|vacation|beauty',
 'Religion': 'church|pope|vatican|religion|religious|christian|muslim|judaism|hindu|buddhist'
}
CATEGORIES = list(CATEGORY_TERMS)
SCOPES = ['National','State','Local','International','Unknown']
STOP = set('the a an of in on at by for to and or with from as is was were be been are has have had it its this that these those how why what when where who will would could should new news latest says said after before over about into amid more their they his her he she us our your all not than today yesterday tomorrow live updates update report reports reporting story stories'.split())
MONTHS = 'January February March April May June July August September October November December'.split()
KNOWN_ORGS = ['Supreme Court','White House','Federal Reserve','United Nations','OpenAI','Google','Apple','Microsoft','Meta','Amazon','Tesla','Nvidia','Netflix','NATO','FDA','NASA','FBI','NFL','NBA','MLB','NHL']
LOCATIONS = ['United States','Washington','New York','California','Texas','Florida','Ohio','Georgia','Illinois','Boston','Chicago','Los Angeles','San Francisco','London','Israel','Ukraine','Russia','Iran','China','India','Canada','France','Germany','Japan','Gaza','Lebanon','United Kingdom']
ALIASES = {'u.s.':'united states','us':'united states','fed':'federal reserve','donald trump':'trump','president trump':'trump'}
ACTION_PATTERNS = [('appeal',r'\bappeal(?:s|ed|ing)?\b'),('court ruling',r'\b(?:ruling|ruled|rules|judge orders|court orders)\b'),
 ('investigation',r'\b(?:investigat\w*|probe)\b'),('charges',r'\b(?:indict\w*|charged|charges)\b'),('arrest',r'\barrest\w*\b'),
 ('earnings',r'\b(?:earnings|quarterly results)\b'),('acquisition',r'\b(?:merger|acquisition|acquire\w*)\b'),
 ('launch',r'\b(?:launch\w*|unveil\w*|release[sd]?)\b'),('resignation',r'\b(?:resign\w*|steps down)\b'),
 ('death',r'\b(?:dies|died|dead|death)\b'),('election',r'\b(?:election|elected|vote[sd]?)\b'),
 ('match',r'\b(?:defeats?|beats?|win[sn]?|victory|game|match)\b'),('strike',r'\b(?:strike|walkout)\b'),('announcement',r'\bannounc\w*\b')]

def tokens(text):
    return [w for w in re.findall(r'[a-z0-9]+',text.lower()) if len(w)>2 and w not in STOP]

def identity(name):
    value=' '.join(name.lower().split())
    return ALIASES.get(value,value)

def source_text(row):
    row=dict(row); body=(row.get('content') or '').strip(); feed=(row.get('feed_content') or '').strip()
    suspected=bool(re.match(r"this article was written by\b",body,re.I))
    if suspected: body=feed if feed and feed!=body else ''
    basis='feed; rejected biography-only body' if suspected else ('article body' if row.get('body_quality')=='full' else 'limited/feed text')
    if not body: body=row.get('transcript') or ''; basis='publisher transcript' if body else 'headline only'
    text=(row['title']+'\n\n'+body).strip()
    return text,basis,suspected

def input_hash(row):
    text,basis,_=source_text(row)
    return hashlib.sha256(json.dumps([text,basis,dict(row).get('published_at'),dict(row).get('content_type'),dict(row).get('content_type_basis')]).encode()).hexdigest()

def passage(text,start,end):
    lo=max(text.rfind('\n',0,start),text.rfind('. ',0,start))+1
    hi=text.find('\n',end)
    if hi<0: hi=min(len(text),end+180)
    return text[lo:hi].strip()[:400]

def span(text,quote):
    if not isinstance(quote,str) or not quote.strip():return None
    start=text.find(quote)
    if start>=0:return {'quote':quote,'start':start,'end':start+len(quote)}
    # Tolerate typography/spacing, while retaining exact original text and offsets.
    def canonical(value):
        chars=[];offsets=[]
        translation=str.maketrans({'“':'"','”':'"','‘':"'",'’':"'",'–':'-','—':'-'})
        for i,c in enumerate(value):
            for part in unicodedata.normalize('NFKC',c).translate(translation):
                part=' ' if part.isspace() else part
                if part==' ' and (not chars or chars[-1]==' '):continue
                # HTML inline-link boundaries sometimes add a space before punctuation.
                if part in ',.;:!?' and chars and chars[-1]==' ':
                    chars.pop();offsets.pop()
                chars.append(part);offsets.append(i)
        return ''.join(chars),offsets
    haystack,offsets=canonical(text);needle,_=canonical(quote.strip());start=haystack.find(needle)
    if start<0:return None
    lo=offsets[start];hi=offsets[start+len(needle)-1]+1
    return {'quote':text[lo:hi],'start':lo,'end':hi}

def baseline(row):
    row=dict(row);text,basis,suspect=source_text(row); title=row['title']; lead=text[:12000]
    categories=[]
    for name,terms in CATEGORY_TERMS.items():
        matches=list(re.finditer(r'\b(?:'+terms+r')\b',lead,re.I))
        if matches:
            evidence=passage(text,matches[0].start(),matches[0].end())
            confidence=min(.92,.48+.10*len(set(m.group().lower() for m in matches))+.15*bool(re.search(r'\b(?:'+terms+r')\b',title,re.I)))
            if confidence>=.65:categories.append({'name':name,'confidence':round(confidence,2),'evidence':span(text,evidence)})
    entities=[];locations=[]
    for name in KNOWN_ORGS+LOCATIONS:
        m=re.search(r'\b'+re.escape(name)+r'\b',lead,re.I)
        if m:
            entity={'name':m.group(),'type':'location' if name in LOCATIONS else 'organization','confidence':.8,'evidence':span(text,passage(text,m.start(),m.end()))}
            (locations if name in LOCATIONS else entities).append(entity)
    for m in re.finditer(r'\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,2}\b',lead):
        name=m.group()
        if any(w.lower() in STOP or w in MONTHS for w in name.split()) or name in KNOWN_ORGS+LOCATIONS:continue
        if len(entities)>=25:break
        if not any(e['name']==name for e in entities):entities.append({'name':name,'type':'unknown','confidence':.5,'evidence':span(text,passage(text,m.start(),m.end()))})
    action=next((a for a,p in ACTION_PATTERNS if re.search(p,title,re.I)),'unknown')
    event_time={'value':None,'precision':'unknown','evidence':None,'confidence':0}
    # Never substitute the publication timestamp for the time of the event.
    date_rx=r'\b(?:on|occurred|happened|held)\s+((?:'+ '|'.join(MONTHS)+r')\s+\d{1,2},?\s+20\d{2}|20\d{2}-\d{2}-\d{2})\b'
    match=re.search(date_rx,lead,re.I)
    if match:
        raw=match[1]
        for fmt in ('%Y-%m-%d','%B %d, %Y','%B %d %Y'):
            try:
                value=datetime.strptime(raw,fmt).date().isoformat();event_time={'value':value,'precision':'date','evidence':span(text,passage(text,match.start(),match.end())),'confidence':.8};break
            except ValueError:pass
    refined=row.get('content_type','unknown')
    if refined=='unknown':
        for kind,p in [('opinion',r'^opinion\b|\bin my view\b'),('analysis',r'^analysis\b'),('review',r'\breview\b'),('explainer',r'\bexplained\b|^what to know')]:
            if re.search(p,title,re.I):refined=kind;break
    scope='International' if any(x['name'] in LOCATIONS[16:] for x in locations) else 'National' if re.search('president|congress|supreme court|federal',lead,re.I) else 'Unknown'
    return {'categories':categories,'entities':entities,'locations':locations,'event_time':event_time,
      'content_type':refined,'content_type_confidence':.8 if row.get('content_type','unknown')!='unknown' else .55,
      'geographic_scope':scope,'action':action,'event_details':discriminators(text),
      'input_suspect':suspect,'input_limited':basis not in ('article body','publisher transcript'),
      'topic_hints':[],'confidence':.55,'method':'local rules v1','warnings':(['Suspected biography/disclaimer extraction; body excluded.'] if suspect else [])+
      (['Limited input; review before automatic grouping.'] if basis not in ('article body','publisher transcript') else [])}

def discriminators(text):
    return sorted(set(m.group().lower() for m in re.finditer(r'\b(?:Q[1-4]|(?:first|second|third|fourth) quarter|game \d+|20\d{2}|\d+-\d+|\d+\s*(?:million|billion))\b',text[:1800],re.I)))

def validate_decision(data,row):
    if not isinstance(data,dict): raise ValueError('Model result must be an object.')
    text,basis,suspected=source_text(row); result=baseline(row);warnings=[]
    cats=[]
    for item in data.get('categories',[]):
        if item.get('name') not in CATEGORIES: raise ValueError('Model returned an unknown news category.')
        evidence=span(text,item.get('quote',''))
        if evidence:cats.append({'name':item['name'],'confidence':clamp(item.get('confidence')),'evidence':evidence})
        else:warnings.append('Dropped category '+str(item.get('name',''))+': no matching supporting passage.')
    result['categories']=cats
    for field in ('entities','locations'):
        valid=[]
        for e in data.get(field,[])[:40]:
            evidence=span(text,e.get('quote',''))
            if evidence and e.get('name') and e['name'].lower() in evidence['quote'].lower():
                valid.append({'name':e['name'][:160],'type':e.get('type','unknown'),'confidence':clamp(e.get('confidence')),'evidence':evidence})
            else:warnings.append('Dropped '+field+' value '+str(e.get('name',''))[:100]+': unmatched mention/evidence.')
        result[field]=valid
    event=data.get('event_time') or {};evidence=span(text,event.get('quote',''))
    result['event_time']={'value':None,'precision':'unknown','confidence':0,'evidence':None}
    if event.get('value') and evidence and event.get('precision') in ('date','approximate','datetime'):
        try:datetime.fromisoformat(event['value'].replace('Z','+00:00'))
        except (ValueError,TypeError):warnings.append('Dropped invalid event time.')
        else:result['event_time']={'value':event['value'],'precision':event['precision'],'confidence':clamp(event.get('confidence')),'evidence':evidence}
    kind=data.get('content_type','unknown')
    if kind not in CONTENT_TYPES: raise ValueError('Invalid content type.')
    if dict(row).get('content_type_basis')!='Manual correction':result['content_type']=kind
    result['content_type_confidence']=clamp(data.get('content_type_confidence'))
    scope=data.get('geographic_scope','Unknown');result['geographic_scope']=scope if scope in SCOPES else 'Unknown'
    result['action']=str(data.get('action') or 'unknown')[:120]
    result['event_details']=discriminators(text)
    result['topic_hints']=[]
    for hint in data.get('topic_hints',[])[:3]:
        evidence=span(text,hint.get('quote',''));title=hint.get('title','');description=hint.get('description','')
        if evidence and 2<=len(title.split())<=6 and description:
            result['topic_hints'].append({'title':title,'description':description[:700],'evidence':evidence})
    result['confidence']=clamp(data.get('confidence'));result['warnings']+=warnings
    return result

def clamp(value):
    try:return max(0,min(1,float(value)))
    except (TypeError,ValueError):return 0

def cosine(left,right):
    if not left or not right or len(left)!=len(right):return 0
    import math
    denom=math.sqrt(sum(x*x for x in left)*sum(x*x for x in right))
    return sum(x*y for x,y in zip(left,right))/denom if denom else 0

def lexical_similarity(a,b):
    a=Counter(tokens(a));b=Counter(tokens(b));keys=a.keys()|b.keys()
    return cosine([a[k] for k in keys],[b[k] for k in keys])
