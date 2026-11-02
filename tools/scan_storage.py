"""Read-only maintainer scan outside Kodi; installed/enabled state is unavailable."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'storage_viewer/resources/lib'))
from jamiestorageviewer.scan import CATEGORIES, local_roots, scan
from jamiestorageviewer.inventory import fallback_manifests


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('kodi_home', type=Path)
    args = parser.parse_args()
    roots, excluded = local_roots([(args.kodi_home, 'home', 'Kodi home')])
    if not roots:
        parser.error('An accessible local Kodi home folder is required.')
    snapshot = scan(roots)
    addons = fallback_manifests(snapshot, {})
    top = sorted(snapshot.addons, key=lambda key: -(snapshot.addons[key]['code']+snapshot.addons[key]['data']))[:12]
    print(json.dumps({'complete':snapshot.complete,'bytes':snapshot.bytes,'files':snapshot.files,
        'directories':len(snapshot.directories),'seconds':round(snapshot.elapsed,2),
        'skipped_links':snapshot.links,'hard_link_duplicates':snapshot.shared_links,'errors':snapshot.error_count,
        'categories':{CATEGORIES[key]:value for key,value in snapshot.categories.items()},
        'largest_addons':[{'id':key,'code_bytes':snapshot.addons[key]['code'],'data_bytes':snapshot.addons[key]['data'],
            'manifest_available':key in addons} for key in top],
        'dependency_inventory':'Local manifests only; Kodi installed/enabled state is unavailable.'},indent=2))


if __name__ == '__main__':
    main()
