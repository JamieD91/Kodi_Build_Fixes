"""Build deterministic Kodi installation ZIPs and a public GitHub repository."""
from pathlib import Path
import argparse
import hashlib
import html
import json
import shutil
import sys
import tempfile
import xml.etree.ElementTree as ET
import zipfile

ROOT=Path(__file__).resolve().parents[1]
ADDON=ROOT/'addon'
CATALOG=json.loads((ADDON/'resources/lib/buildfixes/recipes.json').read_text(encoding='utf-8'))
VERSION=CATALOG['version']
BASE_ID='script.jamied91.buildfixes'
REPO_ID='repository.jamied91.buildfixes'
ORDER_ID='script.jamied91.playerorder'
RAW='https://raw.githubusercontent.com/JamieD91/Kodi_Build_Fixes/main/repository/'


def xml_bytes(root):
    ET.indent(root,space='  ')
    return ET.tostring(root,encoding='utf-8',xml_declaration=True)+b'\n'


def manifest(addon_id,name,description,repo=False):
    root=ET.Element('addon',{'id':addon_id,'name':name,'version':VERSION,'provider-name':'JamieD91'})
    if repo:
        extension=ET.SubElement(root,'extension',{'point':'xbmc.addon.repository'})
        directory=ET.SubElement(extension,'dir')
        for tag,value in [('info',RAW+'addons.xml'),('checksum',RAW+'addons.xml.md5'),('datadir',RAW),('hashes','sha256')]:
            ET.SubElement(directory,tag).text=value
    else:
        requires=ET.SubElement(root,'requires');ET.SubElement(requires,'import',{'addon':'xbmc.python','version':'3.0.0'})
        extension=ET.SubElement(root,'extension',{'point':'xbmc.python.script','library':'addon.py'})
        ET.SubElement(extension,'provides').text='executable'
        ET.SubElement(root,'extension',{'point':'xbmc.service','library':'service.py'})
    metadata=ET.SubElement(root,'extension',{'point':'xbmc.addon.metadata'})
    ET.SubElement(metadata,'summary',{'lang':'en_GB'}).text=name
    ET.SubElement(metadata,'description',{'lang':'en_GB'}).text=description
    ET.SubElement(metadata,'platform').text='all'
    ET.SubElement(metadata,'license').text='GPL-3.0-or-later'
    ET.SubElement(metadata,'source').text='https://github.com/JamieD91/Kodi_Build_Fixes'
    ET.SubElement(metadata,'website').text='https://jamied91.github.io/Kodi_Build_Fixes/'
    ET.SubElement(metadata,'news').text='Structure-checked manual repairs, guarded undo and one-shot restart cleanup. No account data is included.'
    return root


def settings():
    # Legacy settings XML remains supported by Kodi 21 and avoids requiring a
    # settings-generation dependency for these two ordinary text/boolean fields.
    root=ET.Element('settings')
    category=ET.SubElement(root,'category',{'label':'Repair preferences'})
    ET.SubElement(category,'setting',{'id':'provider_base','type':'text','label':'Live TV provider HTTPS address','default':'https://thedaddy.pk'})
    return xml_bytes(root)


def zip_bytes(files,top):
    import io
    stream=io.BytesIO()
    with zipfile.ZipFile(stream,'w',zipfile.ZIP_DEFLATED,compresslevel=9) as archive:
        for relative,raw in sorted(files.items()):
            info=zipfile.ZipInfo(top+'/'+relative,(2026,1,1,0,0,0))
            info.compress_type=zipfile.ZIP_DEFLATED
            info.external_attr=0o100644<<16
            archive.writestr(info,raw)
    return stream.getvalue()


def outputs():
    files={}
    for path in ADDON.rglob('*'):
        if path.is_file() and '__pycache__' not in path.parts and path.suffix not in ('.pyc','.pyo'):
            files[path.relative_to(ADDON).as_posix()]=path.read_bytes()
    if (ROOT/'LICENSE.txt').exists():files['LICENSE.txt']=(ROOT/'LICENSE.txt').read_bytes()
    files['CREDITS.txt']=(ROOT/'CREDITS.txt').read_bytes()
    result={}
    addons=ET.Element('addons')
    packages=[(BASE_ID,'Jamie Build Fixes - Toolkit',[],
               'Manually inspect and reapply the supported Kodi build repairs. Includes backups, guarded undo, structure checks and restart cleanup.')]
    for fix in CATALOG['fixes']:
        packages.append((BASE_ID+'.'+fix['id'],'Jamie Fix - '+fix['title'],[fix['id']],fix['description']))
    package_info=[]
    for addon_id,name,selected,description in packages:
        own=dict(files)
        root=manifest(addon_id,name,description)
        own['addon.xml']=xml_bytes(root)
        own['resources/settings.xml']=settings()
        own['package.json']=json.dumps({'id':addon_id,'version':VERSION,'fixes':selected},indent=2).encode()
        data=zip_bytes(own,addon_id)
        relative='repository/'+addon_id+'/'+addon_id+'-'+VERSION+'.zip'
        digest=hashlib.sha256(data).hexdigest()
        result[relative]=data
        result[relative+'.sha256']=(digest+'\n').encode()
        result['repository/'+addon_id+'/addon.xml']=own['addon.xml']
        addons.append(root)
        package_info.append({'id':addon_id,'version':VERSION,'path':relative,'sha256':digest,'bytes':len(data),'fixes':selected})
    order_manifest=manifest(ORDER_ID,'Jamie Player Menu Organiser',
        'Saved ordering and Move up / Move down for TMDb Helper player menus. '
        'Open this program add-on to enable the checked chooser integration, then restart Kodi.')
    order_manifest.remove(order_manifest.find("extension[@point='xbmc.service']"))
    ET.SubElement(order_manifest.find('requires'),'import',{'addon':'plugin.video.themoviedb.helper','version':'6.20.3'})
    order_manifest.find("extension[@point='xbmc.addon.metadata']/news").text='First release: skin-aware player ordering, context menu, preview and reversible integration.'
    order_files={p.relative_to(ROOT/'player_order').as_posix():p.read_bytes()
        for p in (ROOT/'player_order').rglob('*')
        if p.is_file() and '__pycache__' not in p.parts and p.suffix not in ('.pyc','.pyo')}
    order_files.update({'addon.xml':xml_bytes(order_manifest),'LICENSE.txt':files['LICENSE.txt'],
        'CREDITS.txt':files['CREDITS.txt'],'resources/settings.xml':b'<settings/>\n',
        'package.json':json.dumps({'id':ORDER_ID,'version':VERSION,'feature':'player_order'}).encode()})
    order_zip=zip_bytes(order_files,ORDER_ID)
    order_path='repository/'+ORDER_ID+'/'+ORDER_ID+'-'+VERSION+'.zip'
    order_digest=hashlib.sha256(order_zip).hexdigest()
    result[order_path]=order_zip
    result[order_path+'.sha256']=(order_digest+'\n').encode()
    result['repository/'+ORDER_ID+'/addon.xml']=order_files['addon.xml']
    addons.append(order_manifest)
    package_info.append({'id':ORDER_ID,'version':VERSION,'path':order_path,'sha256':order_digest,
        'bytes':len(order_zip),'fixes':[],'feature':'player_order'})
    repo_manifest=manifest(REPO_ID,'Jamie Build Fixes Repository','Repository for Jamie\'s manually operated Kodi repair toolkit.',True)
    repo_zip=zip_bytes({'addon.xml':xml_bytes(repo_manifest),'README.txt':b'Install this ZIP in Kodi, then browse Jamie Build Fixes Repository / Program add-ons.\n'},REPO_ID)
    repo_name=REPO_ID+'-'+VERSION+'.zip'
    result[repo_name]=repo_zip
    result[repo_name+'.sha256']=(hashlib.sha256(repo_zip).hexdigest()+'\n').encode()
    index=xml_bytes(addons)
    result['repository/addons.xml']=index
    result['repository/addons.xml.md5']=(hashlib.md5(index).hexdigest()+'\n').encode()
    result['repository/addons.xml.sha256']=(hashlib.sha256(index).hexdigest()+'\n').encode()
    result['repository/manifest.json']=json.dumps({'schema':1,'version':VERSION,'packages':package_info},indent=2).encode()+b'\n'
    links=[('Install this first: repository ZIP',repo_name),
           ('Combined repair toolkit ZIP',package_info[0]['path'])]
    links.extend((next(f['title'] for f in CATALOG['fixes'] if f['id']==p['fixes'][0]),p['path']) for p in package_info[1:] if p['fixes'])
    links.append(('Player Menu Organiser',order_path))
    page='<!doctype html><html lang="en"><meta charset="utf-8"><title>Jamie Build Fixes</title><h1>Jamie Build Fixes</h1><p>Install the repository ZIP, then choose the toolkit or an individual repair under Program add-ons.</p><table>\n'
    # Kodi's HTTPDirectory parser accepts an entry only when anchor text
    # matches its relative href. Keep descriptions outside the filename link.
    for label,href in links:
        escaped=html.escape(href,quote=True)
        page+='<tr><td><a href="'+escaped+'">'+escaped+'</a></td><td>'+html.escape(label)+'</td></tr>\n'
    page+='</table><p>Repairs run only when you select Apply. Restart Kodi afterward. <a href="https://github.com/JamieD91/Kodi_Build_Fixes">Source and instructions</a>.</p></html>\n'
    result['index.html']=page.encode()
    result['.nojekyll']=b''
    checks=''.join(hashlib.sha256(raw).hexdigest()+'  '+path+'\n' for path,raw in sorted(result.items()) if path.endswith('.zip'))
    result['SHA256SUMS.txt']=checks.encode()
    return result


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--check',action='store_true');args=parser.parse_args()
    generated=outputs()
    if args.check:
        differences=[name for name,raw in generated.items() if not (ROOT/name).is_file() or (ROOT/name).read_bytes()!=raw]
        if differences:
            print('Generated files need rebuilding:',', '.join(differences));raise SystemExit(1)
        print('All generated ZIPs and repository metadata match the source.')
        return
    for name,raw in generated.items():
        target=ROOT/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(raw)
    print('Built',sum(name.endswith('.zip') for name in generated),'installable Kodi ZIPs and repository metadata.')


if __name__=='__main__':main()
