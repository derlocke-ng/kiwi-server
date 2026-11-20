"""Work in progress (ui-rules 10): the filling kiwi, the job strip and the
output pane.

    jobs = JobArea(store)                 # append it at the bottom of the window
                                          # (JobArea() for an app that keeps its settings itself)
    header.pack_end(jobs.output_button)   # opens and closes the output
    jobs.run("Build the image", ["podman", "build", "."],
             steps=4, is_step=lambda line: line.startswith("STEP "),
             on_done=lambda rc: ...)

Or drive it yourself, for work that is not one command:

    jobs.start("Start 3 boxes", steps=3)
    jobs.log("starting work…\n"); jobs.step()
    jobs.finish(ok=True)

One job at a time: `jobs.busy` says whether one runs; queueing is the app's.
"""

from __future__ import annotations

import math
import shlex
import subprocess
import threading

from gi.repository import GLib, GObject, Gtk, Pango

from .settings import Store


class KiwiProgress(Gtk.DrawingArea):
    """A kiwi slice that fills clockwise as a job gets on.

    The kiwi of the app icon. What is still to do is a pale outline; what is
    done is the fruit. With no count to go by (fraction None) a wedge of fruit
    goes round instead. A finished kiwi gives one small bounce."""

    SKIN, FLESH, SEED, CORE = "#4B781A", "#9CD06E", "#203C00", "#EEFAE5"

    def __init__(self, size=40):
        super().__init__(content_width=size, content_height=size, valign=Gtk.Align.CENTER)
        self.target = None  # 0..1, or None: no count, go round
        self.shown = 0.0  # what is drawn; eases towards target
        self._t0 = None  # frame time of the last tick
        self._spin = 0.0
        self._pop = 0.0  # 1 -> 0 after reaching full
        self._tick = None
        self.set_draw_func(self._draw)
        # hidden in a Revealer until the first job: start moving when shown
        self.connect("map", lambda *_: self.set_fraction(self.target))

    def set_fraction(self, f):
        """f in 0..1, or None when there is nothing to count."""
        if f is not None:
            f = max(0.0, min(1.0, f))
            if f < self.shown:
                self.shown = f  # a new job starts from its own zero
        self.target = f
        if self._tick is None and self.get_mapped():
            self._t0 = None
            self._tick = self.add_tick_callback(self._on_tick)
        self.queue_draw()

    def _on_tick(self, _w, clock):
        now = clock.get_frame_time() / 1e6
        dt = 0.0 if self._t0 is None else min(now - self._t0, 0.1)
        self._t0 = now
        moving = True
        if self.target is None:
            self._spin = (self._spin + dt * 0.9) % 1.0
        else:
            before = self.shown
            self.shown += (self.target - self.shown) * min(1.0, dt * 6)
            if abs(self.target - self.shown) < 0.002:
                self.shown = self.target
            if before < 1.0 <= self.shown:
                self._pop = 1.0
            self._pop = max(0.0, self._pop - dt * 2.5)
            moving = self.shown != self.target or self._pop > 0
        self.queue_draw()
        if not moving:
            self._tick = None
            return False
        return True

    @staticmethod
    def _rgb(cr, hexcol, alpha=1.0):
        r, g, b = (int(hexcol[i : i + 2], 16) / 255 for i in (1, 3, 5))
        cr.set_source_rgba(r, g, b, alpha)

    def _fruit(self, cr, alpha):
        """The icon's kiwi, in its own 128-unit square."""
        for col, r in ((self.SKIN, 58), (self.FLESH, 51)):
            self._rgb(cr, col, alpha)
            cr.arc(64, 64, r, 0, 2 * math.pi)
            cr.fill()
        self._rgb(cr, self.SEED, alpha)
        for k in range(6):
            a = k * math.pi / 3
            cr.save()
            cr.translate(64 + 31 * math.sin(a), 64 - 31 * math.cos(a))
            cr.rotate(a)
            cr.scale(4, 5.6)
            cr.arc(0, 0, 1, 0, 2 * math.pi)
            cr.restore()
            cr.fill()
        self._rgb(cr, self.CORE, alpha)
        cr.arc(64, 64, 21, 0, 2 * math.pi)
        cr.fill()

    def _draw(self, _area, cr, w, h):
        side = min(w, h)
        bounce = 1 + 0.12 * math.sin(math.pi * self._pop) if self._pop else 1
        cr.translate(w / 2, h / 2)
        cr.scale(side / 128 * bounce * 0.94, side / 128 * bounce * 0.94)
        cr.translate(-64, -64)
        self._fruit(cr, 0.22)  # still to do
        if self.target is None:
            start, sweep = self._spin * 2 * math.pi, 0.3 * 2 * math.pi
        else:
            start, sweep = 0.0, self.shown * 2 * math.pi
        if sweep <= 0:
            return
        if sweep < 2 * math.pi:  # a wedge, from 12 o'clock
            cr.move_to(64, 64)
            cr.arc(64, 64, 70, start - math.pi / 2, start + sweep - math.pi / 2)
            cr.close_path()
            cr.clip()
        self._fruit(cr, 1.0)  # done


class JobArea(Gtk.Box):
    """The job strip above the output pane. Hidden until a job starts."""

    def __init__(self, store: Store | None = None) -> None:
        """store: the kit's settings, for "open the output when a job starts"
        (kit.show_output). Without one the output stays as the user left it;
        an app that is not on the kit's settings yet can take the strip so."""
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.store = store
        self.busy = False
        self._steps = 0
        self._done = 0
        self._hide = None

        self.kiwi = KiwiProgress()
        self.title = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END)
        self.title.add_css_class("heading")
        self.sub = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END)
        self.sub.add_css_class("dim-label")
        self.sub.add_css_class("caption")
        text = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=2, valign=Gtk.Align.CENTER, hexpand=True
        )
        text.append(self.title)
        text.append(self.sub)
        strip = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=12,
            margin_top=8,
            margin_bottom=8,
            margin_start=14,
            margin_end=14,
        )
        strip.append(self.kiwi)
        strip.append(text)
        self._strip = Gtk.Revealer(child=strip, transition_type=Gtk.RevealerTransitionType.SLIDE_UP)
        self.append(Gtk.Separator())
        self.append(self._strip)

        # the command's own output, as it is, in a terminal-style pane
        self.buffer = Gtk.TextBuffer()
        self.view = Gtk.TextView(
            buffer=self.buffer, editable=False, monospace=True, top_margin=6, left_margin=6
        )
        scroll = Gtk.ScrolledWindow(child=self.view)
        scroll.set_size_request(-1, 190)
        self._pane = Gtk.Revealer(child=scroll, transition_type=Gtk.RevealerTransitionType.SLIDE_UP)
        self.append(self._pane)
        self.output_button = Gtk.ToggleButton(
            icon_name="utilities-terminal-symbolic", tooltip_text="Show Output"
        )
        self.output_button.bind_property(
            "active", self._pane, "reveal-child", GObject.BindingFlags.BIDIRECTIONAL
        )

    # ── driving a job ─────────────────────────────────────────────────────
    def start(self, label: str, steps: int = 0) -> None:
        """steps: how many `step()` calls make the job; 0 when there is
        nothing to count (the kiwi goes round)."""
        if self._hide is not None:
            GLib.source_remove(self._hide)
            self._hide = None
        self.busy = True
        self._steps, self._done = max(0, steps), 0
        self.title.set_label(label)
        self._strip.set_reveal_child(True)
        if self.store is not None and self.store["kit.show_output"]:
            self.output_button.set_active(True)
        self._show()

    def step(self) -> None:
        if self._done < self._steps:
            self._done += 1
            self._show()

    def _show(self) -> None:
        if not self._steps:
            self.sub.set_label("working…")
            self.kiwi.set_fraction(None)
            return
        # never full before the job has ended: the last step is the end itself
        self.kiwi.set_fraction(self._done / (self._steps + 1))
        self.sub.set_label(f"{self._done} of {self._steps} done")

    def log(self, text: str) -> None:
        self.buffer.insert(self.buffer.get_end_iter(), text)
        mark = self.buffer.create_mark(None, self.buffer.get_end_iter(), False)
        self.view.scroll_mark_onscreen(mark)
        self.buffer.delete_mark(mark)

    def finish(self, ok: bool = True) -> None:
        self.busy = False
        self.kiwi.set_fraction(1.0)
        self.sub.set_label("Done" if ok else "Failed — see the output")
        if not ok:  # also with "show output" off
            self.output_button.set_active(True)

        def fold() -> bool:
            self._hide = None
            if not self.busy:
                self._strip.set_reveal_child(False)
            return False

        self._hide = GLib.timeout_add(1800 if ok else 6000, fold)

    # ── one command as a job ──────────────────────────────────────────────
    def run(self, label: str, argv: list[str], *, steps: int = 0, is_step=None, on_done=None):
        """Run argv with its output streamed into the pane. is_step(line)
        says whether a line of output finishes a step. on_done(returncode)
        is called on the main loop. stdin is closed, so a command that needs
        root asks through pkexec's dialog, not in a terminal nobody sees."""
        self.start(label, steps)
        self.log("\n$ " + " ".join(shlex.quote(a) for a in argv) + "\n")

        def line(text: str) -> bool:
            self.log(text)
            if is_step and is_step(text.rstrip("\n")):
                self.step()
            return False

        def done(rc: int) -> bool:
            self.finish(rc == 0)
            if on_done:
                on_done(rc)
            return False

        def worker() -> None:
            rc = 1
            try:
                proc = subprocess.Popen(
                    argv,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    stdin=subprocess.DEVNULL,
                )
                for out in proc.stdout:
                    GLib.idle_add(line, out)
                rc = proc.wait()
            except OSError as exc:
                GLib.idle_add(line, f"{exc}\n")
            GLib.idle_add(done, rc)

        threading.Thread(target=worker, daemon=True).start()
