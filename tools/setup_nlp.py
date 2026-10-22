#!/usr/bin/env python3
"""Install the isolated free local NLP runtime and download its model once."""
from pathlib import Path
import os,shutil,subprocess
ROOT=Path(__file__).resolve().parents[1]
uv=shutil.which('uv')
if not uv:raise SystemExit('Install uv first, then rerun this script.')
env=dict(os.environ,UV_CACHE_DIR=str(ROOT/'data/runtime/uv-cache'),HF_HOME=str(ROOT/'data/models/huggingface'))
python=ROOT/'data/runtime/nlp-env/bin/python'
if not python.exists():subprocess.run([uv,'venv','--python','3.12',str(python.parent.parent)],env=env,check=True)
subprocess.run([uv,'pip','install','--python',str(python),'-r',str(ROOT/'requirements-nlp.txt')],env=env,check=True)
subprocess.run([str(python),'-c',"from gliner2 import GLiNER2; GLiNER2.from_pretrained('fastino/gliner2-base-v1')"],env=env,check=True)
print('Local GLiNER2 is ready. Inference uses the downloaded snapshot with network access disabled.')
