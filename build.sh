#!/usr/bin/env bash
# Render runs this on every deploy.
set -o errexit
pip install -r requirements.txt
python manage.py collectstatic --no-input
python manage.py migrate --no-input
python manage.py createcachetable
python manage.py secure_supabase      # lock Supabase's public Data API out of every table
python manage.py check --deploy --fail-level ERROR
python manage.py ensure_founder
