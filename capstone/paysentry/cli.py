"""PaySentry command line.

Subcommands map one-to-one onto the delivery phases in DESIGN.md §12, and each
declares which phase implements it. Commands from unbuilt phases parse their
arguments, validate config, and then exit cleanly with a pointer to the phase
that will implement them — rather than raising an ImportError. That is
deliberate: it means ``--help`` and argument validation are testable now, and the
CLI surface is agreed before any detector is written.

Stdlib argparse, no click/typer/rich. A dependency that only formats help text
is not worth pinning in a project whose whole point is measuring two other
dependencies.
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import Callable, NoReturn

from .config import Config, ConfigError, PROFILES

PHASES = {
    0: "Scaffold",
    1: "Data generation",
    2: "TigerGraph provisioning & load",
    3: "Hot path",
    4: "Raphtory ingest",
    5: "Temporal analytics",
    6: "Closed loop",
    7: "Evaluation & write-up",
}


class PendingPhase(RuntimeError):
    """A command whose implementing phase has not been built yet."""

    def __init__(self, command: str, phase: int) -> None:
        super().__init__(
            f"'{command}' is delivered by Phase {phase} ({PHASES[phase]}), "
            f"which is not implemented yet.\n"
            f"See capstone/DESIGN.md §12 for the phase plan and its exit criteria."
        )
        self.phase = phase


# --------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------

def cmd_info(args: argparse.Namespace) -> int:
    """Show resolved configuration and environment readiness. Phase 0."""
    cfg = Config.load(args.profile, args.config)
    creds = Config.savanna_credentials()

    print(f"PaySentry — profile '{cfg.profile.name}'")
    print(f"  config      : {cfg.config_path}")
    print(f"  data dir    : {cfg.paths.profile_dir}")
    print(f"  reports dir : {cfg.paths.reports_dir}")
    print()
    print("Dataset shape")
    p = cfg.profile
    print(f"  customers={p.customers}  accounts={p.accounts}  devices={p.devices}  "
          f"merchants={p.merchants}")
    print(f"  transactions={p.transactions}  span={p.span_days}d")
    print()
    print("Planted typologies (rings at this profile)")
    for name, spec in cfg.generation.typologies.as_dict().items():
        print(f"  {name:22s} {p.scaled(spec['rings']):>5}")
    print()
    print("Engines")
    try:
        import raphtory
        print(f"  raphtory     {raphtory.__version__}  (local, in-process)")
    except ImportError:
        print("  raphtory     NOT INSTALLED — run: pip install -r requirements.txt")
    try:
        import pyTigerGraph
        print(f"  pyTigerGraph {pyTigerGraph.__version__}")
    except ImportError:
        print("  pyTigerGraph NOT INSTALLED — run: pip install -r requirements.txt")
    print()
    print("TigerGraph Savanna credentials (.env)")
    for key, value in creds.redacted().items():
        print(f"  {key:10s} {value}")
    print(f"  -> {'ready' if creds.is_configured else 'not configured; --store savanna unavailable'}")
    print()
    print("Data files present")
    for label, path in (("events", cfg.paths.events), ("entities", cfg.paths.entities),
                        ("labels", cfg.paths.labels), ("decisions", cfg.paths.decisions),
                        ("signals", cfg.paths.signals), ("scores", cfg.paths.scores)):
        mark = "yes" if path.exists() else " no"
        size = f"{path.stat().st_size:,} B" if path.exists() else ""
        print(f"  [{mark}] {label:10s} {size}")
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    """Verify the engines: Raphtory locally, TigerGraph over the network."""
    cfg = Config.load(args.profile, args.config)
    ok = True

    if args.engine in ("all", "raphtory"):
        from .temporal.verify import check_raphtory

        print(f"Raphtory — engine, temporal semantics, and the decoy control "
              f"(profile '{cfg.profile.name}')")
        result = check_raphtory(cfg)
        print(result.render())
        ok &= result.ok
        print()

    if args.engine in ("all", "savanna"):
        from .store.connectivity import check_savanna

        creds = Config.savanna_credentials()
        print(f"TigerGraph Savanna — connectivity for graph '{creds.graph}'")
        result = check_savanna(cfg)
        print(result.render())
        ok &= result.ok

    return 0 if ok else 1


def cmd_profile(args: argparse.Namespace) -> int:
    """Profile a CSV before loading it into Raphtory."""
    from pathlib import Path

    from .temporal.profile import profile_csv

    path = Path(args.csv)
    if not path.exists():
        print(f"paysentry: no such file: {path}", file=sys.stderr)
        return 1
    print(f"Profiling {path.name} for Raphtory")
    report, ok = profile_csv(path, limit=args.limit, src=args.src,
                             dst=args.dst, time_col=args.time)
    print(report)
    return 0 if ok else 1


def cmd_show(args: argparse.Namespace) -> int:
    """Look at the Raphtory temporal graph."""
    from .temporal.build import build_graph
    from .temporal.extract import extract
    from .temporal.inspect import (compare_cycles, load_rings, overview,
                                   render_account, render_ring)
    from .timeutil import days

    cfg = Config.load(args.profile, args.config)
    if not cfg.paths.events.exists():
        print(f"paysentry: no dataset at {cfg.paths.events} — run "
              f"'paysentry generate --profile {cfg.profile.name}' first", file=sys.stderr)
        return 1

    if args.compare_cycles:
        print(compare_cycles(cfg, args.count))
        return 0

    if args.ring:
        ring_type, ring_txns, events = load_rings(cfg)
        if args.ring not in ring_type:
            print(f"paysentry: no ring {args.ring!r}. Try --list", file=sys.stderr)
            return 1
        print(render_ring(cfg, args.ring, ring_type, ring_txns, events))
        return 0

    if args.list:
        ring_type, ring_txns, _ = load_rings(cfg)
        by_type: dict[str, list[str]] = {}
        for ring, typology in sorted(ring_type.items()):
            by_type.setdefault(typology, []).append(ring)
        for typology, rings in sorted(by_type.items()):
            print(f"  {typology:22s} {len(rings):>5} rings   "
                  f"e.g. {', '.join(rings[:3])}")
        return 0

    until = cfg.end_time_ms
    since = until - days(min(cfg.detection.temporal.lookback_days,
                             cfg.profile.span_days))
    transfers, device_pairs = extract(cfg, since, until)
    tg = build_graph(transfers, device_pairs)

    if args.account:
        print(render_account(cfg, tg, args.account))
        return 0

    print(f"Raphtory temporal graph — profile '{cfg.profile.name}'")
    print(overview(cfg, tg))
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    """Serve the temporal graph over Raphtory's GraphQL server. Blocks."""
    from .temporal.serve import serve

    cfg = Config.load(args.profile, args.config)
    if not cfg.paths.events.exists():
        print(f"paysentry: no dataset at {cfg.paths.events} — run "
              f"'paysentry generate --profile {cfg.profile.name}' first", file=sys.stderr)
        return 1
    serve(cfg, port=args.port, rebuild=not args.reuse)
    return 0


def cmd_generate(args: argparse.Namespace) -> int:
    """Generate a synthetic dataset with ground truth. Phase 1."""
    from .generator.generate import generate

    cfg = Config.load(args.profile, args.config)
    try:
        result = generate(cfg, seed=args.seed, force=args.force)
    except FileExistsError as exc:
        print(f"paysentry: {exc}", file=sys.stderr)
        return 1

    print()
    print("Planted typologies")
    for name, count in sorted(result.typology_counts.items()):
        print(f"  {name:22s} {count:>7} transactions")
    print()
    print("SHA-256 (identical seeds must produce identical hashes)")
    for name, digest in result.hashes.items():
        print(f"  {name:16s} {digest}")

    if args.verify:
        from .generator.verify import verify

        print()
        print("Structural verification")
        report = verify(cfg)
        print(report.render())
        if not report.ok:
            print("\npaysentry: dataset failed verification", file=sys.stderr)
            return 4
    return 0


def cmd_provision(args: argparse.Namespace) -> int:
    """Create schema and install queries on the selected store. Phase 2."""
    from .store.base import open_store

    cfg = Config.load(args.profile, args.config)
    with open_store(cfg, args.store) as store:
        store.provision(drop=args.drop, reinstall=getattr(args, 'reinstall', False))
        print(f"provisioned '{store.name}' store for profile '{cfg.profile.name}'")
        for name, count in store.counts().items():
            print(f"  {name:14s} {count:>10,}")
    return 0


def cmd_load(args: argparse.Namespace) -> int:
    """Bulk-load a generated dataset into the selected store. Phase 2."""
    from .store.base import EntitySet, iter_events, open_store

    cfg = Config.load(args.profile, args.config)
    if not cfg.paths.events.exists():
        print(f"paysentry: no dataset at {cfg.paths.events} — run "
              f"'paysentry generate --profile {cfg.profile.name}' first", file=sys.stderr)
        return 1

    entities = EntitySet.load(cfg.paths.entities)
    with open_store(cfg, args.store) as store:
        store.provision(drop=args.drop)
        stats = store.bulk_load(entities, iter_events(cfg.paths.events, args.limit))
        print(f"loaded profile '{cfg.profile.name}' into '{store.name}' "
              f"in {stats.elapsed_s}s")
        print(f"  vertices {stats.total_vertices:>10,}")
        for name, count in stats.vertices.items():
            print(f"    {name:12s} {count:>10,}")
        print(f"  edges    {stats.total_edges:>10,}")
        for name, count in stats.edges.items():
            print(f"    {name:12s} {count:>10,}")
    return 0


def cmd_replay(args: argparse.Namespace) -> int:
    """Replay the event log through the hot path, producing decisions. Phase 3."""
    from .pipeline.replay import replay
    from .store.base import open_store

    cfg = Config.load(args.profile, args.config)
    if not cfg.paths.events.exists():
        print(f"paysentry: no dataset at {cfg.paths.events} — run "
              f"'paysentry generate --profile {cfg.profile.name}' first", file=sys.stderr)
        return 1

    print(f"Replaying '{cfg.profile.name}' through the '{args.store}' hot path")
    with open_store(cfg, args.store) as store:
        stats = replay(cfg, store, limit=args.limit, fresh=args.fresh,
                       sample=args.sample)
    print(stats.render(cfg.runtime.latency_budget_ms.total_p95))
    print(f"  decisions  {cfg.paths.decisions}")
    return 0


def cmd_analyze(args: argparse.Namespace) -> int:
    """Build the Raphtory temporal graph and run temporal analytics. Phases 4-5."""
    import json as _json

    from .temporal.build import build_graph
    from .temporal.extract import extract
    from .temporal.score import score_accounts
    from .temporal.signals import TemporalContext, run_all
    from .timeutil import days

    cfg = Config.load(args.profile, args.config)
    if not cfg.paths.events.exists():
        print(f"paysentry: no dataset at {cfg.paths.events} — run "
              f"'paysentry generate --profile {cfg.profile.name}' first", file=sys.stderr)
        return 1

    until = cfg.end_time_ms
    since = until - days(min(cfg.detection.temporal.lookback_days,
                             cfg.profile.span_days))

    store = None
    if args.source == "store":
        from .store.base import open_store
        store = open_store(cfg, args.store)

    print(f"Analyzing '{cfg.profile.name}' with Raphtory (source: {args.source})")
    try:
        transfers, device_pairs = extract(cfg, since, until, store)
        tg = build_graph(transfers, device_pairs)
        print(tg.summary())

        # Merchant settlement and payroll accounts legitimately show large
        # fan-in and fan-out; without this they dominate the mule signal (191 of
        # 201 flagged accounts were businesses). Excluding them is not tuning to
        # the generator — a real system knows which of its accounts are
        # businesses, and Raphtory's graph carries no account type of its own.
        business = frozenset(
            a["account_id"] for a in
            _json.loads(cfg.paths.entities.read_text())["accounts"]
            if a.get("account_type") == "business")
        ctx = TemporalContext(cfg=cfg, tg=tg, since=since, until=until,
                              business_accounts=business)
        print(f"  excluding {len(business):,} business accounts from mule detection")
        signals = run_all(ctx)
    finally:
        if store is not None:
            store.close()

    scores = score_accounts(cfg, signals, scored_at=until)
    scores.sort(key=lambda s: (-s.score, s.account_id))

    # Sorted before writing: detector internals iterate dicts whose order is not
    # guaranteed, so two runs produce identical signals in different order. The
    # evaluation compares files, so the order has to be pinned too.
    signals.sort(key=lambda s: _json.dumps(s.to_dict(), sort_keys=True))
    with cfg.paths.signals.open("w") as handle:
        for signal in signals:
            handle.write(_json.dumps(signal.to_dict()) + "\n")
    with cfg.paths.scores.open("w") as handle:
        for score in scores:
            handle.write(_json.dumps(score.to_dict()) + "\n")

    counts: dict[str, int] = {}
    for signal in signals:
        counts[str(signal.kind)] = counts.get(str(signal.kind), 0) + 1
    print()
    print("Signals")
    for kind, count in sorted(counts.items()):
        marker = "  (control)" if kind == "static_cycle" else ""
        print(f"  {kind:24s} {count:>7}{marker}")
    print()
    print("Timings")
    for name, seconds in ctx.timings.items():
        print(f"  {name:24s} {seconds:>7.2f}s")
    print()
    print(f"Scored {len(scores)} accounts; top 5:")
    for score in scores[:5]:
        print(f"  {score.account_id}  {score.score:6.2f}  {', '.join(score.reasons)}")
    print()
    print(f"  signals  {cfg.paths.signals}")
    print(f"  scores   {cfg.paths.scores}")
    return 0


def cmd_writeback(args: argparse.Namespace) -> int:
    """Push Raphtory-derived risk scores back into the store. Phase 6."""
    from .feedback.writeback import load_scores, write_back
    from .store.base import open_store

    cfg = Config.load(args.profile, args.config)
    if not cfg.paths.scores.exists():
        print(f"paysentry: no scores at {cfg.paths.scores} — run "
              f"'paysentry analyze --profile {cfg.profile.name}' first", file=sys.stderr)
        return 1

    scores = load_scores(cfg.paths.scores, args.min_score)
    with open_store(cfg, args.store) as store:
        stats = write_back(store, scores)
        print(f"wrote {stats.written:,} risk scores into '{store.name}' "
              f"(max {stats.max_score:.1f}, {stats.rings} rings)")

        sample = store.account_risk([s.account_id for s in scores[:5]])
        print("\n  read back from the store:")
        for account, risk in sorted(sample.items()):
            print(f"    {account}  score={risk.score:6.2f}  ring={risk.ring_id}  "
                  f"reasons={len(risk.reasons)}")
    print("\n  the hot path's counterparty_risk check now reads these on the next replay")
    return 0


def cmd_evaluate(args: argparse.Namespace) -> int:
    """Score both engines against ground truth and write the report. Phase 7."""
    from .evaluation.metrics import GroundTruth, load_detections
    from .evaluation.report import render

    cfg = Config.load(args.profile, args.config)
    missing = [str(p) for p in (cfg.paths.labels, cfg.paths.decisions, cfg.paths.signals)
               if not p.exists()]
    if missing:
        print("paysentry: missing inputs — run generate / replay / analyze first:",
              file=sys.stderr)
        for path in missing:
            print(f"  {path}", file=sys.stderr)
        return 1

    truth = GroundTruth.load(cfg.paths.labels)
    found = load_detections(cfg.paths.decisions, cfg.paths.signals)

    # If a run against the other store was preserved, compare them.
    cross = None
    savanna = cfg.paths.decisions_for("savanna")
    if savanna.exists() and cfg.paths.decisions.exists():
        from .evaluation.cross_engine import compare
        cross = compare(savanna, cfg.paths.decisions, "TigerGraph (Savanna)",
                        "SQLite (LocalStore)", note=args.cross_note or "")

    text = render(cfg, truth, found, cross=cross)

    cfg.paths.reports_dir.mkdir(parents=True, exist_ok=True)
    out = cfg.paths.reports_dir / f"evaluation-{cfg.profile.name}.md"
    out.write_text(text + "\n")
    print(text)
    print(f"\nwritten: {out}")
    return 0


# --------------------------------------------------------------------------
# Parser
# --------------------------------------------------------------------------

def _add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--profile", choices=PROFILES, default="small",
                        help="dataset size profile (default: small)")
    parser.add_argument("--config", default=None, metavar="PATH",
                        help="path to config.yaml (default: capstone/config.yaml)")


def _add_store(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--store", choices=("local", "savanna"), default="local",
                        help="local = SQLite fallback, free and offline; "
                             "savanna = TigerGraph cloud, burns free-tier credits "
                             "(default: local)")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="paysentry",
        description="Real-time payments fraud/AML detection on TigerGraph + Raphtory. "
                    "See capstone/DESIGN.md for the architecture.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Typical flow:\n"
               "  paysentry info\n"
               "  paysentry generate --profile small --seed 42\n"
               "  paysentry replay   --profile small --store local\n"
               "  paysentry analyze  --profile small\n"
               "  paysentry evaluate --profile small\n",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="verbose output")
    subs = parser.add_subparsers(dest="command", metavar="<command>")

    specs: list[tuple[str, str, Callable[[argparse.Namespace], int], bool]] = [
        ("info", "show resolved config, installed engines, and data files [phase 0]", cmd_info, False),
        ("check", "verify the engines (Raphtory and/or TigerGraph)", cmd_check, False),
        ("profile", "profile a CSV before loading it into Raphtory", cmd_profile, False),
        ("show", "look at the Raphtory temporal graph", cmd_show, False),
        ("serve", "serve the temporal graph over Raphtory's GraphQL UI", cmd_serve, False),
        ("generate", "generate a synthetic dataset with ground truth [phase 1]", cmd_generate, False),
        ("provision", "create schema and install queries [phase 2]", cmd_provision, True),
        ("load", "bulk-load a generated dataset into a store [phase 2]", cmd_load, True),
        ("replay", "replay the event log through the hot path [phase 3]", cmd_replay, True),
        ("analyze", "run Raphtory temporal analytics [phases 4-5]", cmd_analyze, True),
        ("writeback", "push risk scores back into the store [phase 6]", cmd_writeback, True),
        ("evaluate", "score both engines against ground truth [phase 7]", cmd_evaluate, True),
    ]
    for name, help_text, handler, needs_store in specs:
        sub = subs.add_parser(name, help=help_text, description=handler.__doc__)
        _add_common(sub)
        if needs_store:
            _add_store(sub)
        sub.set_defaults(func=handler)

    subs.choices["check"].add_argument(
        "--engine", choices=("all", "raphtory", "savanna"), default="all",
        help="which engine to verify (default: all)")
    subs.choices["check"].add_argument(
        "--threads", type=int, default=1,
        help="rayon threads, matching analyze's default (default: 1)")

    prof = subs.choices["profile"]
    prof.add_argument("csv", help="path to the CSV to profile")
    prof.add_argument("--src", default=None, help="force the source-id column")
    prof.add_argument("--dst", default=None, help="force the destination-id column")
    prof.add_argument("--time", default=None, help="force the timestamp column")
    prof.add_argument("--limit", type=int, default=200_000,
                      help="profile at most N rows (default: 200000)")
    prof.add_argument("--threads", type=int, default=1, help=argparse.SUPPRESS)

    srv = subs.choices["serve"]
    srv.add_argument("--port", type=int, default=1736, help="default: 1736")
    srv.add_argument("--reuse", action="store_true",
                     help="reuse a previously saved graph instead of rebuilding")
    srv.add_argument("--threads", type=int, default=1, help=argparse.SUPPRESS)

    show = subs.choices["show"]
    show.add_argument("--compare-cycles", action="store_true",
                      help="real layering rings beside decoys — the central contrast")
    show.add_argument("--ring", metavar="RING_ID",
                      help="show one planted ring in detail, e.g. CIRC-0000")
    show.add_argument("--account", metavar="ACCOUNT_ID",
                      help="show one account's timeline")
    show.add_argument("--list", action="store_true", help="list planted rings")
    show.add_argument("--count", type=int, default=2,
                      help="how many of each to show with --compare-cycles")
    show.add_argument("--threads", type=int, default=1, help=argparse.SUPPRESS)

    gen = subs.choices["generate"]
    gen.add_argument("--seed", type=int, default=42,
                     help="RNG seed; identical seeds must produce identical output "
                          "(default: 42)")
    gen.add_argument("--force", action="store_true",
                     help="overwrite an existing dataset for this profile")
    gen.add_argument("--verify", action="store_true",
                     help="check the written dataset against the structural "
                          "claims in DESIGN.md §4.3")

    for name in ("provision", "load"):
        subs.choices[name].add_argument(
            "--drop", action="store_true",
            help="drop and recreate the schema before loading (DESTROYS data)")
    subs.choices["provision"].add_argument(
        "--reinstall", action="store_true",
        help="recompile the queries, leaving schema and data intact")
    subs.choices["writeback"].add_argument(
        "--min-score", type=float, default=0.0, dest="min_score",
        help="only write back scores at or above this value")
    subs.choices["load"].add_argument(
        "--limit", type=int, default=None,
        help="load only the first N transactions (smoke tests)")

    rep = subs.choices["replay"]
    rep.add_argument("--limit", type=int, default=None,
                     help="stop after N transactions (smoke tests)")
    rep.add_argument("--sample", type=int, default=None,
                     help="screen N evenly-spaced transactions instead of all of "
                          "them; each still sees its full history. Use against "
                          "--store savanna, where a full replay costs uptime")
    rep.add_argument("--fresh", action="store_true",
                     help="drop the store and replay into an empty one, so the "
                          "hot path never sees a transaction before it arrives")
    rep.add_argument("--speed", type=float, default=0.0,
                     help="simulated-time acceleration; 0 = as fast as possible "
                          "(default: 0)")

    ana = subs.choices["analyze"]
    subs.choices["evaluate"].add_argument(
        "--cross-note", default=None, dest="cross_note",
        help="caveat to print under the cross-engine comparison")

    ana.add_argument("--threads", type=int, default=1,
                     help="rayon threads for Raphtory algorithms; 1 keeps results "
                          "reproducible (default: 1)")
    ana.add_argument("--source", choices=("store", "log"), default="log",
                     help="build the temporal graph from the store's export or "
                          "straight from the event log (default: log)")

    return parser


def main(argv: list[str] | None = None) -> int | NoReturn:
    parser = build_parser()
    args = parser.parse_args(argv)

    # Raphtory's parallel algorithms (pagerank, louvain, label_propagation) give
    # different answers run to run — and label_propagation's `seed` argument does
    # not fix it. Pinning rayon to one thread makes them reproducible, which an
    # evaluation harness needs. Measured cost on this workload: none worth
    # reporting. See docs/decisions/002-community-detection.md.
    # Must be set before raphtory is first imported, hence here rather than in
    # the analyze command.
    threads = getattr(args, "threads", None)
    if threads:
        os.environ["RAYON_NUM_THREADS"] = str(threads)

    if not getattr(args, "command", None):
        parser.print_help()
        return 0

    try:
        return args.func(args)
    except PendingPhase as exc:
        print(f"paysentry: {exc}", file=sys.stderr)
        return 2
    except ConfigError as exc:
        print(f"paysentry: configuration error: {exc}", file=sys.stderr)
        return 3
    except KeyboardInterrupt:
        print("\npaysentry: interrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
