"""Read-only scan of a Kodi profile; never writes to that profile."""
from pathlib import Path
import argparse
import json
import sys
import tempfile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'addon/resources/lib'))
from buildfixes.engine import Engine
from buildfixes.backend import XmlBackend

def main():
    parser=argparse.ArgumentParser();parser.add_argument('kodi_home',type=Path);args=parser.parse_args()
    catalog=json.loads((ROOT/'addon/resources/lib/buildfixes/recipes.json').read_text())
    with tempfile.TemporaryDirectory() as profile:
        engine=Engine(args.kodi_home,profile,XmlBackend(args.kodi_home),catalog)
        print(json.dumps([{'id':p.id,'status':p.status,'reason':p.reason,'versions':p.versions,
                           'files_to_change':[f.path for f in p.files],'restart_jobs':p.jobs} for p in engine.scan()],indent=2))

if __name__=='__main__':main()
