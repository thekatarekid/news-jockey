from flask import jsonify,request
from .corpus import initialize_corpus,CorpusWorker,create_corpus_run,overview,control
from .storage import connect

def register_corpus_routes(app):
    path=app.config['DATABASE'];initialize_corpus(path)
    app.extensions['corpus_worker']=CorpusWorker(path)
    @app.get('/api/corpus')
    def corpus_overview():return jsonify(overview(path))
    @app.post('/api/corpus/jobs')
    def corpus_create():return jsonify(create_corpus_run(path,request.get_json().get('month'))),202
    @app.patch('/api/corpus/jobs/<rid>')
    def corpus_control(rid):
        control(path,rid,request.get_json().get('action'));return jsonify(ok=True)
