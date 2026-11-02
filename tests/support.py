from pathlib import Path
import json
import shutil
import sys
import xml.etree.ElementTree as ET

PROJECT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(PROJECT/'addon/resources/lib'))
from buildfixes.backend import XmlBackend
from buildfixes.engine import Engine

CATALOG=json.loads((PROJECT/'addon/resources/lib/buildfixes/recipes.json').read_text())


def make_engine(base):
    base=Path(base)
    root=base/'kodi';profile=base/'toolkit'
    shutil.copytree(PROJECT/'tests/fixtures',root)
    for addon in (root/'addons').iterdir():
        if not (addon/'addon.xml').exists():
            (addon/'addon.xml').write_text('<addon id="'+addon.name+'" version="99.0.0"/>')
    # A synthetic route contract; no account information is copied into tests.
    router=root/'addons/plugin.video.themoviedb.helper/resources/tmdbhelper/lib/script/router.py'
    router.parent.mkdir(parents=True,exist_ok=True)
    router.write_text("ROUTES = {'sync_options': None}\n")
    settings=root/'addons/skin.xenon2/resources/scripts/reauthorize/main/resources/settings.xml'
    settings.parent.mkdir(parents=True,exist_ok=True)
    settings.write_text('''<settings><setting type="action" label="Authorize Trakt for Kodi" option="close" action="RunScript(script.module.reauthorize, action=traktAuth)" visible="eq(-6,)"/><setting id="unrelated" default="yes"/></settings>''')
    backend=XmlBackend(root)
    backend.set_setting('plugin.video.playlistbrowser','DLHD_base','https://dlive.sx')
    backend.set_setting('plugin.video.playlistbrowser','unrelated','preserve-me')
    engine=Engine(root,profile,backend,CATALOG)
    return engine
