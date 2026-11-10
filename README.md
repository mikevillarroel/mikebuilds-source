# MikeBuilds Apps - Source

AltStore/SideStore-compatible app source for MikeBuilds apps (BuildBox, TendieMaker, Layers). All IPAs ship unsigned.

**Source URL:** https://mikevillarroel.github.io/mikebuilds-source/source.json

## Catalog upstream sync

`buildbox-catalog.json` (BuildBox's internal catalog) carries the approved
third-party apps. Their entries auto-sync from the developers' official
AltStore sources via `tools/sync-upstreams.py`:

- **Cercube** ← https://altstore.cercube.com/
- **ReGram, Watusi** (Fouad Raheb) ← https://source.fouadraheb.com/

The script only ever updates those mapped apps (matched by bundle ID +
name), only when upstream is strictly newer, and never touches BuildBox,
TendieMaker, or Layers. A daily job runs it with `--apply` and commits
only when something changed. Manual check: `python3 tools/sync-upstreams.py`
(dry-run); add `--apply` to write.
