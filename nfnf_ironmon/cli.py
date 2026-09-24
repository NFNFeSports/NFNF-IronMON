"""Command-line interface: ``python -m nfnf_ironmon <command>``."""

from __future__ import annotations

import argparse
import json
import logging
import sys

from . import APP_NAME, __version__
from .app import Application, dumps
from .events import registered_event_types


def _print_runs(app: Application) -> None:
    runs = app.list_runs()
    if not runs:
        print("No runs yet. Start one with: python -m nfnf_ironmon run new")
    for r in runs:
        flag = " (archived)" if r.archived else ""
        print(f"{r.id}  {r.status.value:<10} {r.game_id:<8} seed={r.row['seed']}  "
              f"integrity={r.integrity_status}{flag}")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="nfnf-ironmon", description=f"{APP_NAME} {__version__}")
    p.add_argument("--home", help="Portable data folder (default: application folder or $NFNF_IRONMON_HOME)")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init", help="Create folders, config and database")
    dr = sub.add_parser("doctor", help="Status of every part of the standalone application")
    dr.add_argument("--json", action="store_true")
    sub.add_parser("career", help="IronMON career: attempts, best progress, play time")

    comp = sub.add_parser("components", help="Bundled third-party components").add_subparsers(dest="comp_cmd", required=True)
    comp.add_parser("list")
    cf = comp.add_parser("fetch", help="Download + verify components into the app folder (developer/packager step)")
    cf.add_argument("ids", nargs="*", help="component ids (default: all for this platform)")
    cf.add_argument("--platform", help="e.g. linux-x64, windows-x64")
    cf.add_argument("--pin", action="store_true", help="record sha256 for components without a pinned hash")

    ctl = sub.add_parser("controller", help="Controllers / input").add_subparsers(dest="ctl_cmd", required=True)
    ctl.add_parser("list", help="Backends and detected devices")
    ctl.add_parser("mappings", help="Available input mappings")
    ct = ctl.add_parser("test", help="Show live input for a few seconds")
    ct.add_argument("--seconds", type=float, default=10.0)

    emu = sub.add_parser("emulator", help="Integrated emulator engine").add_subparsers(dest="emu_cmd", required=True)
    emu.add_parser("info", help="Capabilities of each emulator adapter")
    es = emu.add_parser("smoke", help="Boot a run's ROM headless in the integrated core")
    es.add_argument("run_id")
    es.add_argument("--frames", type=int, default=600)
    es.add_argument("--screenshot", help="PNG path (default: <run>/logs/smoke.png)")
    sub.add_parser("games", help="List game adapters")
    sub.add_parser("events", help="List registered event types")

    rom = sub.add_parser("rom", help="Manage user-provided original ROMs").add_subparsers(dest="rom_cmd", required=True)
    ri = rom.add_parser("import", help="Validate, identify and copy a ROM you own (original is never modified)")
    ri.add_argument("path")
    rs = rom.add_parser("inspect", help="Identify a ROM without importing it")
    rs.add_argument("path")
    rom.add_parser("list")
    rom.add_parser("scan", help="Discover ROMs in games/original/ (read-only)")

    rules = sub.add_parser("rules", help="Rulesets").add_subparsers(dest="rules_cmd", required=True)
    rules.add_parser("list")
    rsh = rules.add_parser("show")
    rsh.add_argument("id")

    run = sub.add_parser("run", help="Runs").add_subparsers(dest="run_cmd", required=True)
    rn = run.add_parser("new", help="Start a new run")
    rn.add_argument("--game")
    rn.add_argument("--ruleset")
    rn.add_argument("--profile", help="Randomizer profile id")
    rn.add_argument("--seed", type=int)
    rn.add_argument("--emulator")
    rn.add_argument("--tracker")
    rn.add_argument("--no-launch", action="store_true", help="Stop at READY")
    run.add_parser("list")
    for name, helptext in (("show", "Show run details"), ("verify", "Run integrity verification"),
                           ("complete", "Mark run COMPLETED and archive"),
                           ("archive", "Archive a finished run")):
        x = run.add_parser(name, help=helptext)
        x.add_argument("run_id")
    for name, helptext in (("fail", "Mark run FAILED and archive"),
                           ("abandon", "Mark run ABANDONED and archive"),
                           ("restart", "Fail + archive this run, then start a fresh one")):
        x = run.add_parser(name, help=helptext)
        x.add_argument("run_id")
        x.add_argument("--reason")
    ev = run.add_parser("event", help="Record an event manually (player/tracker simulation)")
    ev.add_argument("run_id")
    ev.add_argument("type")
    ev.add_argument("--payload", default="{}", help="JSON object")

    sub.add_parser("ui", help="Open the desktop UI")
    sv = sub.add_parser("serve", help="Local JSON API on 127.0.0.1")
    sv.add_argument("--port", type=int, default=8765)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    try:
        with Application(args.home) as app:
            return _dispatch(app, args)
    except Exception as exc:  # noqa: BLE001 — report any failure as a one-line CLI error
        print(f"error: {exc}", file=sys.stderr)
        return 1


def _dispatch(app: Application, args: argparse.Namespace) -> int:
    if args.cmd == "init":
        print(f"{APP_NAME} home: {app.paths.home}\nDatabase: {app.paths.db_path}\n"
              f"Place your own ROM dumps anywhere and import them with: rom import <path>")
    elif args.cmd == "doctor":
        from .doctor import render
        report = app.doctor()
        print(dumps(report) if args.json else render(report))
    elif args.cmd == "career":
        print(app.career().render())
    elif args.cmd == "components":
        return _components_cmd(app, args)
    elif args.cmd == "controller":
        return _controller_cmd(app, args)
    elif args.cmd == "emulator":
        return _emulator_cmd(app, args)
    elif args.cmd == "games":
        for g in app.list_games():
            print(f"{g['id']:<8} {g['name']:<18} gen {g['generation']}  {g['status']:<9} "
                  f"roms={g['roms_imported']}")
    elif args.cmd == "events":
        for name, desc in sorted(registered_event_types().items()):
            print(f"{name:<20} {desc}")
    elif args.cmd == "rom":
        if args.rom_cmd == "import":
            rec = app.import_rom(args.path)
            print(dumps(rec.metadata))
        elif args.rom_cmd == "inspect":
            identity, digests = app.roms.inspect(args.path)
            print(dumps({"identity": identity.to_dict(), **digests}))
        elif args.rom_cmd == "scan":
            res = app.roms.scan()
            for r in res.registered:
                print(f"registered  {r.game_id:<8} {r.version:<30} {r.path.name}")
            for r in res.known:
                print(f"known       {r.game_id:<8} {r.version:<30} {r.path.name}")
            for f in res.unrecognised:
                print(f"unrecognised {f.name}")
            for p in res.problems:
                print(f"problem     {p}")
        else:
            for r in app.roms.list():
                print(f"{r.game_id:<8} {r.version:<32} {r.sha256}")
    elif args.cmd == "rules":
        if args.rules_cmd == "list":
            for rs in app.rulesets.list():
                print(f"{rs.id:<20} v{rs.version}  {rs.name}  ({len(rs.rules)} rules)")
        else:
            rs = app.rulesets.load(args.id)
            print(f"{rs.name} v{rs.version}  sha256={rs.sha256}")
            for r in rs.rules:
                print(f"  [{r.severity:<6}] {r.id:<28} on {r.event_type or '-':<18} -> {r.failure_behavior}")
    elif args.cmd == "run":
        return _run_cmd(app, args)
    elif args.cmd == "ui":
        from .ui import launch_ui
        launch_ui(app)
    elif args.cmd == "serve":
        from .api import serve
        serve(app, port=args.port)
    return 0


def _components_cmd(app: Application, args: argparse.Namespace) -> int:
    cm = app.components
    if args.comp_cmd == "list":
        for c in cm.all_status():
            print(f"{c.id:<22} {c.state:<21} {c.version:<22} {c.license}")
        return 0
    if args.platform:
        cm.platform = args.platform
    ids = args.ids or [i for i in cm.ids() if cm.platform_spec(i)]
    for cid in ids:
        entry = cm.fetch(cid, args.platform, pin=args.pin)
        print(f"{cid:<22} OK  {entry}")
    return 0


def _controller_cmd(app: Application, args: argparse.Namespace) -> int:
    cm = app.controllers
    if args.ctl_cmd == "list":
        for b in cm.backend_status():
            print(f"backend {b['backend']:<15} {b['status']:<12} {b['detail']}")
        devices = cm.devices()
        for d in devices:
            flag = "gamepad" if d.is_gamepad else "ignored"
            print(f"device  {d.name:<35} {d.kind:<8} {flag:<8} {d.id}  {'; '.join(d.notes)}")
        if not devices:
            print("device  (none detected)")
        sel = cm.select_device()
        print(f"selected: {sel.name if sel else 'none'}   mapping: {cm.mapping_id}")
    elif args.ctl_cmd == "mappings":
        for m in cm.mappings.list():
            print(f"{m.id}: {m.name}")
            for phys, logical in m.describe():
                print(f"    {phys:<12} -> {logical}")
    else:
        if cm.open() is None:
            print("No gamepad detected. Connect an Xbox/XInput controller and try again.")
            return 1
        print(f"Testing {cm._open.device.name} for {args.seconds:.0f}s — press buttons (Ctrl+C to stop)")
        cm.test(args.seconds, lambda phys, logical: print(
            f"  physical: {' '.join(sorted(phys)) or '-':<30} NFNF: {' '.join(sorted(logical)) or '-'}"))
    return 0


def _emulator_cmd(app: Application, args: argparse.Namespace) -> int:
    if args.emu_cmd == "info":
        for eid, e in app.emulators.items():
            inst = e.detect_installation()
            print(f"{eid:<14} {e.status:<10} installed={inst.found!s:<5} {e.capabilities()}")
        return 0
    run = app.runs.get(args.run_id)
    meta = app.runs.metadata(run.id)
    if not meta.get("rom"):
        raise ValueError(f"{run.id} has no ROM")
    import tempfile
    from pathlib import Path
    shot = Path(args.screenshot) if args.screenshot else None
    if shot is None and not run.archived:
        shot = run.path / "logs" / "smoke.png"
    with tempfile.TemporaryDirectory(prefix="nfnf-smoke-") as tmp:
        res = app.emulators["nfnf-libretro"].smoke_test(app.paths.abs(meta["rom"]), Path(tmp),
                                                        args.frames, shot)
    print(dumps(res.to_dict()))
    return 0


def _run_cmd(app: Application, args: argparse.Namespace) -> int:
    c = args.run_cmd
    if c == "new":
        run = app.new_run(game_id=args.game, ruleset_id=args.ruleset, profile_id=args.profile,
                          seed=args.seed, emulator_id=args.emulator, tracker_id=args.tracker,
                          launch=not args.no_launch)
        print(dumps(app.run_summary(run)))
    elif c == "list":
        _print_runs(app)
    elif c == "show":
        run = app.runs.get(args.run_id)
        print(dumps({"summary": app.run_summary(run), "metadata": app.runs.metadata(run.id),
                     "events": len(app.runs.events(run.id))}))
    elif c == "verify":
        print(dumps(app.verify_run(args.run_id).to_dict()))
    elif c == "fail":
        print(dumps(app.run_summary(app.fail_run(args.run_id, args.reason or "player_failed"))))
    elif c == "abandon":
        print(dumps(app.run_summary(app.abandon_run(args.run_id, args.reason or "abandoned"))))
    elif c == "complete":
        print(dumps(app.run_summary(app.complete_run(args.run_id))))
    elif c == "archive":
        print(dumps(app.runs.archive(args.run_id)))
    elif c == "restart":
        print(dumps(app.run_summary(app.restart_run(args.run_id, args.reason or "player_failed"))))
    elif c == "event":
        payload = json.loads(args.payload)
        if not isinstance(payload, dict):
            raise ValueError("--payload must be a JSON object")
        print(dumps(app.record_event(args.run_id, args.type, payload)))
        print(f"Run is now {app.runs.get(args.run_id).status.value}")
    return 0
