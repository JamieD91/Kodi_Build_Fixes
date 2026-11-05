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
- ReAuthorize token-lifetime metadata, its hidden Trakt sign-in action and
  the extracted TMDb Helper Trakt provider's application-key/token integration.
- ExtendedInfo's known malformed manifest.
- Trakt context-menu actions on supported non-library TMDb Helper items.
- Red Light playback identification: omit title-generated Trakt slugs while
  retaining the supplied TMDb, IMDb and TVDB identifiers.
- Viper Torrentio empty-result handling.
- Older Yahoo weather label mapping. The supported newer upstream mapping is
  recognised and left unchanged.

The Live TV provider address is editable in toolkit Settings. A working address
cannot be guaranteed indefinitely: upstream providers can move or stop serving.

## Player Menu Organiser

Install **Jamie Player Menu Organiser** separately from this repository's Program
add-ons. Open it, choose **Enable / reapply enhanced player menus**, then restart
Kodi. **Put Red Light first** provides a shortcut to that saved group order.

In a TMDb Helper player chooser, hold Select/OK, use the context-menu key or
right-click. **Move / Order...** also opens the same options. Move up, down, to
top or bottom saves immediately. Normal Select still chooses the player.
**Preview and arrange player groups** lets you organise without starting playback.

Player groups and their inner play/search menus keep separate orders. New
entries follow saved entries; temporarily unavailable players retain their
saved place. Reset restores TMDb Helper's original order for that menu.

The organiser uses the active skin's select layout, with a bundled fallback.
It affects TMDb Helper player choosers only. It does not change skin files,
global remote keymaps, player definitions, playback settings or account data.
The small chooser hook is checked structurally, backed up before changes and
can be disabled from the organiser. If a Helper update clears it, run Enable
again; unknown implementations are refused and saved preferences are retained.
Installing the organiser does not enable the hook automatically.

The organiser and Storage Viewer are independent program add-ons; they do not
require Xenon or any of the repair patches. Organiser 0.1.2 supports Windows
Kodi's Python 3.8 and the unchanged player-chooser contract in Helper 6.15.1,
including the Crew build using Xonfluence. Later Helper versions are checked
by code structure. Storage Viewer uses Kodi's current local profile and active
skin and requires no Helper integration. These features do not reorder native
source/resolver dialogs generated internally by other video add-ons.
Storage Viewer 0.1.1 also refreshes Windows directory-entry metadata when
browsing folders, avoiding stale file sizes in Kodi's Python 3.8 runtime.

Organiser 0.1.1 adds **Automatic recovery settings → Automatically reapply
enabled player menus**. It is off by default, retaining manual operation.
With it on, the service checks at startup and watches the chooser file for
updates. An intact hook is not rewritten and creates no extra backups. A
recognised missing hook is backed up and restored after the file settles and
playback stops. Unknown code is left alone and logged. **Disable enhanced
player menus** is remembered and prevents automatic re-enabling. Your saved
menu orders are unchanged. The service never restarts Kodi; an already loaded
chooser may need reopening or the next normal restart. With the toggle off,
the startup service exits immediately without scanning Helper or monitoring.

## Kodi Storage Viewer

Install **Kodi Storage Viewer** from this repository's Program add-ons and open
it to scan. It is independent of TMDb Helper, the repair toolkit and the player
organiser, and uses Kodi's native dialogs to work with different skins.

Explore measured storage by category, add-on, largest files or folder. Add-on
totals separate installed code from saved data/caches across local profiles.
Thumbnails, databases, installer ZIPs, temporary files, logs and profile files
have their own categories. Unmatched data folders are listed for review.

Each add-on shows direct/indirect required dependants (including disabled
add-ons), optional dependencies and discovered active-skin/favourites links.
**No declared dependants** is guidance, not proof that an add-on is unused:
builds can call add-ons dynamically. **Open Kodi add-on manager** takes you to
Kodi's controls for reviewing, disabling or uninstalling an add-on.

The viewer performs no cleanup, patching or disabling. Scans stay in memory;
no report or account data is uploaded. The size scan uses file metadata, while
small manifests and bounded skin/favourites XML are read for references.
Scans can be cancelled; inaccessible files and partial results are reported.
Local overlapping roots are deduplicated and links/junctions are skipped.
Sizes are logical file contents, with hard-link duplicates counted once;
filesystem allocation and Android app-private/APK usage can differ. The largest
500 entries per folder and 100 files overall are shown to keep memory bounded.

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

Toolkit 0.1.3 repairs ReAuthorize's old TMDb Helper key-file assumption after
the Trakt API moved into `script.module.tmdbhelper.sync.trakt`. It configures
the recognised application key only when the provider and ReAuthorize already
hold the same access/refresh token pair. Separate provider sign-ins, custom
keys and changed code are left alone. The repaired ReAuthorize adapter writes
both legacy and provider token stores and clears stale session properties.
Its shared key-file route also directs ReAuthorize's periodic API checker to
the provider when installed, retaining the legacy route on older installations.
No application keys or account tokens are shipped with the repair. Restart
Kodi after applying it so the changed application-key module is reloaded.
The newer Xenon adapter that delegates to `tmdbh_compat.sync_trakt` is
recognised and preserved, including its OAuth secret handling. Its presence
does not prevent restoring an application key or checker route overwritten
by an update. Unknown changes to that sync branch still stop the repair.

Toolkit 0.1.4 adds the Red Light identification repair. A title-generated slug
can refer to another show (for example `cia` instead of `cia-2026`), causing
Trakt to reject a scrobble. The repair changes only that known assignment in
the playback properties; other metadata and playback behavior are preserved.

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
