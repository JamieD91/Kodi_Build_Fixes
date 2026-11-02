"""Kodi adapter and an offline XML adapter used only by tests/dry-run tooling."""
import json
from pathlib import Path
import xml.etree.ElementTree as ET
from buildfixes.engine import safe_path


class XmlBackend:
    def __init__(self, root, provider='https://thedaddy.pk'):
        self.root, self.provider = Path(root), provider
        self.playing = False

    def get_setting(self, addon, key):
        path = safe_path(self.root, 'userdata/addon_data/' + addon + '/settings.xml')
        if not path.exists():
            return ''
        for node in ET.parse(path).getroot().iter('setting'):
            if node.get('id') == key:
                return node.text or node.get('value') or ''
        return ''

    def set_setting(self, addon, key, value):
        path = safe_path(self.root, 'userdata/addon_data/' + addon + '/settings.xml')
        path.parent.mkdir(parents=True, exist_ok=True)
        root = ET.parse(path).getroot() if path.exists() else ET.Element('settings', {'version': '2'})
        node = next((n for n in root.iter('setting') if n.get('id') == key), None)
        if node is None:
            node = ET.SubElement(root, 'setting', {'id': key})
        node.attrib.pop('default', None); node.attrib.pop('value', None)
        node.text = value
        path.write_bytes(ET.tostring(root, encoding='utf-8', xml_declaration=True))

    def provider_base(self): return self.provider
    def is_playing(self): return self.playing


class KodiBackend:
    def __init__(self, root, own_addon):
        self.root, self.own = Path(root), own_addon

    def get_setting(self, addon, key):
        import xbmcaddon
        try:
            # Fresh handle: embedded ReAuthorize explicitly warns against stale
            # settings handles reverting another component's recently saved data.
            return xbmcaddon.Addon(addon).getSetting(key)
        except RuntimeError:
            # Embedded ReAuthorize has userdata but no registered addon object.
            return XmlBackend(self.root).get_setting(addon, key)

    def set_setting(self, addon, key, value):
        import xbmcaddon
        xbmcaddon.Addon(addon).setSetting(key, value)

    def provider_base(self):
        import xbmcaddon
        return xbmcaddon.Addon(self.own.getAddonInfo('id')).getSetting('provider_base') or 'https://thedaddy.pk'

    def is_playing(self):
        import xbmc
        return xbmc.Player().isPlaying()
