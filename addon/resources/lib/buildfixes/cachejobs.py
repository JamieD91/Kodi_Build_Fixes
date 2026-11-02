"""One-shot cleanup after a requested restart; never auto-applies source patches."""
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import time
from buildfixes.engine import Busy, Incompatible, safe_path
from buildfixes.special import get_settings


def calendar_cache(engine):
    rel = 'userdata/addon_data/plugin.video.themoviedb.helper/database_07/ItemContainer.db'
    path = safe_path(engine.root, rel)
    removed = 0
    if path.exists():
        con = sqlite3.connect(str(path), timeout=0.25)
        try:
            columns = [r[1] for r in con.execute('PRAGMA table_info(simplecache)')]
            if columns != ['id', 'expires', 'data', 'checksum']:
                raise Incompatible('Calendar cache schema changed; source repair remains installed')
            con.execute('BEGIN IMMEDIATE')
            # Only derived calendar lists, never watch history or sync records.
            result = con.execute("DELETE FROM simplecache WHERE id GLOB ? OR id GLOB ?", ('ListTraktCalendar*', 'ListCalendar_*'))
            removed = result.rowcount
            con.commit()
        except Exception:
            con.rollback(); raise
        finally:
            con.close()
    listings = 0
    for rel in ('temp/archive_cache', 'cache/archive_cache'):
        base = safe_path(engine.root, rel)
        if not base.is_dir():
            continue
        for path in base.glob('*.fi'):
            checked = safe_path(engine.root, path.relative_to(engine.root).as_posix())
            if checked.is_symlink() or not checked.is_file():
                continue
            raw = checked.read_bytes()
            if b'plugin.video.themoviedb.helper' in raw and (b'info=trakt_calendar' in raw or b'info=dir_calendar_trakt' in raw):
                checked.unlink(); listings += 1
    return {'calendar_rows': removed, 'calendar_listings': listings}


def guide_retry(engine):
    rel = 'userdata/addon_data/plugin.video.playlistbrowser/dlhd/refresh-state.json'
    lock = safe_path(engine.root, 'userdata/addon_data/plugin.video.playlistbrowser/dlhd/export.lock')
    if lock.exists():
        raise Busy('Channel export is running')
    path = safe_path(engine.root, rel)
    if path.exists():
        state = json.loads(path.read_text())
        state['retry_after'] = 0
        engine.atomic_write(rel, json.dumps(state, indent=2).encode())
    return {'guide_retry_reset': True}


def trakt_metadata(engine):
    master = get_settings(engine, 'script.module.reauthorize')
    original = engine.backend.get_setting('script.trakt', 'authorization')
    if not original or not master.get('trakt.token'):
        return {'trakt_metadata': 'No signed-in account needs correction'}
    auth = json.loads(original)
    expiry = int(float(master.get('trakt.expires') or '0'))
    lifetime = int(float(master.get('trakt.lifetime') or '604800'))
    lifetime = lifetime if lifetime > 0 else 604800
    if auth.get('access_token') != master.get('trakt.token') or auth.get('refresh_token') != master.get('trakt.refresh'):
        return {'trakt_metadata': 'Different token copies; no account data changed'}
    if auth.get('created_at') == expiry - lifetime and auth.get('expires_in') == lifetime:
        return {'trakt_metadata': 'Already correct'}
    # Let ReAuthorize complete a due renewal with its repaired adapter first.
    if expiry - time.time() <= lifetime / 2:
        raise Busy('Trakt renewal is due; leaving metadata untouched for now')
    again = get_settings(engine, 'script.module.reauthorize')
    if again != master or engine.backend.get_setting('script.trakt', 'authorization') != original:
        raise Busy('Sign-in changed during inspection')
    record = {'before_created_at': auth.get('created_at'), 'before_expires_in': auth.get('expires_in'),
              'after_created_at': expiry - lifetime, 'after_expires_in': lifetime,
              'token_fingerprint': hashlib.sha256((auth['access_token'] + '\0' + auth['refresh_token']).encode()).hexdigest()}
    auth.update(created_at=expiry - lifetime, expires_in=lifetime)
    # Do not persist token strings in the toolkit's metadata audit.
    engine.write_json(engine.profile / 'trakt-metadata-audit.json', record)
    engine.backend.set_setting('script.trakt', 'authorization', json.dumps(auth))
    actual = json.loads(engine.backend.get_setting('script.trakt', 'authorization'))
    if actual != auth:
        raise Busy('Trakt settings changed during correction; scan again')
    return {'trakt_metadata': 'Expiry metadata corrected; token values preserved'}


def process_pending(engine):
    if not (engine.profile / 'pending.json').exists():
        return {}
    with engine.lock():
        return _process_pending(engine)


def _process_pending(engine):
    path = engine.profile / 'pending.json'
    if not path.exists():
        return {}
    pending = json.loads(path.read_text())
    if pending.get('origin_pid') == os.getpid():
        return {'restart_required': True}
    handlers = {'calendar_cache': calendar_cache, 'guide_retry': guide_retry, 'trakt_metadata': trakt_metadata}
    results, retry = {}, []
    for job in pending.get('jobs', []):
        if job not in handlers:
            results[job] = 'Unknown cleanup task; no changes made'
            continue
        try:
            results[job] = handlers[job](engine)
        except (Busy, sqlite3.OperationalError, PermissionError):
            retry.append(job)
            results[job] = 'Busy; cleanup remains pending'
        except (Incompatible, ValueError, OSError) as error:
            retry.append(job)
            results[job] = type(error).__name__ + '; cleanup remains pending'
    if retry:
        pending['jobs'] = retry
        engine.write_json(path, pending)
    else:
        path.unlink()
    engine.write_json(engine.profile / 'last-cleanup.json', results)
    return results
