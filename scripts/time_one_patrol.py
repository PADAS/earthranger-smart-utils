"""Time every HTTP request import-patrols makes for one patrol.

    uv run python scripts/time_one_patrol.py --config sync.yaml --patrol 050500_001453

Imports the patrol through the real CLI into a throwaway ledger (so the
main ledger is untouched; the main run later finds the patrol on the site
and skips it), then prints the requests grouped by method, status and path.
Run it before and after a change on the same patrol: that is the benchmark.
"""

from __future__ import annotations

import argparse
import collections
import re
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


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--config", required=True)
    ap.add_argument("--patrol", required=True, help="SMART patrol id or uuid")
    ap.add_argument("--workers", type=int, default=1)
    ap.add_argument("--ledger", default=None, help="default: a temp file")
    args = ap.parse_args(argv)

    requests.Session.request = _timed  # type: ignore[assignment]
    from er_smart_sync.cli import main as cli_main

    ledger = args.ledger or tempfile.mktemp(  # ty: ignore[deprecated]
        suffix=".json", prefix="time-one-patrol-"
    )
    t0 = time.perf_counter()
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
