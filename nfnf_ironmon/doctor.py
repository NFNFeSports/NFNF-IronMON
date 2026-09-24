"""``doctor``: status of every part of the eventual standalone application.

Status words (never inflated):
    OK / FOUND / BUNDLED     present and working
    READY                    implemented, tested, usable now
    PROTOTYPE                works in a limited, tested form (e.g. headless)
    IMPLEMENTED (UNTESTED)   code exists but could not be exercised on this machine
    SYSTEM (DEV ONLY)        works via a manually installed program — not allowed for users
    PLANNED                  designed, not implemented
    MISSING / INVALID        needed and absent / broken
    N/A                      does not apply to this OS
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING, Any

from . import __version__
from .platform_support import describe_host, os_family
from .randomizers.rnqs import RnqsError, load_rnqs

if TYPE_CHECKING:
    from .app import Application


def _item(label: str, status: str, detail: str = "") -> dict[str, str]:
    return {"label": label, "status": status, "detail": detail}


def build_report(app: "Application") -> dict[str, Any]:
    frozen = bool(getattr(sys, "frozen", False))
    host = describe_host()
    sections: list[dict[str, Any]] = []

    sections.append({"title": "Core", "items": [
        _item("Python runtime", "BUNDLED" if frozen else "OK",
              f"{host['python']} ({'frozen build' if frozen else 'development runtime'})"),
        _item("Database", "OK", f"schema v{app.db.schema_version}, {app.paths.rel(app.paths.db_path)}"),
        _item("App root", "OK", str(app.paths.app_root)),
        _item("User data", "OK", str(app.paths.home)),
    ]})

    scan = app.roms.scan()   # read-only discovery of games/original/
    items = []
    for g in app.games.all():
        roms = app.roms.list(g.game_id)
        runs = "runs supported" if g.status == "supported" else "identification only"
        items.append(_item(g.display_name, "FOUND" if roms else "MISSING",
                           f"{roms[0].version} [{runs}]" if roms else f"no original ROM [{runs}]"))
    for problem in scan.problems:
        items.append(_item("ROM problem", "INVALID", problem))
    sections.append({"title": "Games", "items": items})

    upr = app.randomizers.get("upr-zx")
    java, jsrc = upr.java_path()
    jar, _ = upr.jar_path()
    java_status = {"bundled": "BUNDLED", "config": "OK", "system": "SYSTEM (DEV ONLY)"}.get(jsrc, "MISSING")
    items = [
        _item("UPR ZX", "FOUND" if jar else "MISSING",
              f"v{upr.info().version}, GPL-3.0, {jar}" if jar else "components fetch"),
        _item("Java runtime", java_status, str(java) if java else "components fetch"),
    ]
    for p in app.profiles.list():
        if p.randomizer != "upr-zx":
            continue
        try:
            info = load_rnqs(upr.settings_file(p.settings))
            items.append(_item(f"Profile {p.id}", "FOUND",
                               f"settings v{info.version}, ROM '{info.rom_name}', sha256 {info.sha256[:12]}…"))
        except RnqsError as exc:
            status = "MISSING" if "not found" in str(exc) else "INVALID"
            items.append(_item(f"Profile {p.id}", status, str(exc)))
    items.append(_item("Seed reproducibility", "NOT DETERMINISTIC",
                       "UPR CLI has no seed option; actual seed read from its log"))
    sections.append({"title": "Randomizer", "items": items})

    integ = app.emulators["nfnf-libretro"]
    inst = integ.detect_installation()
    sections.append({"title": "Emulator", "items": [
        _item("Integrated backend", integ.status if inst.found else "MISSING",
              "mGBA core via libretro, headless (boot/video/input/memory/saves tested)"
              if inst.found else "; ".join(inst.notes)),
        _item("Interactive play", "PLANNED", "window, audio, pacing — Phase 3"),
        *[_item(f"External {e.display_name}", "FOUND" if e.detect_installation().found else "NOT INSTALLED",
                "optional development path, not required")
          for k, e in app.emulators.items() if k in ("bizhawk", "mgba")],
    ]})

    items = []
    for b in app.controllers.backend_status():
        status = b["status"]
        if b["backend"] == "xinput" and status == "READY":
            status = "IMPLEMENTED (UNTESTED)"
        items.append(_item(b["name"], status, b["detail"]))
    if os_family() != "windows":
        items.append(_item("XInput backend", "N/A", "Windows only (implemented, untested — no Windows host)"))
    try:
        devices = app.controllers.devices()
    except Exception as exc:  # noqa: BLE001
        devices, items = [], items + [_item("Device scan", "INVALID", str(exc))]
    pads = [d for d in devices if d.is_gamepad]
    items.append(_item("Gamepad", "FOUND" if pads else "NOT DETECTED",
                       ", ".join(f"{d.name} ({d.kind})" for d in pads) or
                       f"{len(devices)} non-gamepad joystick node(s) ignored" if devices else "none connected"))
    items.append(_item("Mapping", "OK", app.controllers.mapping_id))
    sections.append({"title": "Controller", "items": items})

    sections.append({"title": "Tracker", "items": [
        _item("Integrated tracker engine", "PLANNED", "Phase 3: memory → events inside NFNF"),
        _item("Community Ironmon-Tracker", "NOT INSTALLED", "reference only; not required"),
    ]})

    sections.append({"title": "Standalone build", "items": [
        _item("Windows", "PLANNED", "PyInstaller onedir + bundled JRE/core (see docs/packaging.md)"),
        _item("Linux", "PORTABLE BUILD" if frozen else "DEVELOPMENT",
              "running from a frozen build" if frozen else "running from source"),
    ]})

    comps = [c.to_dict() for c in app.components.all_status()]
    sections.append({"title": "Bundled components", "items": [
        _item(c["id"], c["state"], f"{c['version'][:24]}, {c['license']}") for c in comps]})

    return {"version": __version__, "host": host, "sections": sections, "components": comps}


def render(report: dict[str, Any]) -> str:
    lines = [f"NFNF IronMON Doctor  (v{report['version']}, {report['host']['os']})", ""]
    for sec in report["sections"]:
        lines.append(sec["title"])
        for it in sec["items"]:
            detail = f"  {it['detail']}" if it["detail"] else ""
            lines.append(f"    {it['label']:<32} {it['status']:<22}{detail}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"
