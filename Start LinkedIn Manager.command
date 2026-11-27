#!/bin/bash
# Double-click this file in Finder to start the LinkedIn Manager.
cd "$(dirname "$0")" || exit 1
echo "Starting LinkedIn Manager... (close this window to stop it)"
python3 linkedin_manager/server.py --open
