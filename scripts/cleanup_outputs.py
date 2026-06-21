#!/usr/bin/env python3
"""Blindspot output inventory tool.

IMPORTANT: By default this does NOT delete anything. Blindspot keeps all files for
record/legal/audit reasons. Use this script to see disk usage and old files only.
"""
from __future__ import annotations
import argparse
from pathlib import Path


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument('--output', default='/home/azureuser/Blindspot/output_protected')
    p.add_argument('--limit', type=int, default=250)
    args = p.parse_args()
    out = Path(args.output)
    files = sorted((x for x in out.rglob('*') if x.is_file()), key=lambda x: x.stat().st_mtime, reverse=True)
    total = sum(x.stat().st_size for x in files)
    print(f'files={len(files)} bytes={total} gb={total/(1024**3):.3f}')
    print('Keeping all files. No deletion is performed.')
    print(f'Newest {args.limit} files:')
    for f in files[:args.limit]:
        print(f'{f.stat().st_size}\t{f}')
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
