#!/usr/bin/env sh
cd "$(dirname "$0")" || exit 1
if [ ! -x venv/bin/python ]; then echo "Set up first: see README.md in this folder."; exit 1; fi
exec venv/bin/python transcribe_server.py
