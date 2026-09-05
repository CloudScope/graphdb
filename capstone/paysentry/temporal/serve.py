"""Serve the temporal graph from Raphtory's GraphQL server.

Raphtory is normally *embedded*: the graph lives in the calling process and dies
with it. That is why a server started inside a short script is unreachable a
moment later — there is no daemon, nothing survives the interpreter exiting.

``GraphServer`` is the other mode. It loads graphs from a working directory and
serves them over GraphQL with a browsable UI, and it keeps running as long as
this process does — so it has to block, which is what ``run()`` does.

Worth being precise about what this is and is not. It is a query service over
saved graphs, with a cache and a TTL. It is not a distributed transactional
store: no sharding, no ACID guarantees across writes, no authorization-path
latency budget. The distinction from a system of record is one of degree rather
than kind, but the degree is the whole argument.
"""

from __future__ import annotations

from pathlib import Path

from ..config import Config
from ..timeutil import days


def serve(cfg: Config, port: int = 1736, rebuild: bool = True) -> None:
    """Build (or reuse) the profile's temporal graph and serve it. Blocks."""
    from raphtory.graphql import GraphServer

    work_dir = cfg.paths.profile_dir / "graphql"
    work_dir.mkdir(parents=True, exist_ok=True)
    saved = work_dir / cfg.profile.name

    if rebuild or not saved.exists():
        from .build import build_graph
        from .extract import extract

        until = cfg.end_time_ms
        since = until - days(min(cfg.detection.temporal.lookback_days,
                                 cfg.profile.span_days))
        print(f"  building '{cfg.profile.name}' temporal graph...")
        transfers, device_pairs = extract(cfg, since, until)
        tg = build_graph(transfers, device_pairs)
        print(tg.summary())
        # The server reads graphs off disk, so the in-memory graph has to be
        # persisted before it can be served.
        tg.graph.save_to_file(str(saved))
        print(f"  saved to {saved}")
    else:
        print(f"  reusing {saved}")

    print()
    print(f"  Raphtory GraphQL server on http://localhost:{port}")
    print(f"  graph path: '{cfg.profile.name}'")
    print()
    print("  try, in the UI's query pane:")
    print(f'    {{ graph(path: "{cfg.profile.name}") {{ nodes {{ list {{ name }} }} }} }}')
    print()
    print("  Ctrl-C to stop — the server lives in THIS process and stops with it.")
    print()
    GraphServer(work_dir=str(work_dir)).run(port=port)
