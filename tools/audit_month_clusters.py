"""Read-only audit of current feature coverage; does not publish sample clusters."""
import argparse,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from newsjockey.storage import connect
from newsjockey.event_clustering import feature_record,cluster
p=argparse.ArgumentParser();p.add_argument('--database',default=str(ROOT/'data/newsjockey.sqlite3'));p.add_argument('--job',required=True);p.add_argument('--output',required=True);a=p.parse_args()
with connect(a.database) as db:
 rows=db.execute('SELECT i.input,f.* FROM corpus_items i JOIN corpus_features f ON f.article_id=i.article_id AND f.input_hash=i.input_hash WHERE i.run_id=? ORDER BY i.article_id',(a.job,)).fetchall()
records=[feature_record(json.loads(r['input']),json.loads(r['data'])) for r in rows]
groups,pairs,report=cluster(records,[json.loads(r['title_vector']) for r in rows],[json.loads(r['lead_vector']) for r in rows])
report.update(articles=len(rows),groups=len(groups),multi=sum(len(g['members'])>1 for g in groups),singleton=sum(len(g['members'])==1 for g in groups))
result={'report':report,'clusters':[{'articles':[{'id':records[i]['id'],'title':records[i]['title'],'publisher_id':records[i]['publisher_id']} for i in g['members']]} for g in groups if len(g['members'])>1],
 'borderline':[{'a':records[r['left']]['title'],'b':records[r['right']]['title'],**r} for r in sorted(pairs,key=lambda p:p['score'],reverse=True) if r['decision']=='review']}
Path(a.output).write_text(json.dumps(result,indent=2));print(report)
