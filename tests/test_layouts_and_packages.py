from pathlib import Path
import ast
import hashlib
import importlib.util
import json
import re
import tempfile
import unittest
import xml.etree.ElementTree as ET
import zipfile
import io
from support import make_engine,PROJECT


class LayoutTests(unittest.TestCase):
    def test_changed_calendar_structure_is_reported_without_stopping_the_scan(self):
        with tempfile.TemporaryDirectory() as temp:
            engine=make_engine(temp)
            target=engine.root/'addons/script.module.tmdbhelper.sync.trakt/resources/lib/tmdbhelper_sync_trakt/directories/mapper_calendar_base.py'
            target.write_text(target.read_text().replace("    mediatype = 'episode'","    mediatype = 'episode'\n    changed_configuration = True"))
            plans=engine.scan()
            self.assertEqual(len(plans),10)
            self.assertEqual(next(p.status for p in plans if p.id=='calendar'),'incompatible')
            self.assertEqual(next(p.status for p in plans if p.id=='autowidget'),'ready')

    def test_both_older_calendar_locations(self):
        for legacy in (False,True):
            with self.subTest(legacy=legacy),tempfile.TemporaryDirectory() as temp:
                engine=make_engine(temp)
                provider=engine.root/'addons/script.module.tmdbhelper.sync.trakt/resources/lib/tmdbhelper_sync_trakt/directories/mapper_calendar_base.py'
                raw=provider.read_bytes();provider.unlink()
                core=engine.root/'addons/plugin.video.themoviedb.helper/resources/tmdbhelper/lib/items/directories'
                target=core/'trakt/mapper_calendar.py' if legacy else core/'mapper_calendar.py'
                target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(raw)
                caller=target.parent/'lists_calendar.py'
                module='tmdbhelper.lib.items.directories.'+('trakt.mapper_calendar' if legacy else 'mapper_calendar')
                caller.write_text('from '+module+' import FactoryCalendarEpisodeItemMapper\n')
                p=engine.scan(['calendar'])[0]
                self.assertEqual(p.status,'ready',p.reason)
                engine.apply(p)
                self.assertEqual(engine.scan(['calendar'])[0].status,'fixed')

    def test_an_unreferenced_old_mapper_is_not_patched(self):
        with tempfile.TemporaryDirectory() as temp:
            engine=make_engine(temp)
            provider=engine.root/'addons/script.module.tmdbhelper.sync.trakt/resources/lib/tmdbhelper_sync_trakt/directories/mapper_calendar_base.py'
            raw=provider.read_bytes();provider.unlink()
            target=engine.root/'addons/plugin.video.themoviedb.helper/resources/tmdbhelper/lib/items/directories/mapper_calendar.py'
            target.write_bytes(raw)
            p=engine.scan(['calendar'])[0]
            self.assertEqual(p.status,'incompatible')
            self.assertEqual(target.read_bytes(),raw)


class PackageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec=importlib.util.spec_from_file_location('builder',PROJECT/'tools/build.py')
        cls.build=importlib.util.module_from_spec(spec);spec.loader.exec_module(cls.build)
        cls.outputs=cls.build.outputs()

    def test_every_package_has_one_correct_root_and_valid_manifest(self):
        count=0
        for name,raw in self.outputs.items():
            if not name.endswith('.zip'):continue
            count+=1
            with zipfile.ZipFile(io.BytesIO(raw)) as z:
                self.assertIsNone(z.testzip())
                roots={n.split('/')[0] for n in z.namelist()}
                self.assertEqual(len(roots),1)
                root=next(iter(roots))
                manifest=ET.fromstring(z.read(root+'/addon.xml'))
                self.assertEqual(manifest.get('id'),root)
                self.assertEqual(manifest.get('version'),self.build.VERSION)
                for path in z.namelist():
                    self.assertNotIn('..',Path(path).parts)
                    self.assertFalse(path.startswith('/'))
                    self.assertNotIn('__pycache__',path)
                    self.assertNotIn('userdata/',path)
                    self.assertNotIn('fixtures/',path)
                    self.assertNotIn('tests/',path)
                    if path.endswith('.py'):compile(z.read(path),path,'exec')
                if root.startswith('script.'):
                    package=json.loads(z.read(root+'/package.json'))
                    self.assertEqual(package['id'],root)
                    ET.fromstring(z.read(root+'/resources/settings.xml'))
                    self.assertIn(root+'/LICENSE.txt',z.namelist())
        self.assertEqual(count,12)  # combined + ten individual repairs + repository

    def test_repository_index_and_all_download_checksums(self):
        xml=self.outputs['repository/addons.xml']
        self.assertEqual(self.outputs['repository/addons.xml.md5'].decode().strip(),hashlib.md5(xml).hexdigest())
        root=ET.fromstring(xml)
        self.assertEqual(len(root.findall('addon')),11)
        manifest=json.loads(self.outputs['repository/manifest.json'])
        for package in manifest['packages']:
            raw=self.outputs[package['path']]
            digest=hashlib.sha256(raw).hexdigest()
            self.assertEqual(package['sha256'],digest)
            self.assertEqual(self.outputs[package['path']+'.sha256'].decode().strip(),digest)

    def test_build_is_reproducible(self):
        self.assertEqual(self.outputs,self.build.outputs())

    def test_file_manager_page_lists_all_packages_with_kodi_http_rules(self):
        # Kodi Omega xbmc/filesystem/HTTPDirectory.cpp requires the decoded
        # anchor text to match the relative href, rather than a friendly label.
        import html
        from urllib.parse import unquote, urljoin
        page=self.outputs['index.html'].decode('utf-8')
        pattern=r'<a href="([^"]*)"[^>]*>\s*(.*?)\s*</a>(.+?)(?=<a|</tr|$)'
        visible=[]
        for link,name,metadata in re.findall(pattern,page,re.I):
            if html.unescape(name.strip()).rstrip('/')==unquote(html.unescape(link)).rstrip('/'):
                visible.append(link)
        packages={path for path in self.outputs if path.endswith('.zip')}
        self.assertEqual(set(visible),packages)
        self.assertTrue(visible[0].startswith('repository.jamied91.buildfixes-'))
        for path in visible:
            self.assertEqual(urljoin('https://jamied91.github.io/Kodi_Build_Fixes/',path),
                             'https://jamied91.github.io/Kodi_Build_Fixes/'+path)

    def test_runtime_uses_python39_compatible_syntax(self):
        for path in (PROJECT/'addon').rglob('*.py'):
            if '__pycache__' not in path.parts:
                ast.parse(path.read_text(encoding='utf-8'),feature_version=(3,9))

    def test_no_machine_paths_or_private_storage_names_in_install_zips(self):
        forbidden=[b'C:\\Users\\Jamie',b'C:/Users/Jamie',b'TV IP.txt',b'connection.json',b'kodi-tv-patch-backups',b'kodi-reauthorize-20261101']
        for name,raw in self.outputs.items():
            if not name.endswith('.zip'):continue
            with zipfile.ZipFile(io.BytesIO(raw)) as z:
                for path in z.namelist():
                    data=z.read(path)
                    for word in forbidden:self.assertNotIn(word,data,(name,path,word))


if __name__=='__main__':unittest.main()
