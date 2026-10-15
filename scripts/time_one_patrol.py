"""Time every HTTP request import-patrols makes for one patrol.

    uv run python scripts/time_one_patrol.py --config sync.yaml --patrol 050500_001453

Imports the patrol through the real CLI into a throwaway ledger, then
prints the requests grouped by method, status and path. Run it before and
after a change on the same patrol: that is the benchmark.

The throwaway ledger is deleted afterwards unless it still lists rangers the
run reactivated and could not retire again; then it is kept and its path
printed, because it is the only record of who to retire.

The main ledger never hears of this patrol, so a later main run does not
skip it: it reads the patrol, patches it, finds its events on the site and
re-sends its track (das keeps nothing twice, but the work is repeated).
Pass --ledger with the main ledger's path to record the patrol there instead.
"""

from __future__ import annotations

import argparse
import collections
import json
import os
import re
import shutil
import statistics
import sys
import tempfile
import time

import requests
from click.testing import CliRunner

CALLS: list = []
_orig = requests.Session.request


def _timed(self, method, url, *a, **kw):
    start = time.perf_counter()
    path = url.split("/api/v1.0/")[-1].split("?")[0]
    path = re.sub(r"[0-9a-f]{8}-[0-9a-f-]{27}", "<id>", path)
    status: int | str = "unknown"
    try:
        resp = _orig(self, method, url, *a, **kw)
        status = resp.status_code
        return resp
    except BaseException as exc:
        # Record failed requests too (e.g. ConnectionError, Timeout), then re-raise.
        status = type(exc).__name__
        raise
    finally:
        CALLS.append((method, path, status, time.perf_counter() - start))


def pending_retirements(ledger_path: str) -> int:
    """Rangers a ledger still lists as reactivated, across every site in it.
    An unreadable ledger counts as pending so it is never thrown away."""
    try:
        with open(ledger_path, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return 0
    except (OSError, ValueError):
        return 1
    return sum(len(site.get("reactivated") or []) for site in data.values())


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--config", required=True)
    ap.add_argument("--patrol", required=True, help="SMART patrol id or uuid")
    ap.add_argument("--workers", type=int, default=1)
    ap.add_argument("--ledger", default=None, help="default: a temp file")
    args = ap.parse_args(argv)

    requests.Session.request = _timed  # type: ignore[assignment]
    from er_smart_sync.cli import main as cli_main

    tmpdir = None
    ledger = args.ledger
    if not ledger:
        tmpdir = tempfile.mkdtemp(prefix="time-one-patrol-")
        ledger = os.path.join(tmpdir, "ledger.json")
    t0 = time.perf_counter()
    try:
        res = CliRunner().invoke(
            cli_main,
            [
                "import-patrols",
                "--config",
                args.config,
                "--patrol",
                args.patrol,
                "--ledger",
                ledger,
                "--workers",
                str(args.workers),
            ],
            catch_exceptions=False,
        )
    finally:
        if tmpdir:
            pending = pending_retirements(ledger)
            if pending:
                print(
                    f"KEPT {ledger}: it lists {pending} ranger(s) still reactivated. "
                    "Retire them by re-running import-patrols with "
                    f"--patrol {args.patrol} --ledger {ledger}",
                    file=sys.stderr,
                )
            else:
                shutil.rmtree(tmpdir, ignore_errors=True)
    wall = time.perf_counter() - t0
    print("\n".join(res.output.strip().splitlines()[-4:]))
    inside = sum(c[3] for c in CALLS)
    print(f"\nwall {wall:.1f}s, {len(CALLS)} HTTP requests, {inside:.1f}s inside HTTP")
    groups: dict = collections.defaultdict(list)
    for method, path, status, elapsed in CALLS:
        groups[(method, path, status)].append(elapsed)
    for (method, path, status), xs in sorted(
        groups.items(), key=lambda kv: -sum(kv[1])
    ):
        print(
            f"  {method:6} {status} {path:55} n={len(xs):3} "
            f"total={sum(xs):5.1f}s median={statistics.median(xs) * 1000:5.0f}ms"
        )
    return res.exit_code or 0


if __name__ == "__main__":
    sys.exit(main())
