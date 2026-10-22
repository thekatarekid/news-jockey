"""Whole-corpus retrieval and event-specific, cohesion-checked clustering."""
import math
import re
from collections import Counter
from datetime import datetime
from .classification import tokens, identity


def feature_record(payload,data):
    title=payload['title'];words=set(tokens(title))
    entities={identity(e['name']) for e in data.get('entities',[]) if e.get('confidence',0)>=.65}
    # Subject entities in the headline are stronger than background mentions.
    subject={e for e in entities if e in title.lower() or any(w in words for w in tokens(e))}
    return {**payload,'data':data,'words':words,'entities':entities,'subject':subject,
            'stamp':datetime.fromisoformat(payload['published_at']).timestamp() if payload.get('published_at') else None}


def vocabulary(records):
    df=Counter(w for r in records for w in r['words']);n=len(records)
    return {w:math.log((n+1)/(count+1))+1 for w,count in df.items()}


def headline_entities(record,kind):
    text=' '.join(tokens(record['title']))
    names=[]
    for e in record['data'].get('locations' if kind=='location' else 'entities',[]):
        if e.get('type')!=kind:continue
        name=' '.join(tokens(e['name']))
        if name and name in text:names.append(name)
    return set(names)


def conflicting_subjects(a,b):
    # Allow short/full aliases (iPhone / iPhone 18 Pro), not Pro / Air variants.
    return bool(a and b and not any(x==y or x in y or y in x for x in a for y in b))


def event_guards(left,right):
    titles=[r['title'].lower() for r in (left,right)];hard=[]
    puzzles=[]
    for t in titles:
        found=set(re.findall(r'\b(?:wordle|strands|pips|hurdle|quordle|connections|crossword|sudoku)\b',t))
        if 'connections' in found and 'sports' in t:found={'connections sports'}
        puzzles.append(found)
    if puzzles[0] and puzzles[1] and puzzles[0].isdisjoint(puzzles[1]):hard.append('Different daily puzzle titles')
    shopping=[bool(re.search(r'\b(?:deals?|discount|prime day|sale|save|off)\b',t)) for t in titles]
    if all(shopping):
        product=[headline_entities(r,'product') for r in (left,right)]
        if conflicting_subjects(*product):hard.append('Different products/offers')
        families=[set(re.findall(r'\b(?:speakers?|earbuds?|headphones?|iphone|macbook|pokemon|pokémon|vacuum|television|tv|kindle|tablet|ipad|monitor|laptop)\b',t)) for t in titles]
        families=[{w.rstrip('s').replace('pokémon','pokemon') for w in f} for f in families]
        if families[0] and families[1] and families[0].isdisjoint(families[1]):hard.append('Different shopping product categories')
    for pattern in (r'\bweek \d+\b',r'\b(?:macbook pro|macbook air|macbook neo)\b'):
        a,b=(set(re.findall(pattern,t)) for t in titles)
        if a and b and a.isdisjoint(b):hard.append('Different named edition or product variant')
    if any('high school' in t or 'weather forecast' in t for t in titles):
        places=[headline_entities(r,'location') for r in (left,right)]
        if conflicting_subjects(*places):hard.append('Different local coverage areas')
    def stage(record):
        # Read headline first; subordinate "after" clauses are background.
        main=re.split(r'\b(?:after|following)\b',record['title'].lower())[0]
        for label,pattern in [('administrative leave',r'on (?:paid |administrative )?leave'),('execution failed',r'botched execution|failed execution'),('execution authorized',r'allows? execution|execution.*go forward'),
          ('injury',r'injur|out of practice|miss.*(?:game|practice)|remain out|not optimistic.*plays'),('signing',r'\bsigns?\b.*(?:roster|deal|contract|cb|qb|wr)'),
          ('trade',r'\btrad(?:e|es|ed|ing)\b'),('dismissal',r'dismiss.*(?:lawsuit|case|charges)|bars? .*charg'),
          ('lawsuit',r'files? .*lawsuit|\bsues?\b'),('charges',r'\bindict|\bcharged\b'),('court ruling',r'\bruling\b|court rules'),
          ('appeal',r'\bappeal(?:s|ed)?\b')]:
            if re.search(pattern,main):return label
        # Reaction pieces can share the concrete event that triggered them.
        lead=record.get('text','')[len(record['title']):][:350].lower()
        if re.search(r'\btrad(?:e|es|ed|ing)\b',lead):return 'trade'
        return None
    # Fund distributions and historical profiles have named subjects that a
    # broad company/team embedding can otherwise collapse incorrectly.
    if all(re.search(r'\bdeclares?\b.*\bdividend\b',t) for t in titles):
        issuers=[re.split(r'\bdeclares?\b',t)[0].strip() for t in titles]
        amounts=[set(re.findall(r'\$\d+(?:\.\d+)?',t)) for t in titles]
        if issuers[0]!=issuers[1] or (all(amounts) and amounts[0].isdisjoint(amounts[1])):hard.append('Different dividend instrument or declaration')
    if any(re.search(r'jersey history|biography|life of|career of',t) for t in titles):
        if conflicting_subjects(headline_entities(left,'person'),headline_entities(right,'person')):hard.append('Different profile subjects')
    if all(re.search(r'\bbest\b.*\bloans?\b',t) for t in titles):
        guides=[set(tokens(t))-{'best','business','loan','loans','october','2026'} for t in titles]
        if guides[0]!=guides[1]:hard.append('Different loan recommendation categories')
    a,b=stage(left),stage(right)
    if (a in ('injury','trade','signing') and b is None) or (b in ('injury','trade','signing') and a is None):
        hard.append('Specific sports development versus broader team coverage')
    if a and b and a!=b:hard.append('Different event stages: '+a+' / '+b)
    if a==b=='injury' and conflicting_subjects(headline_entities(left,'person'),headline_entities(right,'person')):hard.append('Different injured people')
    return hard


def compare(left,right,title_sim,lead_sim,idf):
    a,b=left['words'],right['words'];common=a&b
    weight=lambda words:sum(idf.get(w,1)**2 for w in words)
    lexical=weight(common)/math.sqrt(max(1,weight(a)*weight(b)))
    shared=left['entities']&right['entities'];subject=left['subject']&right['subject']
    # NER boundaries differ across publishers ("TikToker Brooke Eby" versus
    # "Brooke Eby") and the model occasionally misses a plainly named subject.
    # A name extracted in either article and evidenced in BOTH headlines is a
    # shared subject even if the other extraction omitted it. No fuzzy surname
    # or generic-role matching: these can conflate different people/events.
    headline_text=[' '+' '.join(tokens(r['title']))+' ' for r in (left,right)]
    generic={'police','court','government','company','team','president','student','female student','male student','football','basketball','baseball'}
    for entity in left['entities']|right['entities']:
        name=' '.join(tokens(entity))
        if len(name)>=4 and name not in generic and all(' '+name+' ' in title for title in headline_text):
            shared.add(entity);subject.add(entity)
    semantic=.55*float(title_sim)+.45*float(lead_sim)
    hours=abs(left['stamp']-right['stamp'])/3600 if left['stamp'] and right['stamp'] else 0
    hard=event_guards(left,right)
    for pattern in (r'\bq[1-4]\b',r'\bgame \d+\b',r'\b20\d{2}\b',r'\b(?:october|oct\.?|september|sept\.?)\s+\d{1,2}\b'):
        x=set(re.findall(pattern,left['title'].lower()));y=set(re.findall(pattern,right['title'].lower()))
        if x and y and x.isdisjoint(y):hard.append('Different headline event identifier/date')
    actions=left['data'].get('action','unknown'),right['data'].get('action','unknown')
    if set(actions) in ({'arrest','court ruling'},{'charges','court ruling'},{'death','investigation'},{'earnings','acquisition'},{'appeal','court ruling'}):hard.append('Different event stages')
    recurring=any(re.search(r'\b(stock market today|daily|horoscope|wordle|connections|weather forecast|game highlights|power rankings)\b',r['title'],re.I) for r in (left,right))
    if recurring and hours>20:hard.append('Different dated recurring editions')
    if hours>168:hard.append('Delayed coverage requires review')
    game_ids=[set(re.findall(r'\bgame ([1-7])\b',r.get('text',r['title'])[:350],re.I)) for r in (left,right)]
    if game_ids[0] and game_ids[1] and game_ids[0].isdisjoint(game_ids[1]):hard.append('Different game numbers in event lead')
    exact=' '.join(tokens(left['title']))==' '.join(tokens(right['title'])) and len(a)>=4
    same=(exact and hours<=72) or (semantic>=.95 and lexical>=.10 and subject) or (semantic>=.96 and lexical>=.14 and shared and actions[0]==actions[1] and actions[0]!='unknown') or (semantic>=.92 and lexical>=.30 and (subject or lexical>=.55)) or (semantic>=.88 and lexical>=.55 and shared) or (semantic>=.85 and lexical>=.75)
    # Very close paraphrases can identify the participant only in the lead
    # (e.g. headlines saying "new broadcaster" instead of the person's name).
    if semantic>=.975 and lexical>=.35 and shared and hours<=72:same=True
    # Long-delay matches need stronger event-specific evidence.
    if hours>72 and not (semantic>=.91 and lexical>=.5):same=False
    if not left['title'] or not right['title']:same=False
    match_cue=lambda r:bool(re.search(r'\bvs\.?\s|\b(?:game|halftime|quarter|kickoff|final score|tnf)\b|thursday night football',r['title'],re.I))
    sports_common=common-set('week football game games score scores live updates update nfl mlb nba nhl discussion night thursday sunday monday watch highlights plays season preview picks predictions best betting college odds start time player players'.split())
    if match_cue(left) and match_cue(right) and len(sports_common)>=2 and hours<=24 and semantic>=.94:
        # Different coverage angles of one identified matchup can share a story.
        hard=[h for h in hard if h!='Specific sports development versus broader team coverage']
        same=True
    if hard:same=False
    decision='same_event' if same else 'review' if not hard and semantic>=.72 and (lexical>=.18 or subject) else 'reject'
    reason='; '.join(hard) if hard else ('Matching event language, participants and semantic context' if same else 'Related coverage; same event not established')
    return dict(decision=decision,score=round(semantic,4),title_similarity=round(float(title_sim),4),lead_similarity=round(float(lead_sim),4),lexical=round(lexical,4),shared_entities=sorted(shared),hours=round(hours,1),reason=reason)


def cluster(records,title_vectors,lead_vectors,progress=None,neighbors=32):
    import numpy as np
    n=len(records)
    if not n:return [],[],{'candidate_pairs':0,'accepted_edges':0}
    tv=np.asarray(title_vectors,dtype=np.float32);lv=np.asarray(lead_vectors,dtype=np.float32)
    idf=vocabulary(records);pairs={};k=min(neighbors,n)
    # Full-month search, bounded memory. No batch-local clustering.
    for start in range(0,n,128):
        t=tv[start:start+128]@tv.T;l=lv[start:start+128]@lv.T;s=.55*t+.45*l
        choices=np.argpartition(s,n-k,axis=1)[:,-k:]
        for offset,indices in enumerate(choices):
            i=start+offset
            for j in indices:
                j=int(j)
                if j==i:continue
                key=(min(i,j),max(i,j))
                if key in pairs:continue
                if s[offset,j]<.70:continue
                result=compare(records[i],records[j],t[offset,j],l[offset,j],idf)
                if result['decision']!='reject':pairs[key]=result
        if progress:progress('finding candidate matches',min(n,start+128),n)
    edges=sorted(((v['score'],a,b,v) for (a,b),v in pairs.items() if v['decision']=='same_event'),reverse=True)
    parent=list(range(n));members={i:[i] for i in range(n)};anchors={i:i for i in range(n)}
    def root(i):
        while parent[i]!=i:parent[i]=parent[parent[i]];i=parent[i]
        return i
    rejected_cohesion=0
    for _,a,b,result in edges:
        x,y=root(a),root(b)
        if x==y:continue
        if len(members[x])<len(members[y]):x,y=y,x
        anchor=anchors[x]
        # Every incoming member must match the stable representative. Also check
        # both cluster representatives against the farthest existing member.
        checks=[(anchor,m) for m in members[y]]
        farthest=min(members[x],key=lambda m:float(tv[anchor]@tv[m]))
        checks.append((farthest,anchors[y]))
        if any(compare(records[i],records[j],tv[i]@tv[j],lv[i]@lv[j],idf)['decision']!='same_event' for i,j in checks):
            rejected_cohesion+=1;continue
        parent[y]=x;members[x]+=members.pop(y)
    groups=[{'anchor':anchors[r],'members':ids} for r,ids in members.items()]
    groups.sort(key=lambda g:(-len(g['members']),records[g['anchor']]['id']))
    candidates=[{'left':a,'right':b,**v} for (a,b),v in pairs.items()]
    return groups,candidates,dict(candidate_pairs=len(pairs),accepted_edges=len(edges),cohesion_rejections=rejected_cohesion,neighbors=neighbors)
