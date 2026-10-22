#!/usr/bin/env python3
"""Bounded evaluation of a fixed labeled fixture; no paid services."""
import argparse
from collections import Counter
import itertools
import json
from pathlib import Path
import sys
import time
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from newsjockey.classification import baseline,cosine,source_text,CATEGORIES
from newsjockey.grouping import compare_events,topic_match,topic_keywords
from newsjockey.processing_adapters import embed,subscription_decision,PAIR_SCHEMA
from newsjockey.storage import connect,now,ROOT
from newsjockey.analysis_store import initialize_analysis,config

def metrics(c):
 tp,fp,fn=c['tp'],c['fp'],c['fn']
 p=tp/(tp+fp) if tp+fp else None;r=tp/(tp+fn) if tp+fn else None
 return dict(c,precision=round(p,4) if p is not None else None,recall=round(r,4) if r is not None else None,
             f1=round(2*p*r/(p+r),4) if p is not None and r is not None and p+r else 0)
def main():
 parser=argparse.ArgumentParser();parser.add_argument('--embeddings',action='store_true');parser.add_argument('--luna-pairs',type=int,default=0);parser.add_argument('--database');parser.add_argument('--output',default='artifacts/phase-3-5/evaluation.json');args=parser.parse_args()
 if not 0<=args.luna_pairs<=20:parser.error('--luna-pairs must be 0–20')
 fixture=json.loads((ROOT/'tests/fixtures/news_processing_eval.json').read_text());items=fixture['items'];data={a['id']:baseline(a) for a in items};vectors={};timings=[];category=Counter(tp=0,fp=0,fn=0);percat={c:Counter(tp=0,fp=0,fn=0) for c in CATEGORIES}
 for a in items:
  expected=set(a['categories']);actual={c['name'] for c in data[a['id']]['categories']}
  for c in expected|actual:percat[c]['tp' if c in expected&actual else 'fp' if c in actual else 'fn']+=1
  category.update(tp=len(expected&actual),fp=len(actual-expected),fn=len(expected-actual))
  if args.embeddings:
   v,m=embed(source_text(a)[0]);vectors[a['id']]=v;timings.append(m['elapsed_seconds'])
 outcomes=[];counts={m:Counter(tp=0,fp=0,fn=0,tn=0) for m in ['rules','rules+embedding']};model_calls=[]
 for left,right in itertools.combinations(items,2):
  expected=left['story']==right['story'];semantic=cosine(vectors.get(left['id']),vectors.get(right['id']));results={}
  for name,sim in [('rules',0),('rules+embedding',semantic)]:
   decision=compare_events(data[left['id']],data[right['id']],left,right,sim);pred=decision['decision']=='same_event'
   counts[name]['tp' if expected and pred else 'fn' if expected else 'fp' if pred else 'tn']+=1;results[name]=decision
  outcomes.append({'left':left['id'],'right':right['id'],'same_story':expected,'same_topic':left['topic']==right['topic'],**results})
 ambiguous=sorted([o for o in outcomes if o['rules+embedding']['decision']=='review'],key=lambda o:o['rules+embedding']['score'],reverse=True)[:args.luna_pairs]
 for o in ambiguous:
  a=next(a for a in items if a['id']==o['left']);b=next(a for a in items if a['id']==o['right'])
  result,m=subscription_decision('luna','Decide if these report exactly the same event. Same person or topic is insufficient. Check action, date, participants, game/quarter. Quote exact evidence from each.',{'left':source_text(a)[0],'right':source_text(b)[0]},PAIR_SCHEMA)
  pred=result['same_event'] and result['confidence']>=.98 and result['left_quote'] in source_text(a)[0] and result['right_quote'] in source_text(b)[0]
  model_calls.append({'left':o['left'],'right':o['right'],'expected':o['same_story'],'accepted':bool(pred),'result':result,'metrics':m})
 topic_counts=Counter(tp=0,fp=0,fn=0,tn=0)
 for o in outcomes:
  a=next(a for a in items if a['id']==o['left']);b=next(a for a in items if a['id']==o['right'])
  hint={'title':' '.join(a['title'].split()[:6]),'description':a['title']};topic={'title':' '.join(b['title'].split()[:6]),'description':b['title'],'keywords':json.dumps(topic_keywords(b['title'],b['title']))}
  # Raw-headline descriptor baseline is deliberately measured separately from model-produced topic scopes.
  pred,_,_=topic_match(hint,topic);expected=o['same_topic'];topic_counts['tp' if expected and pred else 'fn' if expected else 'fp' if pred else 'tn']+=1
 represented=[metrics(c)['f1'] for c in percat.values() if sum(c.values())]
 result={'dataset':fixture['version'],'limitations':fixture['note'],'article_count':len(items),'pair_count':len(outcomes),'categories_micro':metrics(category),'categories_macro_f1_represented':round(sum(represented)/len(represented),4),'category_results':{c:metrics(v) for c,v in percat.items() if sum(v.values())},'story_matching':{k:metrics(v) for k,v in counts.items() if args.embeddings or k=='rules'},'local_topic_seed_matching':metrics(topic_counts),'luna_ambiguous_pairs':model_calls,'embedding_seconds':{'total':round(sum(timings),3),'mean':round(sum(timings)/len(timings),3) if timings else None},'targets':{'same_event_precision':.98,'production_quality_established':False},'pairs':outcomes}
 out=Path(args.output);out.parent.mkdir(parents=True,exist_ok=True);out.write_text(json.dumps(result,indent=2))
 if args.database:
  initialize_analysis(args.database)
  with connect(args.database) as db:cid,_=config(db);summary={k:v for k,v in result.items() if k!='pairs'};db.execute('INSERT INTO processing_evaluations(created_at,config_id,dataset_version,results) VALUES(?,?,?,?)',(now(),cid,fixture['version'],json.dumps(summary)))
 print(json.dumps({k:v for k,v in result.items() if k not in ('pairs','category_results','luna_ambiguous_pairs')},indent=2))
if __name__=='__main__':main()
