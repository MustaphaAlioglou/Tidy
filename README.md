# Tidy

Point it at a messy folder and it shows you a plan to tidy it. You approve the
plan, it runs, and you can undo it.

Two native frontends share one core: **GTK4 + libadwaita** on GNOME (and most
other desktops), **Qt 6** on KDE Plasma. `tidy` picks the right one from
`XDG_CURRENT_DESKTOP`; force one with `tidy --gtk`, `tidy --qt` or `TIDY_UI=qt`.

## What v0.1 finds

| Group       | What                                                                    | Action                    |
|-------------|-------------------------------------------------------------------------|---------------------------|
| Clutter     | `.part`/`.crdownload` older than a day, empty folders, archives already extracted next to their folder | holding area |
| Duplicates  | byte-identical copies; only top-level copies are removed, a copy filed in a subfolder is kept | holding area |
| Installers  | `.deb` `.rpm` `.pkg.tar.zst` already installed, or any installer older than 30 days | holding area |
| Old files   | top-level files and folders not used in 6 months                        | moved to `Archive/`       |

## Safety

- **Preview first.** Scanning never changes anything.
- **Nothing is deleted outright.** Removals go to a holding area
  (`~/.local/share/tidy/holding`, or `.tidy-holding-<uid>` on the same drive)
  and expire after 30 days.
- **Every move is logged before it happens** (SQLite, `~/.local/share/tidy/tidy.db`),
  so an interrupted run is reconciled on the next start. Undo a whole run or
  single files; if the original name is taken, the file comes back as `name (2)`.
- **No overwrites.** Moves use `renameat2(RENAME_NOREPLACE)`.
- **Files changed since the scan are skipped**, as are files modified in the
  last 10 minutes (they may still be downloading).
- **Protected:** system folders, the home folder itself, hidden files and
  folders, git/hg/svn repositories, project folders (`package.json`,
  `Cargo.toml`, `pyproject.toml`, …), other drives, symlinks.
- Scanning reads files with `O_NOATIME`, so it never makes old files look recent.
- Local only: no network, no account, no telemetry.

## Use

```sh
./install.sh            # symlinks into ~/.local/bin, adds a desktop entry
tidy                    # GUI
tidy ~/Downloads        # GUI, scanning straight away
tidy-cli scan ~/Downloads [--apply]
tidy-cli history
tidy-cli undo RUN [--move ID]
```

Runs on the system Python (`/usr/bin/python3`) with its PyGObject/PySide6. The
core is stdlib-only.

## Tests

```sh
/usr/bin/python3 -m unittest discover -s tests
```

## Roadmap

- v0.2: sort by file type, before/after view, drag files between groups, "what's big" view
- v0.3: content-aware groups (receipts, screenshots, documents), learning from your choices
- v0.4: watch mode with gentle notifications, plain-sentence rules
