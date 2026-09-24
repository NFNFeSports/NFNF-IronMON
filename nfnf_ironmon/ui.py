"""Minimal Tkinter UI (functionality first, no polish)."""

from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, simpledialog, ttk

from . import APP_NAME, __version__
from .app import Application, dumps
from .platform_support import open_in_file_manager


class MainWindow:
    def __init__(self, root: tk.Tk, app: Application):
        self.root, self.app = root, app
        root.title(f"{APP_NAME} {__version__}")
        root.geometry("900x620")

        ttk.Label(root, text=APP_NAME, font=("TkDefaultFont", 16, "bold")).pack(anchor="w", padx=10, pady=(10, 0))
        body = ttk.Frame(root)
        body.pack(fill="both", expand=True, padx=10, pady=10)

        left = ttk.Frame(body)
        left.pack(side="left", fill="both", expand=True)
        ttk.Label(left, text="Games").pack(anchor="w")
        self.games = tk.Listbox(left, height=6, exportselection=False)
        self.games.pack(fill="x")
        ttk.Label(left, text="Runs").pack(anchor="w", pady=(10, 0))
        self.runs = tk.Listbox(left, height=12, exportselection=False)
        self.runs.pack(fill="both", expand=True)
        self.runs.bind("<<ListboxSelect>>", lambda e: self.show_selected())

        right = ttk.Frame(body)
        right.pack(side="left", fill="both", expand=True, padx=(10, 0))
        ttk.Label(right, text="Current Run").pack(anchor="w")
        self.current = tk.Text(right, height=12, width=40, state="disabled")
        self.current.pack(fill="both", expand=True)

        buttons = ttk.Frame(right)
        buttons.pack(fill="x", pady=(10, 0))
        for text, cmd in (("NEW RUN", self.new_run), ("OPEN RUN", self.open_run),
                          ("FAIL RUN", self.fail_run), ("VERIFY", self.verify_run),
                          ("SETTINGS", self.settings)):
            ttk.Button(buttons, text=text, command=cmd).pack(side="left", padx=2)

        side = ttk.Frame(body)
        side.pack(side="left", fill="both", padx=(10, 0))
        ttk.Label(side, text="CONTROLLER").pack(anchor="w")
        self.controller = tk.Text(side, height=14, width=34, state="disabled")
        self.controller.pack(fill="x")
        cbuttons = ttk.Frame(side)
        cbuttons.pack(fill="x", pady=(4, 10))
        ttk.Button(cbuttons, text="TEST CONTROLLER", command=self.test_controller).pack(side="left", padx=2)
        ttk.Button(cbuttons, text="CONFIGURE", command=self.configure_controller).pack(side="left", padx=2)
        ttk.Label(side, text="IRONMON CAREER").pack(anchor="w")
        self.career = tk.Text(side, height=10, width=34, state="disabled")
        self.career.pack(fill="x")
        self._testing_until = 0.0
        self.refresh()

    # --- data -------------------------------------------------------------
    def refresh(self) -> None:
        self.games.delete(0, "end")
        for g in self.app.list_games():
            note = "" if g["status"] == "supported" else "  (planned)"
            self.games.insert("end", f"{g['name']}{note}")
        self.runs.delete(0, "end")
        self._run_ids = []
        for r in reversed(self.app.list_runs()):
            self.runs.insert("end", f"{r.id}  {r.status.value}")
            self._run_ids.append(r.id)
        run = self.app.current_run()
        self._show(run)
        self._show_controller()
        self._set_text(self.career, self.app.career().render().replace("IRONMON CAREER\n\n", ""))

    @staticmethod
    def _set_text(widget: tk.Text, text: str) -> None:
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("1.0", text)
        widget.configure(state="disabled")

    def _show_controller(self, live: str = "") -> None:
        cm = self.app.controllers
        try:
            dev = cm.select_device()
        except Exception as exc:  # noqa: BLE001
            dev, err = None, str(exc)
        else:
            err = ""
        lines = [f"Device:  {dev.name if dev else 'none detected'}",
                 f"Status:  {'Connected' if dev and dev.connected else 'Not connected'}" + (f" ({err})" if err else ""),
                 "", f"Mapping: {cm.mapping_id}"]
        try:
            lines += [f"  {p:<10} → {l}" for p, l in cm.mapping().describe()]
        except Exception as exc:  # noqa: BLE001
            lines.append(f"  (mapping error: {exc})")
        if live:
            lines += ["", f"Pressed: {live}"]
        self._set_text(self.controller, "\n".join(lines))

    def _show(self, run) -> None:
        if run is None:
            text = "No runs yet.\nPress NEW RUN."
        else:
            game = self.app.games.get(run.game_id).display_name
            text = (f"Run: {run.id}\nGame: {game}\nSeed: {run.row['seed']}\n"
                    f"Status: {run.status.value}\nIntegrity: {run.integrity_status}\n"
                    f"Ruleset: {run.row['ruleset_id']}\nRandomizer: {run.row['randomizer_name']}\n"
                    f"Emulator: {run.row['emulator_id']}   Tracker: {run.row['tracker_id']}\n"
                    f"Started: {run.row['started_at'] or '-'}\nEnded: {run.row['ended_at'] or '-'}\n"
                    f"Reason: {run.row['end_reason'] or '-'}")
        self.current.configure(state="normal")
        self.current.delete("1.0", "end")
        self.current.insert("1.0", text)
        self.current.configure(state="disabled")

    def selected_run_id(self) -> str | None:
        sel = self.runs.curselection()
        if sel:
            return self._run_ids[sel[0]]
        run = self.app.current_run()
        return run.id if run else None

    def show_selected(self) -> None:
        rid = self.selected_run_id()
        if rid:
            self._show(self.app.runs.get(rid))

    # --- actions ----------------------------------------------------------
    def new_run(self) -> None:
        supported = [g["id"] for g in self.app.list_games() if g["status"] == "supported"]
        game = simpledialog.askstring("New run", f"Game ({', '.join(supported)}):",
                                      initialvalue=self.app.config.get("default_game"), parent=self.root)
        if not game:
            return
        ruleset = simpledialog.askstring(
            "New run", "Ruleset (" + ", ".join(r.id for r in self.app.rulesets.list()) + "):",
            initialvalue=self.app.config.get("default_ruleset"), parent=self.root)
        if not ruleset:
            return
        try:
            run = self.app.new_run(game_id=game.strip(), ruleset_id=ruleset.strip())
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("New run failed", str(exc), parent=self.root)
        else:
            messagebox.showinfo("New run", f"{run.id} is {run.status.value} (seed {run.row['seed']})",
                                parent=self.root)
        self.refresh()

    def open_run(self) -> None:
        rid = self.selected_run_id()
        if not rid:
            return
        run = self.app.runs.get(rid)
        win = tk.Toplevel(self.root)
        win.title(rid)
        text = tk.Text(win, width=90, height=30)
        text.pack(fill="both", expand=True)
        events = self.app.runs.events(rid)
        text.insert("1.0", dumps(self.app.runs.metadata(rid)) + "\n\nEvents:\n" +
                    "\n".join(f"{e['seq']:>4} {e['timestamp']} {e['type']:<18} {e['payload']}" for e in events))
        ttk.Button(win, text="Open folder", command=lambda: open_in_file_manager(run.path)).pack()

    def fail_run(self) -> None:
        rid = self.selected_run_id()
        if not rid or not messagebox.askyesno("Fail run", f"Mark {rid} as FAILED and archive it?", parent=self.root):
            return
        try:
            self.app.fail_run(rid)
            if messagebox.askyesno("Fail run", "Start a new run with the same game and ruleset?", parent=self.root):
                self.app.restart_run(rid)
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("Error", str(exc), parent=self.root)
        self.refresh()

    def verify_run(self) -> None:
        rid = self.selected_run_id()
        if rid:
            report = self.app.verify_run(rid)
            lines = [f"{c.status.value:<10} {c.name}: {c.detail}" for c in report.checks]
            messagebox.showinfo(f"Integrity {rid}", f"{report.status.value}\n\n" + "\n".join(lines),
                                parent=self.root)
            self.refresh()

    def test_controller(self) -> None:
        import time
        if self.app.controllers.open() is None:
            messagebox.showinfo("Controller", "No gamepad detected. Connect an Xbox/XInput controller.",
                                parent=self.root)
            return
        self._testing_until = time.monotonic() + 10
        self._poll_controller()

    def _poll_controller(self) -> None:
        import time
        pressed = self.app.controllers.poll_logical()
        self._show_controller(" ".join(sorted(pressed)) or "-")
        if time.monotonic() < self._testing_until:
            self.root.after(33, self._poll_controller)
        else:
            self.app.controllers.close()
            self._show_controller()

    def configure_controller(self) -> None:
        ids = [m.id for m in self.app.controllers.mappings.list()]
        choice = simpledialog.askstring("Controller mapping", "Mapping (" + ", ".join(ids) + "):",
                                        initialvalue=self.app.controllers.mapping_id, parent=self.root)
        if choice and choice.strip() in ids:
            self.app.controllers.mapping_id = choice.strip()
            self.app.config.set_override(("controller", "mapping"), choice.strip())
            self._show_controller()
        elif choice:
            messagebox.showerror("Controller mapping", f"Unknown mapping {choice!r}", parent=self.root)

    def settings(self) -> None:
        d = self.app.doctor()
        lines = [f"Home: {d['home']}", f"Config: {self.app.paths.config_file}", ""]
        lines += [f"{e['emulator']}: {'found' if e['found'] else 'not found'}" for e in d["emulators"]]
        lines += [f"{t['tracker']}: {'found' if t['found'] else 'not found'}" for t in d["trackers"]]
        lines += [f"{r['id']}: {'available' if r['available'] else r['detail']}" for r in d["randomizers"]]
        lines += ["", "Edit config/settings.json to configure paths.", "Open the config folder?"]
        if messagebox.askyesno("Settings", "\n".join(lines), parent=self.root):
            open_in_file_manager(self.app.paths.config_dir)


def launch_ui(app: Application) -> None:
    root = tk.Tk()
    MainWindow(root, app)
    root.mainloop()
