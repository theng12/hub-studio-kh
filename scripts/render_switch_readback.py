#!/usr/bin/env python3
"""Read switch publication after separately verified writes; never retry a mutation."""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import time

from hub_rollout import Gateway


def verify_publication(gateway, expected: list[dict], write_proof: dict,
                       destination: Path, *, timeout_s: float, now=time.monotonic) -> int:
    if write_proof.get('store_verified') is not True or write_proof.get('audits_verified') is not True:
        raise ValueError('switch store and audit writes must be verified separately first')
    if not math.isfinite(timeout_s) or timeout_s <= 0:
        raise ValueError('publication readback requires a positive client deadline')
    targets = {}
    for row in expected:
        if (set(row) != {'controller_id', 'machine_id', 'opted_in'}
                or type(row['opted_in']) is not bool
                or any(not isinstance(row[k], str) or not row[k]
                       for k in ('controller_id', 'machine_id'))):
            raise ValueError('expected switches require exact identities and boolean states')
        key = (row['controller_id'], row['machine_id'])
        if key in targets:
            raise ValueError('duplicate expected switch identity')
        targets[key] = row
    if not targets:
        raise ValueError('no expected switches supplied')
    if Path(destination).exists():
        raise FileExistsError(destination)
    deadline = now() + timeout_s
    record = {'switch_writes_verified': True, 'audits_verified': True,
              'publication_verified': False, 'state': 'switch writes verified; publication readback pending',
              'client_deadline_seconds': timeout_s, 'force_snapshot': None,
              'workers': [], 'missing': [], 'mismatched': [], 'error': None}
    try:
        status, snapshot = gateway.call('GET', '/admin/api/fleet?force=true',
                                        timeout=max(0.001, deadline - now()))
        if status != 200 or not isinstance(snapshot, dict):
            raise RuntimeError(f'force snapshot HTTP {status}')
        controllers = {key[0] for key in targets}
        record['force_snapshot'] = {
            'generated_at': snapshot.get('generated_at'),
            'locations': [{k: row.get(k) for k in ('id', 'reachable')}
                          for row in snapshot.get('locations', []) if row.get('id') in controllers],
            'machines': [{k: row.get(k) for k in ('location_id', 'machine', 'reachable', 'operations')}
                         for row in snapshot.get('machines', [])
                         if (row.get('location_id'), row.get('machine')) in targets],
        }
        remaining = deadline - now()
        if remaining <= 0:
            raise TimeoutError('client deadline exhausted after force snapshot')
        status, listing = gateway.call('GET', '/admin/api/fleet/remote-rendering', timeout=remaining)
        if now() > deadline:
            raise TimeoutError('client deadline exhausted during switch listing')
        if status != 200 or not isinstance(listing, dict) or not isinstance(listing.get('workers'), list):
            raise RuntimeError(f'switch listing HTTP {status}')
        workers = {}
        for row in listing['workers']:
            key = (row.get('controller_id'), row.get('machine_id'))
            if key not in targets:
                continue
            if key in workers:
                raise ValueError('duplicate published switch identity')
            workers[key] = row
        for key, expected_row in targets.items():
            actual = workers.get(key)
            if actual is None:
                record['missing'].append(expected_row)
            else:
                record['workers'].append({k: actual.get(k) for k in
                                          ('controller_id', 'machine_id', 'opted_in', 'hub_accepting')})
                if actual.get('opted_in') is not expected_row['opted_in']:
                    record['mismatched'].append(expected_row)
        record['publication_verified'] = not record['missing'] and not record['mismatched']
        if record['publication_verified']:
            record['state'] = 'switch writes and publication verified'
    except Exception as exc:
        # HTTP bodies/URLs may contain credentials; retain only the diagnostic type.
        record['error'] = type(exc).__name__
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open('x') as handle:
        handle.write(json.dumps(record, indent=2) + '\n')
    return 0 if record['publication_verified'] else 2


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--verified-writes', required=True,
                        help='retained JSON with store_verified and audits_verified true')
    parser.add_argument('--expected', required=True, help='JSON array of exact keyed expected switches')
    parser.add_argument('--evidence', required=True)
    parser.add_argument('--timeout', required=True, type=float, help='total client read deadline in seconds')
    args = parser.parse_args(argv)
    return verify_publication(Gateway(), json.loads(Path(args.expected).read_text()),
                              json.loads(Path(args.verified_writes).read_text()),
                              Path(args.evidence), timeout_s=args.timeout)


if __name__ == '__main__':
    raise SystemExit(main())
