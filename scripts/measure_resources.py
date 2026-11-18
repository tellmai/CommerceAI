#!/usr/bin/env python3
"""Sample native API/worker/PostgreSQL PSS during manual browser operations."""
import argparse
from datetime import UTC, datetime
import json
from pathlib import Path
import time

import psutil

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--seconds', type=int, default=30)
    parser.add_argument('--output', type=Path, default=ROOT / 'docs/normal-memory.json')
    args = parser.parse_args()
    if not 1 <= args.seconds <= 300:
        parser.error('seconds must be 1..300')
    pids = json.loads((ROOT / '.local/services.json').read_text())
    pids['postgres'] = int((ROOT / '.local/pgdata/postmaster.pid').read_text().splitlines()[0])
    samples = []
    started = time.monotonic()
    while time.monotonic() - started < args.seconds:
        current = {}
        for name, pid in pids.items():
            parent = psutil.Process(pid)
            processes = [parent, *parent.children(recursive=True)] if name == 'postgres' else [parent]
            current[name] = sum(p.memory_full_info().pss for p in processes if p.is_running()) / 1024**2
        samples.append(current)
        time.sleep(.2)
    result = {
        'measured_at': datetime.now(UTC).isoformat(), 'seconds': args.seconds, 'samples': len(samples),
        'activity': 'Browser navigation and manual workbench operations',
        'metric': 'PSS MiB, native Linux processes; excludes browser, build and OS file cache',
        'peak_mib': {k: round(max(s[k] for s in samples), 2) for k in pids},
        'peak_total_mib': round(max(sum(s.values()) for s in samples), 2),
    }
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
