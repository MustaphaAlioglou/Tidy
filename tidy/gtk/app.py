import os
import sys
import threading
import time

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, GObject, Gtk  # noqa: E402

ADW_MIN = (1, 5)
if (Adw.get_major_version(), Adw.get_minor_version()) < ADW_MIN:
    raise ImportError(f"libadwaita {'.'.join(map(str, ADW_MIN))}+ needed, found "
                      f"{Adw.get_major_version()}.{Adw.get_minor_version()}")

from ..core import (APP_ID, HOLD, HOLD_DAYS, MOVE, VERSION, Cancelled, History, Protected,  # noqa: E402
                    check_folder, make_plan, places)
from ..core import fmt  # noqa: E402

size = GLib.format_size


def run_async(fn, done, error=None):
    def work():
        try:
            result = fn()
        except Exception as e:
            if error:
                GLib.idle_add(error, e)
            else:
                GLib.idle_add(sys.excepthook, type(e), e, e.__traceback__)
        else:
            GLib.idle_add(done, result)
    threading.Thread(target=work, daemon=True).start()


def tilde(path):
    home = os.path.expanduser("~")
    return "~" + path[len(home):] if path.startswith(home + os.sep) else path


def sym(*names):
    theme = Gtk.IconTheme.get_for_display(Gdk.Display.get_default())
    return next((n for n in names if theme.has_icon(n)), names[-1])


def file_icon(path, is_dir=False):
    if is_dir:
        return Gio.ThemedIcon.new(sym("folder-symbolic", "folder"))
    kind = Gio.content_type_guess(path, None)[0]
    names = [*Gio.content_type_get_symbolic_icon(kind).get_names(), *Gio.content_type_get_icon(kind).get_names()]
    return Gio.ThemedIcon.new(sym(*names, "text-x-generic-symbolic"))


def spinner():
    if hasattr(Adw, "Spinner"):
        return Adw.Spinner(width_request=48, height_request=48)
    s = Gtk.Spinner(width_request=48, height_request=48)
    s.start()
    return s


def show_in_folder(path, parent):
    Gtk.FileLauncher(file=Gio.File.new_for_path(path)).open_containing_folder(parent, None, None)


def page(title, child, tag=None, end=(), bottom=None):
    header = Adw.HeaderBar()
    for widget in end:
        header.pack_end(widget)
    view = Adw.ToolbarView(content=child)
    view.add_top_bar(header)
    if bottom:
        view.add_bottom_bar(bottom)
    return Adw.NavigationPage(title=title, child=view, tag=tag or title)


def boxed_rows(title, rows, description=None):
    group = Adw.PreferencesGroup(title=title, description=description)
    for row in rows:
        group.add(row)
    return group


def plain_row(title, subtitle=""):
    return Adw.ActionRow(title=title, subtitle=subtitle, use_markup=False, title_lines=1, subtitle_lines=2)


class Window(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="Tidy", default_width=760, default_height=680)
        self.history = app.history
        self.cancel = None
        self.toasts = Adw.ToastOverlay()
        self.nav = Adw.NavigationView()
        self.toasts.set_child(self.nav)
        self.set_content(self.toasts)
        self.start = self._start_page()
        self.nav.add(self.start)

    def toast(self, text, button=None, action=None):
        t = Adw.Toast(title=text, timeout=6)
        if button:
            t.set_button_label(button)
            t.connect("button-clicked", lambda *_: action())
        self.toasts.add_toast(t)

    def alert(self, heading, body):
        d = Adw.AlertDialog(heading=heading, body=body)
        d.add_response("ok", "OK")
        d.present(self)

    def _start_page(self):
        rows = []
        for label, path, icon in places():
            row = Adw.ActionRow(title=label, subtitle=tilde(path), activatable=True)
            row.add_prefix(Gtk.Image.new_from_icon_name(sym(f"{icon}-symbolic", "folder-symbolic")))
            row.add_suffix(Gtk.Image.new_from_icon_name("go-next-symbolic"))
            row.connect("activated", lambda _r, p=path: self.scan(p))
            rows.append(row)
        other = Adw.ActionRow(title="Other Folder…", activatable=True)
        other.add_prefix(Gtk.Image.new_from_icon_name(sym("folder-open-symbolic", "document-open-folder-symbolic", "folder-symbolic")))
        other.add_suffix(Gtk.Image.new_from_icon_name("go-next-symbolic"))
        other.connect("activated", lambda *_: self.choose_folder())
        rows.append(other)

        status = Adw.StatusPage(
            icon_name="edit-clear-all-symbolic", title="Tidy Up a Folder",
            description="You get a plan first. Nothing moves until you approve it, and every change can be undone.",
            child=Adw.Clamp(maximum_size=420, child=boxed_rows(None, rows)))

        menu = Gio.Menu()
        menu.append("_About Tidy", "app.about")
        menu_button = Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=menu, primary=True,
                                     tooltip_text="Main Menu")
        history = Gtk.Button(icon_name="document-open-recent-symbolic", tooltip_text="Tidy History")
        history.connect("clicked", lambda *_: HistoryDialog(self).present(self))
        return page("Tidy", status, "start", end=(menu_button, history))

    def choose_folder(self):
        dialog = Gtk.FileDialog(title="Choose a Folder to Tidy", modal=True)

        def picked(d, res):
            try:
                folder = d.select_folder_finish(res)
            except GLib.Error:
                return
            if folder and folder.get_path():
                self.scan(folder.get_path())
        dialog.select_folder(self, None, picked)

    def scan(self, path):
        if self.cancel:
            self.cancel.set()
        cancel = self.cancel = threading.Event()
        name = os.path.basename(path.rstrip(os.sep)) or path
        status = Adw.StatusPage(title=f"Scanning {name}…", description="Looking at file types, ages and sizes")
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=24, halign=Gtk.Align.CENTER)
        box.append(spinner())
        stop = Gtk.Button(label="Cancel", css_classes=["pill"])
        stop.connect("clicked", lambda *_: self.nav.pop())
        box.append(stop)
        status.set_child(box)
        scanning = page(name, status, "scanning")
        scanning.connect("hidden", lambda *_: cancel.set())
        self.nav.push(scanning)

        def progress(n):
            GLib.idle_add(status.set_description, f"Looked at {n:,} files")

        def done(plan):
            if self.nav.get_visible_page() is scanning:
                self.nav.replace([self.start, PlanPage(self, plan)])

        def failed(e):
            if isinstance(e, Cancelled):
                return
            if self.nav.get_visible_page() is scanning:
                self.nav.pop()
            if isinstance(e, Protected):
                self.alert("Tidy Leaves This Folder Alone", str(e))
            else:
                self.alert("Could Not Scan", str(e))

        run_async(lambda: make_plan(path, cancel, progress, history=self.history), done, failed)

    def apply(self, plan):
        status = Adw.StatusPage(title="Tidying…", child=spinner())
        working = page(os.path.basename(plan.folder), status, "working")
        working.set_can_pop(False)
        self.nav.push(working)

        def progress(n, total):
            GLib.idle_add(status.set_description, f"{n} of {total}")

        run_async(lambda: self.history.apply(plan, progress),
                  lambda res: self.nav.replace([self.start, DonePage(self, plan, res)]),
                  lambda e: (self.nav.pop(), self.alert("Could Not Tidy", str(e))))

    def undo(self, run):
        def done(res):
            self.nav.pop_to_tag("start")
            msg = f"Put back {fmt.items(res.done)}"
            if res.failed:
                msg += f", {len(res.failed)} could not be restored"
            self.toast(msg)
        run_async(lambda: self.history.undo(run), done, lambda e: self.alert("Could Not Undo", str(e)))


class PlanPage(Adw.NavigationPage):
    def __init__(self, win, plan):
        super().__init__(title=os.path.basename(plan.folder), tag="plan")
        self.win = win
        self.plan = plan
        self.items = {}
        self.expanded = set()
        header = Adw.HeaderBar()
        view = Adw.ToolbarView()
        view.add_top_bar(header)
        self.set_child(view)

        if not plan.groups:
            header.set_title_widget(Adw.WindowTitle(title=self.get_title(), subtitle=tilde(plan.folder)))
            view.set_content(Adw.StatusPage(
                icon_name="emblem-ok-symbolic", title="Already Tidy",
                description=f"Looked at {plan.scanned:,} files ({size(plan.total_size)}). Nothing needs tidying."))
            return

        actions = Gio.SimpleActionGroup()
        reveal = Gio.SimpleAction.new("reveal", GLib.VariantType.new("s"))
        reveal.connect("activate", lambda _a, v: show_in_folder(self.items[v.get_string()].path, win))
        actions.add_action(reveal)
        move = Gio.SimpleAction.new("move", GLib.VariantType.new("s"))
        move.connect("activate", lambda _a, v: self.move(*v.get_string().split("|", 1)))
        actions.add_action(move)
        self.insert_action_group("plan", actions)

        self.content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=24,
                               margin_top=24, margin_bottom=24, margin_start=12, margin_end=12)
        self.scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)
        self.scroller.set_child(Adw.Clamp(maximum_size=640, child=self.content))
        stack = Adw.ViewStack()
        stack.add_titled_with_icon(self.scroller, "plan", "Plan", sym("view-list-symbolic", "view-list-details-symbolic"))
        stack.add_titled_with_icon(self._big_page(), "big", "What's Big",
                                   sym("drive-harddisk-symbolic", "folder-symbolic"))
        header.set_title_widget(Adw.ViewSwitcher(stack=stack, policy=Adw.ViewSwitcherPolicy.WIDE))
        view.set_content(stack)

        self.go = Gtk.Button(css_classes=["pill", "suggested-action"], halign=Gtk.Align.CENTER,
                             margin_top=12, margin_bottom=12)
        self.go.connect("clicked", lambda *_: win.apply(plan))
        view.add_bottom_bar(self.go)
        self.rebuild()

    def rebuild(self):
        scroll = self.scroller.get_vadjustment().get_value()
        while child := self.content.get_first_child():
            self.content.remove(child)
        self.items.clear()
        plan = self.plan

        hero = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        hero.append(Gtk.Label(label=f"{plan.item_count:,} items → {len(plan.groups)} groups",
                              css_classes=["title-1"], wrap=True))
        hero.append(Gtk.Label(label=f"{tilde(plan.folder)} · nothing moves until you approve",
                              css_classes=["dim-label"], wrap=True))
        self.content.append(hero)
        self.content.append(self._compare())

        for group in plan.groups:
            self.content.append(self._group(group))

        if plan.skipped:
            skipped = Adw.ExpanderRow(title="Left alone", subtitle=fmt.items(len(plan.skipped)))
            for path, reason in plan.skipped:
                skipped.add_row(plain_row(os.path.relpath(path, plan.folder), reason))
            self.content.append(boxed_rows("Protected", [skipped],
                                           "Repositories, projects and hidden files are never touched."))
        self.refresh()
        GLib.idle_add(self.scroller.get_vadjustment().set_value, scroll)

    def _compare(self):
        def card(caption):
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, css_classes=["card"],
                          width_request=170)
            inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, margin_top=12, margin_bottom=12,
                            margin_start=18, margin_end=18)
            inner.append(Gtk.Label(label=caption, css_classes=["caption-heading", "dim-label"], xalign=0))
            count = Gtk.Label(css_classes=["title-2"], xalign=0)
            amount = Gtk.Label(css_classes=["dim-label"], xalign=0)
            inner.append(count)
            inner.append(amount)
            box.append(inner)
            return box, count, amount

        row = Gtk.Box(spacing=12, halign=Gtk.Align.CENTER)
        before, self.before_count, self.before_size = card("BEFORE")
        after, self.after_count, self.after_size = card("AFTER")
        row.append(before)
        row.append(Gtk.Image(icon_name="go-next-symbolic", css_classes=["dim-label"]))
        row.append(after)
        self.changes = Gtk.Label(css_classes=["dim-label"], wrap=True, justify=Gtk.Justification.CENTER)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        box.append(row)
        box.append(self.changes)
        return box

    def _group(self, group):
        expander = Adw.ExpanderRow(title=fmt.items(len(group.items)), subtitle=size(group.size),
                                   show_enable_switch=True, enable_expansion=group.enabled)
        filled = []

        def fill(*_):
            if expander.get_expanded():
                self.expanded.add(group.key)
            else:
                self.expanded.discard(group.key)
            if expander.get_expanded() and not filled:
                filled.append(True)
                for item in group.items:
                    expander.add_row(self._item_row(item, group))

        def toggled(*_):
            group.enabled = expander.get_enable_expansion()
            self.refresh()

        expander.connect("notify::expanded", fill)
        expander.connect("notify::enable-expansion", toggled)
        if group.key in self.expanded and group.enabled:
            expander.set_expanded(True)

        box = boxed_rows(group.title, [expander], group.description)
        if group.action == MOVE:
            dest = Gtk.Button(css_classes=["flat"], valign=Gtk.Align.CENTER, tooltip_text="Change Destination",
                              child=Adw.ButtonContent(icon_name=sym("folder-symbolic"), label=tilde(group.dest),
                                                      can_shrink=True))
            dest.connect("clicked", lambda *_: self.change_dest(group))
            box.set_header_suffix(dest)
        else:
            box.set_header_suffix(Gtk.Label(label="Holding area", css_classes=["dim-label", "caption"],
                                            valign=Gtk.Align.CENTER))

        target = Gtk.DropTarget.new(GObject.TYPE_STRING, Gdk.DragAction.MOVE)
        target.connect("drop", lambda _t, value, _x, _y: self.move(value, group.key) or True)
        box.add_controller(target)
        return box

    def _item_row(self, item, group):
        key = str(id(item))
        self.items[key] = item
        name = os.path.relpath(item.path, self.plan.folder) + ("/" if item.is_dir else "")
        row = plain_row(name, f"{item.reason} · {size(item.size)}")
        check = Gtk.CheckButton(active=item.enabled, valign=Gtk.Align.CENTER)

        def toggled(c):
            item.enabled = c.get_active()
            self.refresh()
        check.connect("toggled", toggled)
        row.add_prefix(check)
        row.set_activatable_widget(check)

        menu = Gio.Menu()
        entry = Gio.MenuItem.new("Show in Folder", None)
        entry.set_action_and_target_value("plan.reveal", GLib.Variant.new_string(key))
        menu.append_item(entry)
        others = Gio.Menu()
        for g in self.plan.groups:
            if g is not group:
                entry = Gio.MenuItem.new(g.title, None)
                entry.set_action_and_target_value("plan.move", GLib.Variant.new_string(f"{key}|{g.key}"))
                others.append_item(entry)
        if others.get_n_items():
            menu.append_section("Move to Group", others)
        row.add_suffix(Gtk.MenuButton(icon_name="view-more-symbolic", menu_model=menu, valign=Gtk.Align.CENTER,
                                      css_classes=["flat"], tooltip_text="More"))

        drag = Gtk.DragSource(actions=Gdk.DragAction.MOVE)
        drag.connect("prepare", lambda *_: Gdk.ContentProvider.new_for_value(key))
        drag.connect("drag-begin", lambda src, _d: src.set_icon(Gtk.WidgetPaintable(widget=row), 0, 0))
        row.add_controller(drag)
        return row

    def _big_page(self):
        plan = self.plan
        page_ = Adw.PreferencesPage()
        group = Adw.PreferencesGroup(title="What's Taking Up Space",
                                     description=f"{size(plan.total_size)} in {fmt.items(plan.top_count)}")
        total = plan.total_size or 1
        for path, n, is_dir in plan.largest:
            share = n / total
            row = plain_row(os.path.basename(path) + ("/" if is_dir else ""),
                            f"{size(n)} · {share:.0%}" if share >= 0.01 else size(n))
            row.add_prefix(Gtk.Image.new_from_gicon(file_icon(path, is_dir)))
            bar = Gtk.LevelBar(value=share, valign=Gtk.Align.CENTER, width_request=140)
            for name in ("low", "high", "full"):
                bar.remove_offset_value(name)
            row.add_suffix(bar)
            reveal = Gtk.Button(icon_name=sym("folder-open-symbolic", "document-open-folder-symbolic", "folder-symbolic"),
                                valign=Gtk.Align.CENTER, css_classes=["flat"], tooltip_text="Show in Folder")
            reveal.connect("clicked", lambda *_, p=path: show_in_folder(p, self.win))
            row.add_suffix(reveal)
            group.add(row)
        page_.add(group)
        return page_

    def move(self, key, group_key):
        item = self.items.get(key)
        target = next((g for g in self.plan.groups if g.key == group_key), None)
        if item and target:
            self.plan.move_item(item, target)
            self.expanded.add(target.key)
            GLib.idle_add(self.rebuild)

    def change_dest(self, group):
        dialog = Gtk.FileDialog(title=f"Where Should {group.title} Go?", modal=True,
                                initial_folder=Gio.File.new_for_path(self.plan.folder))

        def picked(d, res):
            try:
                folder = d.select_folder_finish(res)
            except GLib.Error:
                return
            path = folder and folder.get_path()
            if not path:
                return
            if os.path.realpath(path) == self.plan.folder:
                self.win.alert("Pick Another Folder", "That is the folder being tidied.")
                return
            try:
                group.dest = check_folder(path)
            except Protected as e:
                self.win.alert("Tidy Can't Move Files There", str(e))
                return
            self.rebuild()
        dialog.select_folder(self.win, None, picked)

    def refresh(self):
        chosen = self.plan.selected
        p = self.plan.preview()
        self.before_count.set_label(fmt.items(p.before_count))
        self.before_size.set_label(size(p.before_size))
        self.after_count.set_label(fmt.items(p.after_count))
        self.after_size.set_label(size(p.after_size))
        parts = []
        for d in p.destinations:
            where = os.path.basename(d.path) if os.path.dirname(d.path) == self.plan.folder else tilde(d.path)
            parts.append(f"{where}{' (new)' if d.new else ''} +{d.count}")
        if p.held_count:
            parts.append(f"holding area {fmt.items(p.held_count)}, {size(p.held_size)}")
        self.changes.set_label(" · ".join(parts))
        self.go.set_label(f"Tidy {fmt.items(len(chosen))}" if chosen else "Nothing Selected")
        self.go.set_sensitive(bool(chosen))


class DonePage(Adw.NavigationPage):
    def __init__(self, win, plan, res):
        super().__init__(title=os.path.basename(plan.folder), tag="done")
        moved_any = res.done > 0
        status = Adw.StatusPage(
            icon_name="emblem-ok-symbolic" if moved_any else "dialog-warning-symbolic",
            title="All Tidy" if moved_any else "Nothing Was Moved",
            description=(f"Moved {fmt.items(res.done)} ({size(res.size)}). Removed items stay in the holding "
                         f"area for {HOLD_DAYS} days, and Tidy History can put anything back.")
            if moved_any else "")
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=24)
        buttons = Gtk.Box(spacing=12, halign=Gtk.Align.CENTER)
        if res.run:
            undo = Gtk.Button(label="Undo", css_classes=["pill"])
            undo.connect("clicked", lambda *_: win.undo(res.run))
            buttons.append(undo)
        finish = Gtk.Button(label="Done", css_classes=["pill", "suggested-action"])
        finish.connect("clicked", lambda *_: win.nav.pop_to_tag("start"))
        buttons.append(finish)
        box.append(buttons)
        problems = [(p, w) for p, w in res.skipped + res.failed]
        if problems:
            rows = [plain_row(os.path.relpath(p, plan.folder), w) for p, w in problems]
            box.append(Adw.Clamp(maximum_size=520, child=boxed_rows("Not Moved", rows)))
        status.set_child(box)
        view = Adw.ToolbarView(content=status)
        view.add_top_bar(Adw.HeaderBar())
        self.set_child(view)


class HistoryDialog(Adw.Dialog):
    def __init__(self, win):
        super().__init__(title="Tidy History", content_width=600, content_height=640)
        self.win = win
        self.history = win.history
        self.nav = Adw.NavigationView()
        self.set_child(self.nav)
        self.reload()

    def reload(self):
        runs = self.history.runs()
        if not runs:
            content = Adw.StatusPage(icon_name="document-open-recent-symbolic", title="No History Yet",
                                     description="Every tidy run shows up here, and you can undo it.")
        else:
            content = Adw.PreferencesPage()
            group = Adw.PreferencesGroup(description=f"Removed items are kept for {HOLD_DAYS} days.")
            for run in runs:
                group.add(self._run_row(run))
            content.add(group)
        self.nav.replace([page("Tidy History", content, "runs")])

    def _run_row(self, run):
        when = time.strftime("%a %d %b %Y, %H:%M", time.localtime(run["started"]))
        active = run["active"] or 0
        state = f"{fmt.items(active)} can be undone" if active else "Fully undone or expired"
        row = Adw.ActionRow(title=when, subtitle=f"{tilde(run['folder'])} · {state}",
                            use_markup=False, activatable=True)
        if active:
            undo = Gtk.Button(label="Undo", valign=Gtk.Align.CENTER)
            undo.connect("clicked", lambda *_: self.undo(run=run["id"]))
            row.add_suffix(undo)
        row.add_suffix(Gtk.Image.new_from_icon_name("go-next-symbolic"))
        row.connect("activated", lambda *_: self.nav.push(self._run_page(run, when)))
        return row

    def _run_page(self, run, when):
        content = Adw.PreferencesPage()
        group = Adw.PreferencesGroup(title=tilde(run["folder"]))
        for m in self.history.moves(run["id"]):
            row = plain_row(os.path.basename(m["src"]), self._describe(m))
            if m["status"] == "moved":
                put_back = Gtk.Button(icon_name="edit-undo-symbolic", valign=Gtk.Align.CENTER,
                                      css_classes=["flat"], tooltip_text="Put Back")
                put_back.connect("clicked", lambda *_, mid=m["id"]: self.undo(ids=[mid]))
                row.add_suffix(put_back)
            group.add(row)
        content.add(group)
        return page(when, content, f"run-{run['id']}")

    @staticmethod
    def _describe(m):
        if m["status"] == "moved":
            if m["action"] == HOLD:
                return "In holding area until " + time.strftime("%d %b", time.localtime(m["expires"]))
            return f"Moved to {tilde(os.path.dirname(m['dst']))}"
        return {"restored": "Put back", "expired": "Expired and deleted",
                "missing": "Missing from the holding area",
                "failed": f"Not moved: {m['error'] or 'error'}"}.get(m["status"], m["status"])

    def undo(self, run=None, ids=None):
        def done(res):
            self.reload()
            msg = f"Put back {fmt.items(res.done)}"
            if res.failed:
                msg += f", {len(res.failed)} could not be restored"
            self.win.toast(msg)
        run_async(lambda: self.history.undo(run=run, ids=ids), done,
                  lambda e: self.win.alert("Could Not Undo", str(e)))


class App(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.HANDLES_OPEN)
        self.history = None
        about = Gio.SimpleAction.new("about", None)
        about.connect("activate", self.on_about)
        self.add_action(about)
        quit_ = Gio.SimpleAction.new("quit", None)
        quit_.connect("activate", lambda *_: self.quit())
        self.add_action(quit_)
        self.set_accels_for_action("app.quit", ["<Control>q"])

    def do_startup(self):
        Adw.Application.do_startup(self)
        self.history = History()
        threading.Thread(target=self.history.purge, daemon=True).start()

    def window(self):
        return self.get_active_window() or Window(self)

    def do_activate(self):
        self.window().present()

    def do_open(self, files, n_files, hint):
        win = self.window()
        win.present()
        if files and files[0].get_path():
            win.scan(files[0].get_path())

    def on_about(self, *_):
        Adw.AboutDialog(application_name="Tidy", application_icon="edit-clear-all", version=VERSION,
                        developer_name="Mustapha Alioglou",
                        comments="Tidy a messy folder: see the plan, approve it, undo anything.").present(
            self.get_active_window())


def main(argv=None):
    return App().run(argv if argv is not None else sys.argv)
