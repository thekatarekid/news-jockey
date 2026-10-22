#!/usr/bin/env python3
"""Install a project-local official Ollama runtime and free EmbeddingGemma 2 model."""
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import tarfile
import urllib.request
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from newsjockey.storage import ROOT
from newsjockey.processing_adapters import start_embeddings, embedding_status, ollama_binary
VERSION='0.40.0'
def setup():
    if platform.system()!='Darwin':raise SystemExit('This setup script targets macOS. Install a compatible Ollama runtime on other systems.')
    target=ROOT/f'data/runtime/ollama-{VERSION}'
    if not (target/'ollama').exists():
        target.mkdir(parents=True,exist_ok=True)
        request=urllib.request.Request(f'https://api.github.com/repos/ollama/ollama/releases/tags/v{VERSION}',headers={'User-Agent':'NewsJockey-local-setup'})
        release=json.load(urllib.request.urlopen(request,timeout=30))
        asset=next(a for a in release['assets'] if a['name']=='ollama-darwin.tgz')
        archive=ROOT/f'data/runtime/ollama-{VERSION}.tgz'
        print('Downloading official Ollama',VERSION,flush=True)
        urllib.request.urlretrieve(asset['browser_download_url'],archive)
        expected=asset.get('digest')
        if not expected or not expected.startswith('sha256:'):raise RuntimeError('Official release did not provide a SHA-256 digest.')
        actual=hashlib.file_digest(archive.open('rb'),'sha256').hexdigest()
        if actual!=expected.split(':')[1]:raise RuntimeError('Runtime checksum mismatch; refusing to install.')
        with tarfile.open(archive) as tar:tar.extractall(target,filter='data')
    start_embeddings()
    env=dict(os.environ,OLLAMA_HOST='127.0.0.1:11435',OLLAMA_NO_CLOUD='1')
    subprocess.run([ollama_binary(),'pull','embeddinggemma-2'],env=env,check=True)
    print(json.dumps(embedding_status(),indent=2))
if __name__=='__main__':setup()
