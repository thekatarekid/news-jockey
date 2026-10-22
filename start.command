#!/bin/zsh
cd "${0:A:h}"
if [[ ! -x .venv/bin/python ]]; then
  python3 -m venv .venv || exit 1
fi
if ! .venv/bin/python -c 'import flask, feedparser, requests, bs4, trafilatura, readability, defusedxml, playwright, bleach' >/dev/null 2>&1; then
  .venv/bin/python -m pip install -r requirements.txt || exit 1
fi
.venv/bin/python tools/portal_service.py start || exit 1
open http://127.0.0.1:8765
echo "You can close this Terminal window. The background service keeps running."
echo "To stop: .venv/bin/python tools/portal_service.py stop"
