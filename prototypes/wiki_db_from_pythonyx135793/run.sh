#!/bin/sh
# One-command start: sh prototypes/wiki_db/run.sh  ->  http://127.0.0.1:8000
# Demo login is on by default and "admin" is the demo admin. Set WIKI_DEMO=0 to use only OAuth.
cd "$(dirname "$0")" || exit 1
export WIKI_DEMO="${WIKI_DEMO:-1}"
export WIKI_ADMINS="${WIKI_ADMINS:-demo:admin}"
exec python3 app.py
