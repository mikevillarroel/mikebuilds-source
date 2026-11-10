#!/usr/bin/env python3
"""Auto-sync BuildBox's internal catalog (buildbox-catalog.json) from the
official upstream AltStore sources of its third-party apps.

Why: the catalog used to be updated by hand, so entries went stale
(e.g. Cercube sat at 6.4.2 / YouTube base 21.40.5 while Majd's source
already served 6.5.0 / 21.44.4). This script polls each upstream source,
and when the upstream's latest version of a mapped app is newer than the
catalog's, it prepends a new entry to that app's versions[] array in the
catalog's exact existing format (fields: version, date, downloadURL,
size, minOSVersion/maxOSVersion when upstream provides them,
localizedDescription).

Catalog version localizedDescription style (mirrors the hand-written
entries, e.g. "Cercube 6.4.2 — improves the player layout..."):
when the upstream version carries a marketingVersion, the description is
"{App Name} {marketingVersion} — {summary}", where {summary} is the first
meaningful line of the upstream release notes (leading bullet and
"This release/This update" stripped, first letter lowercased, trailing
"." ensured). Without a marketingVersion, the summary alone is used.

SAFETY RULES (do not weaken):
- Only the apps in MAPPINGS below are ever touched, matched by
  bundleIdentifier AND case-insensitive exact app name (Fouad's source
  carries several variants sharing a bundle ID — "Regram" vs
  "Regram (No Plugins)" — so name matching matters).
- Miguel's own apps (OWN_BUNDLES) are never modified, and the script
  never adds or removes apps from the catalog.
- An update is applied only when the upstream latest version compares
  strictly NEWER than the catalog's versions[0].version. If upstream is
  older, the script warns and skips.
- The catalog file is rewritten in its exact current serialization
  (json.dumps indent=1, ensure_ascii=False, trailing newline) so diffs
  stay minimal, and written atomically (temp file + rename).

Usage:
    python3 tools/sync-upstreams.py            # dry-run (default): report only
    python3 tools/sync-upstreams.py --apply    # write changes to the catalog

Exit code: 0 on success (including "nothing to do"), 1 on any
fetch/parse error. Output lines are prefixed with the app name; lines
starting with "UPDATE" mark an (available or applied) version bump —
the daily cron greps for those to decide whether to commit and report.
"""

import json
import os
import re
import sys
import urllib.request

REPO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CATALOG_PATH = os.path.join(REPO_DIR, "buildbox-catalog.json")

# Miguel's own apps — NEVER touched by this script.
OWN_BUNDLES = {
    "com.mikevillarroel.buildbox",
    "com.mikevillarroel.tendiemaker",
    "com.mikevillarroel.layers",
}

# Catalog app -> upstream source. "name" is the upstream app's name
# (matched case-insensitively, exactly); bundle must also match.
MAPPINGS = [
    {
        "catalog_name": "Cercube",
        "bundle": "com.google.ios.youtube",
        "upstream_url": "https://altstore.cercube.com/",
        "upstream_name": "Cercube",
    },
    {
        "catalog_name": "ReGram",
        "bundle": "com.burbn.instagram",
        "upstream_url": "https://source.fouadraheb.com/",
        "upstream_name": "Regram",
    },
    {
        "catalog_name": "Watusi",
        "bundle": "net.whatsapp.WhatsApp",
        "upstream_url": "https://source.fouadraheb.com/",
        "upstream_name": "Watusi",
    },
]


def fetch_json(url):
    req = urllib.request.Request(
        url, headers={"User-Agent": "BuildBox-Catalog-Sync/1.0"}
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8"))


def version_key(v):
    """Dotted-version comparison key: numeric segments compare as ints."""
    parts = []
    for seg in str(v).split("."):
        parts.append((0, int(seg), "") if seg.isdigit() else (1, 0, seg))
    return parts


def is_newer(candidate, current):
    a, b = version_key(candidate), version_key(current)
    n = max(len(a), len(b))
    a += [(0, 0, "")] * (n - len(a))
    b += [(0, 0, "")] * (n - len(b))
    return a > b


def iso_date(ts):
    """'2026-11-06T14:17:02Z' / '2026-11-09T18:45:26+00:00' -> '2026-11-06'."""
    m = re.match(r"(\d{4}-\d{2}-\d{2})", str(ts or ""))
    return m.group(1) if m else str(ts or "")


def summarize(app_name, upstream_version):
    """Build the catalog-style one-line localizedDescription (see docstring)."""
    notes = (upstream_version.get("localizedDescription") or "").strip()
    summary = ""
    for line in notes.splitlines():
        line = line.strip()
        if not line:
            continue
        if re.match(r"(?i)^this update contains the following changes:?$", line):
            continue
        line = re.sub(r"^[-•*]\s*", "", line)
        line = re.sub(r"(?i)^this (release|update)\s+", "", line)
        line = re.sub(r"\s*[✨🎉🚀]+$", "", line).strip()
        if line:
            summary = line
            break
    marketing = upstream_version.get("marketingVersion")
    version = upstream_version.get("version")
    if marketing and marketing != version:
        if summary:
            summary = summary[0].lower() + summary[1:]
            if not summary.endswith((".", "!", "?")):
                summary += "."
            return f"{app_name} {marketing} — {summary}"
        return f"{app_name} {marketing}"
    return summary


def build_version_entry(app_name, upstream_version):
    """New catalog versions[] entry, in the catalog's exact field order."""
    entry = {}
    entry["version"] = upstream_version["version"]
    entry["date"] = iso_date(upstream_version.get("date"))
    entry["downloadURL"] = upstream_version["downloadURL"]
    if upstream_version.get("size") is not None:
        entry["size"] = upstream_version["size"]
    if upstream_version.get("minOSVersion"):
        entry["minOSVersion"] = upstream_version["minOSVersion"]
    if upstream_version.get("maxOSVersion"):
        entry["maxOSVersion"] = upstream_version["maxOSVersion"]
    desc = summarize(app_name, upstream_version)
    if desc:
        entry["localizedDescription"] = desc
    return entry


def find_app(apps, bundle, name):
    for app in apps:
        if app.get("bundleIdentifier") == bundle and (
            app.get("name") or ""
        ).lower() == name.lower():
            return app
    return None


def main():
    apply = "--apply" in sys.argv[1:]
    try:
        with open(CATALOG_PATH, encoding="utf-8") as f:
            catalog = json.load(f)
    except Exception as e:
        print(f"ERROR: cannot read catalog: {e}")
        return 1

    upstream_cache = {}
    changed = False

    for mapping in MAPPINGS:
        label = mapping["catalog_name"]
        if mapping["bundle"] in OWN_BUNDLES:
            print(f"{label}: SKIPPED (own app, protected)")
            continue
        cat_app = find_app(catalog.get("apps", []), mapping["bundle"], label)
        if cat_app is None:
            print(f"{label}: WARNING - not found in catalog, skipped")
            continue
        url = mapping["upstream_url"]
        if url not in upstream_cache:
            try:
                upstream_cache[url] = fetch_json(url)
            except Exception as e:
                print(f"ERROR: cannot fetch upstream {url}: {e}")
                return 1
        up_app = find_app(
            upstream_cache[url].get("apps", []),
            mapping["bundle"],
            mapping["upstream_name"],
        )
        if up_app is None or not up_app.get("versions"):
            print(f"{label}: WARNING - not found in upstream {url}, skipped")
            continue

        up_latest = up_app["versions"][0]
        cat_versions = cat_app.get("versions") or []
        cat_current = cat_versions[0]["version"] if cat_versions else "(none)"
        up_version = up_latest["version"]
        marketing = up_latest.get("marketingVersion")
        mlabel = f" ({label} {marketing})" if marketing and marketing != up_version else ""

        if cat_versions and not is_newer(up_version, cat_current):
            if up_version != cat_current:
                print(
                    f"{label}: WARNING - upstream {up_version} is OLDER than "
                    f"catalog {cat_current}, skipped"
                )
            else:
                print(f"{label}: up to date ({cat_current}{mlabel})")
            continue

        entry = build_version_entry(cat_app["name"], up_latest)
        verb = "updated" if apply else "UPDATE AVAILABLE"
        print(
            f"UPDATE {label}: {cat_current} -> {up_version}{mlabel} "
            f"({entry['date']}) [{verb}]"
        )
        if apply:
            cat_app.setdefault("versions", []).insert(0, entry)
            changed = True

    if apply and changed:
        out = json.dumps(catalog, indent=1, ensure_ascii=False) + "\n"
        tmp = CATALOG_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(out)
        os.replace(tmp, CATALOG_PATH)
        # Re-validate what we wrote.
        with open(CATALOG_PATH, encoding="utf-8") as f:
            json.load(f)
        print("Catalog written and re-validated as JSON.")
    elif apply:
        print("No changes to write.")
    else:
        print("Dry-run only — nothing written. Re-run with --apply to write.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
