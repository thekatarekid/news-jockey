"""Explicit, bounded subscription audit of local event decisions. Never mutates clusters."""
import argparse,json,random,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from newsjockey.storage import connect
from newsjockey.processing_adapters import subscription_decision,object_schema,array_schema,STRING,NUMBER
p=argparse.ArgumentParser();p.add_argument('--audit',required=True);p.add_argument('--output',required=True);args=p.parse_args()
x=json.loads(Path(args.audit).read_text());rng=random.Random(761);clusters=x['clusters'];chosen=rng.sample(clusters,min(35,len(clusters)))
pairs=[]
with connect(ROOT/'data/newsjockey.sqlite3') as db:
 snapshots=[json.loads(r[0]) for r in db.execute('SELECT input FROM corpus_items')]
 by_id={r['id']:r for r in snapshots};by_title={r['title']:r for r in snapshots}
 for g in chosen:
  articles=g['articles'];a,b=rng.sample(articles,2)
  rows=[]
  for aid in [a['id'],b['id']]:
   r=by_id[aid]
   rows.append(dict(title=r['title'],published_at=r['published_at'],lead=r['text']))
  pairs.append(dict(id=len(pairs),local_decision='same_event',article_ids=[a['id'],b['id']],left=rows[0],right=rows[1]))
 for pair in rng.sample(x['borderline'][:150],min(25,len(x['borderline'][:150]))):
  rows=[]
  for title in [pair['a'],pair['b']]:
   r=by_title[title]
   rows.append(dict(title=r['title'],published_at=r['published_at'],lead=r['text']))
  pairs.append(dict(id=len(pairs),local_decision='review',left=rows[0],right=rows[1]))
schema=object_schema({'pairs':array_schema({'id':{'type':'integer'},'decision':{'type':'string','enum':['same_event','different_event','uncertain']},'reason':STRING})})
result,usage=subscription_decision('luna','Assess every pair independently. Are they coverage of the same specific event/development, versus merely the same ongoing subject? Different daily puzzles/products/places/matches/earnings quarters, or an arrest versus a later ruling, are DIFFERENT events. Preview and result of the SAME explicitly identified sports match may share a story. Reaction/analysis about the same concrete announcement may share a story. Do not insist on identical wording. Use only evidence provided, not outside knowledge. Return one label for each ID. Unclear = uncertain.',{'pairs':pairs},schema)
assert {p['id'] for p in result['pairs']}==set(range(len(pairs)))
Path(args.output).write_text(json.dumps({'inputs':pairs,'results':result,'usage':usage,'note':'Model-assisted diagnostic sample, not independently adjudicated ground truth.'},indent=2));print('Audited',len(pairs),'pairs',usage)
