"""NFNF IronMON launcher (Tkinter): pick a game and rules, check the controller, START NEW RUN.

The game itself plays in the SDL game window (frontend/session.py); while it is
open this window is hidden, so the user sees one application at a time.
"""

from __future__ import annotations

import json
import tkinter as tk
from tkinter import messagebox, ttk
from typing import Any

from . import APP_NAME, __version__
from .app import Application, dumps
from .capabilities import FEATURES, game_capabilities, overall
from .career import fmt_duration
from .controllers import LOGICAL_BUTTONS, PHYSICAL_BUTTONS
from .events import EventType
from .platform_support import open_in_file_manager
from .recovery import find_interrupted
from .runs import RunState


class Launcher:
    def __init__(self, root: tk.Tk, app: Application, check_recovery: bool = True):
        self.root, self.app = root, app
        root.title(f"{APP_NAME} {__version__}")
        root.geometry("760x640")
        root.minsize(620, 560)
        self._menu()
        pad = {"padx": 12, "pady": 4}
        ttk.Label(root, text="NFNF IRONMON", font=("TkDefaultFont", 20, "bold")).pack(anchor="w", padx=12, pady=(12, 4))
        form = ttk.Frame(root)
        form.pack(fill="x", **pad)
        form.columnconfigure(1, weight=1)

        ttk.Label(form, text="Game:").grid(row=0, column=0, sticky="w", pady=3)
        self.game_var = tk.StringVar()
        self.game_box = ttk.Combobox(form, textvariable=self.game_var, state="readonly")
        self.game_box.grid(row=0, column=1, sticky="ew", pady=3)
        self.game_box.bind("<<ComboboxSelected>>", lambda e: self._game_changed())

        ttk.Label(form, text="Rules:").grid(row=1, column=0, sticky="w", pady=3)
        self.rules_var = tk.StringVar()
        self.rules_box = ttk.Combobox(form, textvariable=self.rules_var, state="readonly")
        self.rules_box.grid(row=1, column=1, sticky="ew", pady=3)

        ttk.Label(form, text="Controller:").grid(row=2, column=0, sticky="w", pady=3)
        ctl = ttk.Frame(form)
        ctl.grid(row=2, column=1, sticky="ew", pady=3)
        self.ctl_var = tk.StringVar(value="…")
        ttk.Label(ctl, textvariable=self.ctl_var).pack(side="left")
        ttk.Button(ctl, text="CONFIGURE", command=self.configure_controller).pack(side="right", padx=2)
        ttk.Button(ctl, text="TEST CONTROLLER", command=self.test_controller).pack(side="right", padx=2)

        self.info_var = tk.StringVar()
        ttk.Label(form, textvariable=self.info_var, justify="left").grid(row=3, column=0, columnspan=2, sticky="w",
                                                                         pady=(10, 4))
        self.status_var = tk.StringVar()
        ttk.Label(form, textvariable=self.status_var, foreground="#b35c00", wraplength=700,
                  justify="left").grid(row=4, column=0, columnspan=2, sticky="w")

        buttons = ttk.Frame(root)
        buttons.pack(fill="x", **pad)
        style = ttk.Style()
        style.configure("Big.TButton", font=("TkDefaultFont", 13, "bold"), padding=10)
        self.start_btn = ttk.Button(buttons, text="START NEW RUN", style="Big.TButton", command=self.start_new_run)
        self.start_btn.pack(side="left")
        self.continue_btn = ttk.Button(buttons, text="CONTINUE RUN", command=self.continue_run)
        self.continue_btn.pack(side="left", padx=8)

        ttk.Label(root, text="Run history").pack(anchor="w", padx=12, pady=(10, 0))
        cols = ("run", "game", "status", "time", "reason", "integrity")
        self.tree = ttk.Treeview(root, columns=cols, show="headings", height=10)
        for c, w in zip(cols, (90, 130, 90, 80, 220, 90)):
            self.tree.heading(c, text=c.upper())
            self.tree.column(c, width=w, anchor="w")
        self.tree.pack(fill="both", expand=True, padx=12)
        self.tree.bind("<Double-1>", lambda e: self.view_run())
        rb = ttk.Frame(root)
        rb.pack(fill="x", padx=12, pady=6)
        ttk.Button(rb, text="VIEW RUN", command=self.view_run).pack(side="left")
        ttk.Button(rb, text="VERIFY INTEGRITY", command=self.verify_run).pack(side="left", padx=4)
        ttk.Button(rb, text="OPEN RUN FOLDER", command=self.open_run_folder).pack(side="left")
        ttk.Button(rb, text="ABANDON RUN", command=self.abandon_selected).pack(side="right")

        self._game_ids: list[str] = []
        self.refresh()
        if check_recovery:
            root.after(300, self.check_recovery)

    # ------------------------------------------------------------------ menu
    def _menu(self) -> None:
        bar = tk.Menu(self.root)
        f = tk.Menu(bar, tearoff=False)
        f.add_command(label="Settings…", command=self.settings)
        f.add_command(label="Rescan games folder", command=self.rescan)
        f.add_command(label="Open games folder", command=lambda: open_in_file_manager(self.app.paths.originals_dir))
        f.add_command(label="Open data folder", command=lambda: open_in_file_manager(self.app.paths.home))
        f.add_separator()
        f.add_command(label="Quit", command=self.root.destroy)
        bar.add_cascade(label="File", menu=f)
        v = tk.Menu(bar, tearoff=False)
        v.add_command(label="Game capabilities", command=self.capabilities)
        v.add_command(label="Career", command=self.show_career)
        v.add_command(label="System check (doctor)", command=self.doctor)
        bar.add_cascade(label="View", menu=v)
        h = tk.Menu(bar, tearoff=False)
        h.add_command(label="Controls", command=self.controls_help)
        h.add_command(label="About & Licenses", command=self.about)
        bar.add_cascade(label="Help", menu=h)
        self.root.config(menu=bar)

    # ------------------------------------------------------------------ data
    def refresh(self) -> None:
        app = self.app
        app.roms.scan()
        labels, self._game_ids = [], []
        for g in app.games.all():
            roms = app.roms.list(g.game_id)
            state = overall(g)
            suffix = "" if (roms and g.status == "supported") else \
                ("  (no ROM in games folder)" if g.status == "supported" else f"  ({state.lower()})")
            labels.append(f"{g.display_name}{suffix}")
            self._game_ids.append(g.game_id)
        previous = self.game_box.current()
        self.game_box["values"] = labels
        default = app.config.get("default_game")
        if previous >= 0:
            self.game_box.current(previous)          # re-select so the label reflects the new status
        elif default in self._game_ids:
            self.game_box.current(self._game_ids.index(default))
        self.rulesets = app.rulesets.list()
        self.rules_box["values"] = [r.name for r in self.rulesets]
        if not self.rules_var.get():
            ids = [r.id for r in self.rulesets]
            self.rules_box.current(ids.index(app.config.get("default_ruleset"))
                                   if app.config.get("default_ruleset") in ids else 0)
        self._refresh_controller()
        career = app.career()
        current = app.runs.current()
        self.info_var.set(f"Attempts: {career.attempts}      Best run: {career.best_progress}      "
                          f"Current run: {'#%03d' % current.row['attempt_number'] if current and current.row['attempt_number'] else '-'}"
                          f"\nLongest run: {fmt_duration(career.longest_seconds)}      Total play time: "
                          f"{fmt_duration(career.total_seconds)}")
        if current:
            label = f"#{current.row['attempt_number']:03d}" if current.row["attempt_number"] else current.id
            self.continue_btn.configure(text=f"CONTINUE RUN {label}", state="normal")
        else:
            self.continue_btn.configure(text="CONTINUE RUN", state="disabled")
        self.tree.delete(*self.tree.get_children())
        for r in reversed(app.list_runs()):
            s = app.run_summary(r)
            game = app.games.get(r.game_id).display_name
            num = f"#{s['attempt']:03d}" if s["attempt"] else r.id
            dur = "-"
            if s["started_at"]:
                from datetime import datetime, timezone
                end = datetime.fromisoformat(s["ended_at"]) if s["ended_at"] else datetime.now(timezone.utc)
                dur = fmt_duration((end - datetime.fromisoformat(s["started_at"])).total_seconds())
            self.tree.insert("", "end", iid=r.id, values=(num, game, s["status"], dur, s["end_reason"] or "",
                                                          s["integrity"]))
        self._game_changed()

    def _refresh_controller(self) -> None:
        try:
            from .frontend.pads import list_pads
            pads = list_pads(self.app)
        except Exception:  # noqa: BLE001
            pads = [d.name for d in self.app.controllers.devices() if d.is_gamepad]
        self.ctl_var.set(f"{pads[0]}  ✓ CONNECTED" if pads else "No controller detected (keyboard works)")

    def selected_game(self) -> str | None:
        i = self.game_box.current()
        return self._game_ids[i] if i >= 0 else None

    def _game_changed(self) -> None:
        gid = self.selected_game()
        if not gid:
            return
        g = self.app.games.get(gid)
        msg = ""
        if g.status != "supported":
            msg = f"{g.display_name} is DETECTED ONLY: it can be identified but runs are not validated yet."
        elif not self.app.roms.list(gid):
            msg = (f"Put your own {g.display_name} dump into {self.app.paths.originals_dir} "
                   "(any file name) — NFNF IronMON never modifies it.")
        else:
            ok, why = self._randomizer_ready(gid)
            if not ok:
                msg = why
        self.status_var.set(msg)
        self.start_btn.configure(state="disabled" if msg else "normal")

    def _randomizer_ready(self, gid: str) -> tuple[bool, str]:
        try:
            profile = self.app.orchestrator._default_profile_for(gid, self.app.config.get("default_randomizer_profile")) \
                if self.app.config.get("default_randomizer_profile") != "auto" else self.app.orchestrator.auto_profile(gid)
            self.app.profiles.load(profile)
            return True, ""
        except Exception as exc:  # noqa: BLE001
            return False, str(exc)

    # ------------------------------------------------------------------ play
    def _play(self, **kwargs: Any) -> None:
        from .frontend.session import GameSession
        self.root.withdraw()
        try:
            GameSession(self.app).play(**kwargs)
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror(APP_NAME, f"The game window closed with an error:\n{exc}", parent=self.root)
        finally:
            self.root.deiconify()
            self.refresh()

    def start_new_run(self) -> None:
        gid = self.selected_game()
        current = self.app.runs.current()
        if current and current.status == RunState.ACTIVE:
            if not messagebox.askyesno(APP_NAME, f"RUN #{current.row['attempt_number']:03d} is still active.\n\n"
                                       "Starting a new run ABANDONS it. This cannot be undone.\n\nContinue?",
                                       parent=self.root):
                return
            self.app.abandon_run(current.id, "replaced by a new run")
        ruleset = self.rulesets[self.rules_box.current()].id
        self.app.config.set_override(("default_game",), gid)
        self.app.config.set_override(("default_ruleset",), ruleset)
        self._play(new_run={"game_id": gid, "ruleset_id": ruleset})

    def continue_run(self) -> None:
        run = self.app.runs.current()
        if run:
            restore = (run.path / "states" / "recovery.state").exists()
            self._play(run_id=run.id, resume=run.status == RunState.ACTIVE, restore_state=restore)

    def check_recovery(self) -> None:
        for item in find_interrupted(self.app.runs):
            run = item.run
            win = tk.Toplevel(self.root)
            win.title("Recover previous run?")
            win.transient(self.root)
            ttk.Label(win, text="RECOVER PREVIOUS RUN?", font=("TkDefaultFont", 14, "bold")).pack(padx=16, pady=(14, 6))
            game = self.app.games.get(run.game_id).display_name
            s = item.session
            ttk.Label(win, justify="left", text=(
                f"RUN #{run.row['attempt_number'] or 0:03d}\nGame: {game}\nStatus: ACTIVE (session ended unexpectedly)\n"
                f"Last known state: area {s.get('area') or 'UNKNOWN'}, heartbeat {s.get('heartbeat', '?')}\n"
                f"Recovery point: {'available' if item.has_recovery_state else 'none (in-game save only)'}\n\n"
                "The interruption is recorded by the integrity system.")).pack(padx=16)
            b = ttk.Frame(win)
            b.pack(pady=12)

            def resume(run=run, win=win, item=item):
                win.destroy()
                self.app.runs.record_event(run.id, EventType.SESSION_INTERRUPTED,
                                           {"detected": "startup", "last_heartbeat": item.session.get("heartbeat")},
                                           source="session")
                self._play(run_id=run.id, resume=True, restore_state=item.has_recovery_state, after_crash=True)

            def abandon(run=run, win=win, item=item):
                if messagebox.askyesno(APP_NAME, "ABANDON this run? This cannot be undone.", parent=win):
                    self.app.runs.record_event(run.id, EventType.SESSION_INTERRUPTED,
                                               {"detected": "startup"}, source="session")
                    self.app.abandon_run(run.id, "abandoned after an interrupted session")
                    win.destroy()
                    self.refresh()
            ttk.Button(b, text="RESUME", command=resume).pack(side="left", padx=6)
            ttk.Button(b, text="ABANDON", command=abandon).pack(side="left", padx=6)

    # ------------------------------------------------------------------ runs
    def _selected_run(self):
        sel = self.tree.selection()
        return self.app.runs.get(sel[0]) if sel else self.app.runs.latest()

    def view_run(self) -> None:
        run = self._selected_run()
        if not run:
            return
        s = self.app.run_summary(run)
        enc = self.app.db.query("SELECT area, species, first_in_area, duplicate, captured FROM encounters "
                                "WHERE run_id=? AND encounter_type='wild' ORDER BY id", (run.id,))
        deaths = self.app.db.query("SELECT species, level, cause FROM deaths WHERE run_id=?", (run.id,))
        lines = [f"{run.id}  {self.app.games.get(run.game_id).display_name}",
                 f"Status: {s['status']}   Integrity: {s['integrity']}",
                 f"Ruleset: {s['ruleset']}   Randomizer: {s['randomizer']}",
                 f"Requested seed: {s['seed']}   Randomizer seed: {s['actual_randomizer_seed']}",
                 f"Source ROM sha256: {run.row['source_rom_sha256']}",
                 f"Randomized ROM sha256: {s['generated_rom_sha256']}",
                 f"Started: {s['started_at']}   Ended: {s['ended_at']}", f"Reason: {s['end_reason'] or '-'}", "",
                 "ENCOUNTERS (first per area)"]
        lines += [f"  {e['area'] or '?'}: {e['species']}   captured: {'YES' if e['captured'] else 'NO'}"
                  f"{'   (first)' if e['first_in_area'] else ''}{'   (duplicate)' if e['duplicate'] else ''}"
                  for e in enc] or ["  none recorded"]
        lines += ["", "DEATHS"] + [f"  {d['species']} Lv{d['level']} — {d['cause'] or '?'}" for d in deaths] \
            or ["  none"]
        self._text_window(run.id, "\n".join(lines))

    def verify_run(self) -> None:
        run = self._selected_run()
        if run:
            rep = self.app.verify_run(run.id)
            messagebox.showinfo(f"Integrity {run.id}", f"{rep.status.value}\n\n" + "\n".join(
                f"{c.status.value:<10} {c.name}: {c.detail}" for c in rep.checks), parent=self.root)
            self.refresh()

    def open_run_folder(self) -> None:
        run = self._selected_run()
        if run:
            open_in_file_manager(run.path)

    def abandon_selected(self) -> None:
        run = self._selected_run()
        if run and run.status in (RunState.ACTIVE, RunState.READY):
            if messagebox.askyesno(APP_NAME, f"ABANDON {run.id}?\n\nThis cannot be undone.", parent=self.root):
                self.app.abandon_run(run.id, "abandoned by player")
                self.refresh()

    # --------------------------------------------------------------- windows
    def _text_window(self, title: str, text: str) -> None:
        w = tk.Toplevel(self.root)
        w.title(title)
        t = tk.Text(w, width=90, height=30, wrap="word")
        t.insert("1.0", text)
        t.configure(state="disabled")
        t.pack(fill="both", expand=True)

    def capabilities(self) -> None:
        w = tk.Toplevel(self.root)
        w.title("Game capabilities")
        cols = ("game", "overall") + FEATURES
        tv = ttk.Treeview(w, columns=cols, show="headings", height=12)
        for c in cols:
            tv.heading(c, text=c)
            tv.column(c, width=120 if c == "game" else 90)
        for g in self.app.games.all():
            caps = game_capabilities(g)
            tv.insert("", "end", values=(g.display_name, overall(g), *(caps[f] for f in FEATURES)))
        tv.pack(fill="both", expand=True)
        from .capabilities import NOTES
        ttk.Label(w, text="\n".join(f"{k}: {v}" for k, v in NOTES.items()), justify="left",
                  wraplength=900).pack(anchor="w", padx=8, pady=6)

    def show_career(self) -> None:
        self._text_window("IronMON career", self.app.career().render())

    def doctor(self) -> None:
        from .doctor import render
        self._text_window("System check", render(self.app.doctor()))

    def controls_help(self) -> None:
        self._text_window("Controls", "\n".join([
            "Controller (default mapping, change in CONFIGURE):",
            "  D-Pad / left stick = D-Pad   A = A   B = B   LB/LT = L   RB/RT = R",
            "  Start = START   Back/View = SELECT   Guide or Back+Start = NFNF menu",
            "Keyboard:",
            "  Arrows = D-Pad   X = A   Z = B   A = L   S = R   Enter = START   Backspace = SELECT",
            "  Esc = menu   P = pause   F11 / Alt+Enter = fullscreen   F12 = screenshot",
            "  - / + = volume   M = mute   H = run panel   F1 = help"]))

    def about(self) -> None:
        lic = self.app.paths.app_root / "licenses"
        w = tk.Toplevel(self.root)
        w.title("About & Licenses")
        ttk.Label(w, text=f"{APP_NAME} {__version__}", font=("TkDefaultFont", 14, "bold")).pack(anchor="w", padx=10, pady=6)
        ttk.Label(w, text="Local, offline IronMON run manager. Your ROMs and saves never leave this computer.\n"
                          "Third-party components are listed below with their licenses.").pack(anchor="w", padx=10)
        pane = ttk.Frame(w)
        pane.pack(fill="both", expand=True, padx=10, pady=6)
        lb = tk.Listbox(pane, width=36)
        lb.pack(side="left", fill="y")
        txt = tk.Text(pane, width=80, height=28, wrap="word")
        txt.pack(side="left", fill="both", expand=True)
        files = sorted(p for p in lic.glob("*") if p.is_file()) if lic.is_dir() else []
        for p in files:
            lb.insert("end", p.name)

        def show(_e=None):
            sel = lb.curselection()
            if sel:
                txt.delete("1.0", "end")
                txt.insert("1.0", files[sel[0]].read_text(encoding="utf-8", errors="replace"))
        lb.bind("<<ListboxSelect>>", show)
        if files:
            lb.selection_set(0)
            show()
        else:
            txt.insert("1.0", "licenses/ folder not found next to the application.")

    def rescan(self) -> None:
        res = self.app.roms.scan()
        messagebox.showinfo(APP_NAME, f"New games found: {len(res.registered)}\n"
                            + "\n".join(f"• {r.metadata['game']} ({r.version})" for r in res.registered)
                            + ("\nProblems:\n" + "\n".join(res.problems) if res.problems else ""), parent=self.root)
        self.refresh()

    # ----------------------------------------------------------- controller
    def test_controller(self) -> None:
        from .frontend import pads
        w = tk.Toplevel(self.root)
        w.title("Test controller")
        var = tk.StringVar(value="Press buttons on your controller…")
        ttk.Label(w, textvariable=var, font=("TkFixedFont", 12), justify="left").pack(padx=16, pady=16)
        state = {"n": 0}

        def tick():
            if not w.winfo_exists():
                return
            try:
                phys, logical, name = pads.poll(self.app)
                var.set(f"Controller: {name or 'none detected'}\n\nPhysical: {' '.join(sorted(phys)) or '-'}\n"
                        f"Game gets: {' '.join(sorted(logical)) or '-'}\n\nMapping: {self.app.controllers.mapping_id}")
            except Exception as exc:  # noqa: BLE001
                var.set(f"Controller backend unavailable: {exc}")
            state["n"] += 1
            if state["n"] < 1800:
                w.after(33, tick)
        tick()

    def configure_controller(self) -> None:
        app = self.app
        w = tk.Toplevel(self.root)
        w.title("Configure controller")
        ttk.Label(w, text="Mapping preset:").grid(row=0, column=0, sticky="w", padx=8, pady=6)
        ids = [m.id for m in app.controllers.mappings.list()]
        preset = tk.StringVar(value=app.controllers.mapping_id)
        box = ttk.Combobox(w, textvariable=preset, values=ids, state="readonly")
        box.grid(row=0, column=1, sticky="ew", padx=8)
        ttk.Label(w, text="Game button  ←  controller button (remap)").grid(row=1, column=0, columnspan=2, pady=(8, 2))
        rows: dict[str, tk.StringVar] = {}

        def load(_e=None):
            m = app.controllers.mappings.load(preset.get())
            for logical, var in rows.items():
                phys = [p for p, ls in m.buttons.items() if logical in ls]
                var.set(phys[0] if phys else "(none)")
        for i, logical in enumerate(LOGICAL_BUTTONS):
            ttk.Label(w, text=logical).grid(row=2 + i, column=0, sticky="w", padx=8)
            var = tk.StringVar()
            ttk.Combobox(w, textvariable=var, values=("(none)",) + PHYSICAL_BUTTONS,
                         state="readonly", width=14).grid(row=2 + i, column=1, sticky="w", padx=8)
            rows[logical] = var
        box.bind("<<ComboboxSelected>>", load)
        load()

        def save():
            base = app.controllers.mappings.load(preset.get())
            buttons: dict[str, list[str]] = {}
            for logical, var in rows.items():
                if var.get() != "(none)":
                    buttons.setdefault(var.get(), []).append(logical)
            data = {"id": "custom", "name": f"Custom (based on {base.id})", "buttons": buttons,
                    "axes": base.axes, "deadzone": base.deadzone}
            (app.paths.input_mappings_dir / "custom.json").write_text(json.dumps(data, indent=2))
            app.controllers.mapping_id = "custom"
            app.config.set_override(("controller", "mapping"), "custom")
            w.destroy()
            self.refresh()

        def use_preset():
            app.controllers.mapping_id = preset.get()
            app.config.set_override(("controller", "mapping"), preset.get())
            w.destroy()

        b = ttk.Frame(w)
        b.grid(row=3 + len(LOGICAL_BUTTONS), column=0, columnspan=2, pady=10)
        ttk.Button(b, text="USE PRESET", command=use_preset).pack(side="left", padx=4)
        ttk.Button(b, text="SAVE AS CUSTOM MAPPING", command=save).pack(side="left", padx=4)

    # --------------------------------------------------------------- settings
    def settings(self) -> None:
        app, cfg = self.app, self.app.config
        w = tk.Toplevel(self.root)
        w.title("Settings")
        nb = ttk.Notebook(w)
        nb.pack(fill="both", expand=True, padx=8, pady=8)
        sess = cfg.section("session")
        values: dict[tuple, tk.Variable] = {}

        def tab(name):
            f = ttk.Frame(nb, padding=10)
            nb.add(f, text=name)
            return f

        def check(parent, label, keys, default):
            var = tk.BooleanVar(value=bool(_get(cfg.data, keys, default)))
            ttk.Checkbutton(parent, text=label, variable=var).pack(anchor="w", pady=2)
            values[keys] = var

        def combo(parent, label, keys, options, default):
            ttk.Label(parent, text=label).pack(anchor="w")
            var = tk.StringVar(value=str(_get(cfg.data, keys, default)))
            ttk.Combobox(parent, textvariable=var, values=options, state="readonly").pack(anchor="w", pady=(0, 6))
            values[keys] = var

        def scale(parent, label, keys, lo, hi, default):
            ttk.Label(parent, text=label).pack(anchor="w")
            var = tk.IntVar(value=int(_get(cfg.data, keys, default)))
            tk.Scale(parent, from_=lo, to=hi, orient="horizontal", variable=var, length=260).pack(anchor="w")
            values[keys] = var

        g = tab("General")
        check(g, "Automatically start a new run after a failure", ("session", "auto_new_run"), False)
        ttk.Label(g, text="Works offline. No account, no cloud, no telemetry.").pack(anchor="w", pady=8)
        t = tab("Games")
        ttk.Label(t, text="Your games (read in place, never modified):").pack(anchor="w")
        for r in app.roms.list():
            ttk.Label(t, text=f"• {r.metadata['game']} — {r.version} — {r.path.name}").pack(anchor="w")
        ttk.Button(t, text="Open games folder", command=lambda: open_in_file_manager(app.paths.originals_dir)).pack(anchor="w", pady=6)
        t = tab("Randomizer")
        combo(t, "Default randomizer profile", ("default_randomizer_profile",),
              ["auto"] + [p.id for p in app.profiles.list()], "auto")
        upr = app.randomizers.get("upr-zx")
        ttk.Label(t, text=f"UPR ZX {upr.info().version} — {upr.is_available()[1]}").pack(anchor="w")
        t = tab("Rules")
        combo(t, "Default ruleset", ("default_ruleset",), [r.id for r in app.rulesets.list()], "standard-ironmon")
        ttk.Button(t, text="Open rules folder (JSON)", command=lambda: open_in_file_manager(app.paths.rules_dir)).pack(anchor="w")
        t = tab("Controller")
        combo(t, "Mapping", ("controller", "mapping"), [m.id for m in app.controllers.mappings.list()], "xbox-gba-labels")
        ttk.Button(t, text="Configure / remap…", command=self.configure_controller).pack(anchor="w")
        t = tab("Video")
        check(t, "Fullscreen (borderless)", ("session", "fullscreen"), False)
        check(t, "Integer scaling (sharp pixels)", ("session", "integer_scaling"), True)
        check(t, "Show run panel under the game", ("session", "show_hud"), True)
        t = tab("Audio")
        scale(t, "Volume (%)", ("session", "volume"), 0, 100, sess.get("volume", 70))
        check(t, "Mute", ("session", "muted"), False)
        t = tab("Emulator")
        integ = app.emulators["nfnf-libretro"]
        ttk.Label(t, text=f"Integrated core: {integ.core_path() or 'MISSING'}").pack(anchor="w")
        scale(t, "Tracker polling interval (frames)", ("session", "tracker_poll_frames"), 5, 60, 15)
        t = tab("Integrity")
        scale(t, "Crash-recovery point every (seconds)", ("session", "recovery_seconds"), 15, 600, 60)
        ttk.Label(t, text="Resets, save loads, state restores and interrupted sessions are always recorded.").pack(anchor="w")
        t = tab("Storage")
        ttk.Label(t, text=f"Data folder: {app.paths.home}").pack(anchor="w")
        ttk.Label(t, text="Set NFNF_IRONMON_HOME or start with --home to keep data elsewhere.").pack(anchor="w")
        ttk.Button(t, text="Open data folder", command=lambda: open_in_file_manager(app.paths.home)).pack(anchor="w", pady=6)

        def save():
            for keys, var in values.items():
                cfg.set_override(keys, var.get())
            app.controllers.mapping_id = cfg.section("controller").get("mapping") or app.controllers.mapping_id
            w.destroy()
            self.refresh()
        ttk.Button(w, text="SAVE", command=save).pack(pady=6)


def _get(data: dict, keys: tuple, default):
    node = data
    for k in keys:
        if not isinstance(node, dict) or k not in node:
            return default
        node = node[k]
    return default if node is None else node


# kept for compatibility with earlier code/tests
MainWindow = Launcher


def launch_ui(app: Application) -> None:
    root = tk.Tk()
    Launcher(root, app)
    root.mainloop()


__all__ = ["Launcher", "MainWindow", "launch_ui", "dumps"]
