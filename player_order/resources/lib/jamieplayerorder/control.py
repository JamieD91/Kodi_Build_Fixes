"""Remember explicit enable/disable intent and serialise automatic/manual changes."""
from contextlib import contextmanager
import errno
import json
from pathlib import Path
from jamieplayerorder.integration import apply, inspect, RELATIVE
from jamieplayerorder.order import atomic_write


class Busy(RuntimeError):
    pass


@contextmanager
def locked(profile, name='integration.lock'):
    profile = Path(profile)
    profile.mkdir(parents=True, exist_ok=True)
    with (profile/name).open('a+b') as stream:
        stream.seek(0, 2)
        if not stream.tell():
            stream.write(b'\0')
            stream.flush()
        stream.seek(0)
        try:
            try:
                import fcntl
            except ImportError:
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            if error.errno in (errno.EACCES, errno.EAGAIN, errno.EWOULDBLOCK, errno.EDEADLK):
                raise Busy('Another organiser operation is running; try again shortly.') from error
            raise
        yield  # Closing releases the OS lock, including after Kodi exits/crashes.


def write_intent(profile, enabled):
    atomic_write(Path(profile)/'integration.json',
                 (json.dumps({'schema': 1, 'enabled': bool(enabled)})+'\n').encode())


def read_intent(profile, helper):
    """Migrate old installations conservatively; a previous Disable stays off."""
    profile = Path(profile)
    try:
        data = json.loads((profile/'integration.json').read_text(encoding='utf-8'))
    except FileNotFoundError:
        enabled = inspect((Path(helper)/RELATIVE).read_text(encoding='utf-8-sig')) == 'enabled'
        manifests = sorted((profile/'backups').glob('*/manifest.json'), reverse=True)
        if manifests:
            operation = json.loads(manifests[0].read_text()).get('operation')
            if operation in ('enable', 'disable'):
                enabled = operation == 'enable'
        write_intent(profile, enabled)
        return enabled
    if not isinstance(data, dict) or data.get('schema') != 1 or type(data.get('enabled')) is not bool:
        raise ValueError('Invalid organiser enable/disable preference; it was left unchanged.')
    return data['enabled']


def set_integration(helper, profile, enable):
    with locked(profile):
        target = Path(helper)/RELATIVE
        source_enabled = inspect(target.read_text(encoding='utf-8-sig')) == 'enabled'
        changed = apply(helper, profile, enable=enable)
        try:
            write_intent(profile, enable)
        except OSError:
            if changed:
                apply(helper, profile, enable=source_enabled)
            raise
        return changed
