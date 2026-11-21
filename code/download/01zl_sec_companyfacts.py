#!/usr/bin/env python3
"""Download SEC companyfacts.zip, once, without the quarterly files of the `sec` source.

WHY. The geography release needs each filing's own annual revenue fact. companyfacts.zip holds every undimensioned
XBRL fact per CIK with its accession number (`accn`), period and form, so one 1.4 GB file answers it. `01_download.py
--source sec` would also fetch the quarterly data sets that were evicted on purpose.

The URL, file name and notes come from the `also` list of the `sec` source in `config/sources.yaml`. The file goes
through core.fetch, so a rerun skips it when it is on disk and in the manifest.

Usage
    python download/01zl_sec_companyfacts.py --dry-run
    python download/01zl_sec_companyfacts.py
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core import config, fetch  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    config.require_contact()
    reg = yaml.safe_load((config.CONFIG_DIR / "sources.yaml").read_text(encoding="utf-8"))
    spec = reg["sources"]["sec"]
    item = next(i for i in spec["also"] if i["as"] == "companyfacts.zip")
    dest = config.RAW / spec["subdir"] / item["as"]
    r = fetch.fetch(item["url"], dest, "sec", dry_run=a.dry_run, expect_min_bytes=500_000_000)
    if not a.dry_run:
        fetch.record(r, "sec")
        print(f"{dest} {dest.stat().st_size / 1e9:.2f} GB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
