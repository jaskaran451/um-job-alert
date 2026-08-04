#!/usr/bin/env python3
from __future__ import annotations
import argparse,json,os,urllib.error,urllib.request
from pathlib import Path

def load(path:Path):
    jobs=json.loads(path.read_text(encoding='utf-8')).get('seen_jobs',[])
    if not isinstance(jobs,list): raise RuntimeError('Invalid seen_jobs list')
    return [j for j in jobs if isinstance(j,dict)]

def main():
    p=argparse.ArgumentParser();p.add_argument('--before',required=True,type=Path);p.add_argument('--after',required=True,type=Path);a=p.parse_args()
    old={str(j.get('id')) for j in load(a.before) if j.get('id')};jobs=[j for j in load(a.after) if j.get('id') and str(j['id']) not in old]
    base=os.getenv('SUBSCRIBER_API_URL','').strip().rstrip('/');key=os.getenv('SUBSCRIBER_API_KEY','').strip()
    if not base or not key: print('Subscriber dispatch is not configured; skipping.');return
    if not jobs: print('No new jobs to dispatch.');return
    req=urllib.request.Request(base+'/api/internal/dispatch',data=json.dumps({'jobs':jobs}).encode(),headers={'Content-Type':'application/json','X-Dispatch-Key':key},method='POST')
    try:
        with urllib.request.urlopen(req,timeout=60) as response: print('Subscriber dispatch:',response.read().decode())
    except urllib.error.HTTPError as exc: raise RuntimeError(f'Dispatch failed HTTP {exc.code}: {exc.read().decode(errors="replace")}') from exc
if __name__=='__main__': main()
