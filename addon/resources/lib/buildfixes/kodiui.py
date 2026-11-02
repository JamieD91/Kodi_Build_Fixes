"""Small native Kodi dialogs; no background source patching or telemetry."""
import json
import os
from pathlib import Path
import time
import traceback
import xbmc
import xbmcaddon
import xbmcgui
import xbmcvfs
from buildfixes import VERSION
from buildfixes.backend import KodiBackend
from buildfixes.cachejobs import process_pending
from buildfixes.engine import Engine, Busy, Incompatible


LABELS = {'ready':'Needs repair', 'fixed':'Already fixed / not needed',
          'missing':'Not installed', 'incompatible':'Changed code - skipped'}


def context():
    own = xbmcaddon.Addon()
    addon_path = Path(xbmcvfs.translatePath(own.getAddonInfo('path')))
    root = Path(xbmcvfs.translatePath('special://home/'))
    profile = Path(xbmcvfs.translatePath(own.getAddonInfo('profile')))
    catalog = json.loads((addon_path/'resources/lib/buildfixes/recipes.json').read_text(encoding='utf-8'))
    package = json.loads((addon_path/'package.json').read_text(encoding='utf-8'))
    return own, Engine(root, profile, KodiBackend(root, own), catalog), package


def safe_error(error):
    # Messages describe checks, not raw token-bearing HTTP/settings exceptions.
    if isinstance(error,(Busy,Incompatible)):
        return str(error)
    if isinstance(error,PermissionError):
        return 'Kodi could not write an affected file. No newer add-on files were deliberately overwritten.'
    return type(error).__name__ + '. Repair details are in the local backup manifest.'


def show_scan(engine, selected, title):
    plans=engine.scan(selected)
    lines=[]
    for p in plans:
        versions=', '.join(k+' '+v for k,v in p.versions.items())
        lines.extend([p.title, LABELS[p.status], p.reason, versions, ''])
    if (engine.profile/'pending.json').exists():
        lines.extend(['Restart Kodi to finish the queued cache/metadata cleanup.',''])
    xbmcgui.Dialog().textviewer(title,'\n'.join(lines))


def apply_menu(engine, selected, title, all_ready=False):
    plans=engine.scan(selected)
    ready=[p for p in plans if p.status=='ready']
    if not ready:
        show_scan(engine,selected,title)
        return
    if all_ready:
        choices=list(range(len(ready)))
    else:
        choices=xbmcgui.Dialog().multiselect('Choose repairs',[p.title for p in ready])
        if not choices:
            return
    chosen=[ready[i] for i in choices]
    if not xbmcgui.Dialog().yesno(title,'Apply these repairs?\n\n'+'\n'.join(p.title for p in chosen)+'\n\nSmall rollback copies will be kept. Restart Kodi afterward.'):
        return
    results=[]
    for p in chosen:
        try:
            engine.apply(p)
            results.append(p.title+': applied and verified')
        except Exception as error:
            results.append(p.title+': '+safe_error(error))
    results.append('\nRestart Kodi before testing. Reopen any calendar/list that was already displayed.')
    xbmcgui.Dialog().textviewer(title,'\n'.join(results))


def undo_menu(engine,title):
    backups=[]
    for path in engine.backups():
        try:
            record=json.loads(path.read_text())
            if record.get('status') in ('installed','prepared'):
                backups.append((path,record))
        except (ValueError,OSError):
            continue
    if not backups:
        xbmcgui.Dialog().ok(title,'There are no active rollback copies in this add-on profile.')
        return
    choice=xbmcgui.Dialog().select('Undo or recover a repair',[r['created']+' - '+r['title'] for _,r in backups])
    if choice<0:return
    path,record=backups[choice]
    if not xbmcgui.Dialog().yesno(title,'Undo '+record['title']+'?\n\nUndo stops if a later add-on update changed the files. Account tokens and watch history are never restored from an old backup.'):
        return
    try:
        engine.undo(path)
        xbmcgui.Dialog().ok(title,'Original files/settings restored. Restart Kodi before using the affected add-on.')
    except Exception as error:
        xbmcgui.Dialog().ok(title,safe_error(error))


def main():
    own,engine,package=context()
    title=own.getAddonInfo('name')
    selected=package.get('fixes') or None
    while True:
        labels=['Scan repairs','Apply selected compatible repairs','Apply all compatible repairs',
                'Undo / recover a repair','Check repository for updates','Settings','Help / how it works']
        choice=xbmcgui.Dialog().select(title,labels)
        if choice<0:return
        try:
            if choice==0:show_scan(engine,selected,title)
            elif choice==1:apply_menu(engine,selected,title)
            elif choice==2:apply_menu(engine,selected,title,True)
            elif choice==3:undo_menu(engine,title)
            elif choice==4:
                xbmc.executebuiltin('UpdateAddonRepos')
                xbmcgui.Dialog().ok(title,'Kodi is checking installed repositories. Install the Jamie Build Fixes repository ZIP first if you have not done so. Toolkit updates do not automatically apply repairs to other add-ons.')
            elif choice==5:own.openSettings()
            else:
                xbmcgui.Dialog().textviewer(title,
                    '1. Stop playback, then Scan repairs.\n'
                    '2. Apply a selected repair, or all compatible repairs.\n'
                    '3. Restart Kodi, then reopen the affected menu.\n\n'
                    'The toolkit checks code structure, not an upper version number. '
                    'Changed implementations are skipped. Existing fixes and supported upstream repairs are recognised.\n\n'
                    'A short startup task finishes only cache/metadata cleanup you already requested. '
                    'It never patches source code automatically and sends no telemetry.\n\n'
                    'Undo restores only files/settings that still match the applied repair. It does not revert '
                    'newer add-on updates, account tokens or watch history. Backups stay in this add-on\'s userdata folder.\n\n'
                    'Source: https://github.com/JamieD91/Kodi_Build_Fixes\nVersion: '+VERSION)
        except Exception as error:
            xbmcgui.Dialog().ok(title,safe_error(error))


def finish_startup():
    own,engine,package=context()
    pending=engine.profile/'pending.json'
    if not pending.exists():return
    try:
        data=json.loads(pending.read_text())
        if data.get('origin_pid')==os.getpid():return
    except (ValueError,OSError):return
    monitor=xbmc.Monitor()
    # Short retries handle a cache transaction or channel export during startup.
    for attempt in range(8):
        if monitor.waitForAbort(3):return
        try:
            result=process_pending(engine)
            xbmc.log('[Jamie Build Fixes] Requested startup cleanup checked; '+
                     ('some tasks remain pending' if pending.exists() else 'finished'),xbmc.LOGINFO)
            if not pending.exists():return
        except Exception as error:
            xbmc.log('[Jamie Build Fixes] Cleanup deferred: '+type(error).__name__,xbmc.LOGWARNING)
