# Jamie Build Fixes

A manually operated Kodi repair toolkit for the fixes tested on Kodi 21.3,
Xenon 5.1, Windows and Android TV. It checks the affected code structure rather
than imposing an upper add-on version number. Unknown code is skipped.

## Install

1. Download **repository.jamied91.buildfixes-0.1.0.zip** from this repository.
2. In Kodi, choose **Add-ons → Install from ZIP file**, then select that ZIP.
3. Choose **Install from repository → Jamie Build Fixes Repository → Program
   add-ons → Jamie Build Fixes - Toolkit**.
4. Run the toolkit, choose **Scan repairs**, then apply the repairs you want.
5. **Restart Kodi**, reopen the affected menus and test playback.

You can install an individual repair instead of the combined toolkit. The
individual ZIPs are in `repository/script.jamied91.buildfixes.<repair>/` and
also appear separately under Program add-ons in the repository.

Installing or updating the toolkit does **not** automatically patch other
add-ons. You must choose Apply. Its short startup service only finishes
cache/metadata cleanup that you requested before restarting Kodi.

## Optional File Manager source

Enable GitHub Pages under **Settings → Pages → Deploy from a branch → main →
/(root) → Save**. Once GitHub finishes publishing it, this Kodi File Manager
source lists the install ZIPs:

**https://jamied91.github.io/Kodi_Build_Fixes/**

Pages is optional. The installed Kodi repository downloads its index and ZIPs
directly from the public GitHub repository's `main` branch.

## Included repairs

- Trakt calendar episode playback, including the extracted Trakt Sync module.
- TMDb Helper callbacks arriving after playback stopped.
- AutoWidget playback timing, seek accounting and empty refresh paths.
- Umbrella progress-episode season/ID mapping.
- Live TV provider setting and first-run retry backoff.
- ReAuthorize token-lifetime metadata and its hidden Trakt sign-in action.
- ExtendedInfo's known malformed manifest.
- Trakt context-menu actions on supported non-library TMDb Helper items.
- Viper Torrentio empty-result handling.
- Older Yahoo weather label mapping. The supported newer upstream mapping is
  recognised and left unchanged.

The Live TV provider address is editable in toolkit Settings. A working address
cannot be guaranteed indefinitely: upstream providers can move or stop serving.

## Compatibility and recovery

The scan reports **Needs repair**, **Already fixed / not needed**, **Not
installed**, or **Changed code - skipped**. Version numbers are reported for
reference, not used as an upper compatibility limit. Comments, whitespace and
quote formatting can change without invalidating a repair; structural changes
to the affected implementation may require a recipe update.

Repairs change only recognised code. Original files and setting changes are
backed up under this toolkit's Kodi userdata folder. **Undo / recover a repair**
checks the current files before restoring anything and refuses to overwrite a
later add-on update. Stop playback before Apply or Undo.

Calendar cleanup deletes derived calendar listings, never watch history or sync
records. Trakt cleanup preserves access/refresh tokens and corrects metadata
only when the token copies match. If renewal is due or the copies differ, it
waits or leaves account data alone. Undo does not restore old credentials or
old token expiry calculations. ReAuthorize remains responsible for renewal.

Future add-on or skin updates may clear a repair. Run Scan and Apply again.
Unknown implementations are intentionally not guessed at. These repairs do not
promise to fix unrelated playback, provider, account or graphics problems.

## Build and test

Python 3.9+ and the standard library are sufficient:

```text
python -m unittest discover -s tests -v
python tools/build.py
python tools/build.py --check
```

`tools/scan_profile.py <Kodi-home-folder>` performs a read-only compatibility
scan outside Kodi. It never applies a repair or changes the scanned profile.

`addon/` contains the common runtime; `recipes.json` contains reviewed structural
repair definitions. `tools/build.py` reproducibly generates the combined ZIP,
individual ZIPs, repository installer, checksums, index and File Manager page.

Only source, synthetic/public test fixtures and generated install packages
belong in GitHub. **Do not upload Kodi userdata, local backups or logs.**
