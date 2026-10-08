import os
import shutil
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from tidy.core import History  # noqa: E402

DAY = 86400


def make_mess(folder):
    now = time.time()

    def mk(rel, data, days):
        path = os.path.join(folder, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as fh:
            fh.write(data)
        os.utime(path, (now - days * DAY, now - days * DAY))

    mk("photo.jpg", b"\xff\xd8\xff" + b"p" * 500, 5)
    mk("photo (1).jpg", b"\xff\xd8\xff" + b"p" * 500, 4)
    mk("old-notes.txt", b"old", 400)
    mk("tool.AppImage", b"tool", 90)
    mk("paper.pdf", b"%PDF-1.7", 3)
    mk("movie.mkv.part", b"partial", 3)


def snapshot(folder):
    out = {}
    for root, dirs, files in os.walk(folder):
        for name in files:
            path = os.path.join(root, name)
            fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOATIME", 0))
            with os.fdopen(fd, "rb") as fh:
                out[os.path.relpath(path, folder)] = fh.read()
    return out


class Frontend:
    def setUp(self):
        self.tmp = tempfile.mkdtemp(dir=os.path.expanduser("~"), prefix="tidy-gui-test-")
        self.folder = os.path.join(self.tmp, "Downloads")
        os.makedirs(self.folder)
        make_mess(self.folder)
        self.before = snapshot(self.folder)
        self.history = History(os.path.join(self.tmp, "data"))
        os.environ["XDG_CONFIG_HOME"] = os.path.join(self.tmp, "config")
        os.environ["XDG_DATA_HOME"] = os.path.join(self.tmp, "data")

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def wait(self, cond, what, timeout=30):
        end = time.time() + timeout
        while time.time() < end:
            self.pump()
            if cond():
                return
            time.sleep(0.01)
        self.fail(f"timed out waiting for {what}")

    def check_applied(self):
        after = snapshot(self.folder)
        self.assertNotEqual(after, self.before)
        self.assertIn(os.path.join("Archive", "old-notes.txt"), after)
        self.assertIn(os.path.join("Pictures", "photo.jpg"), after)
        self.assertNotIn("photo (1).jpg", after)

    def check_restored(self):
        self.assertEqual(snapshot(self.folder), self.before)
        self.assertEqual(sorted(os.listdir(self.folder)), sorted(self.before))


def rules_text():
    from tidy.core import sentences
    with open(sentences.rules_path()) as fh:
        return fh.read()


class FakeWatcher:
    """Stands in for WatchSettings' start/stop so GUI tests never launch a
    real watcher."""

    def __init__(self, real):
        self.real, self.running, self.autostart = real, False, False

    def __getattr__(self, name):
        return getattr(self.real, name)

    def __setattr__(self, name, value):
        if name in ("real", "running", "autostart"):
            object.__setattr__(self, name, value)
        else:
            setattr(self.real, name, value)

    def start(self):
        self.running = True

    def stop(self):
        self.running = False


class QtFrontend(Frontend, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            import tidy.qt.app as q
        except (ImportError, ValueError) as e:
            raise unittest.SkipTest(f"Qt frontend unavailable: {e}")
        cls.q = q
        cls.app = q.QApplication.instance() or q.QApplication(["tidy-test"])
        q.BRIDGE = q.Bridge()

    def pump(self):
        self.app.processEvents()

    def test_scan_apply_undo(self):
        q = self.q
        win = q.MainWindow(self.history)
        win.show()
        win.select_folder(self.folder)
        win.scan(self.folder)
        self.wait(lambda: win.stack.currentWidget() is win.planview, "the plan")
        pv = win.planview
        self.assertGreaterEqual(len(pv.plan.groups), 4)
        self.assertGreater(pv.big.topLevelItemCount(), 0)
        self.assertIn("AFTER", pv.after.text())

        dup = next(c for c, i, g, t in pv.rows.values() if i.path.endswith("paper.pdf"))
        pv.move([dup], "sort:Documents")
        pv.tabs.setCurrentIndex(1)
        pv.go.click()
        self.wait(lambda: win.stack.currentWidget() is win.done, "the done page")
        self.check_applied()

        dialog = q.HistoryDialog(win)
        self.assertEqual(dialog.tree.topLevelItemCount(), 1)
        dialog.close()

        win.undo(win.last_run)
        self.wait(lambda: win.stack.currentWidget() is win.home, "undo")
        self.check_restored()
        win.close()

    def test_watch_dialog(self):
        q = self.q
        win = q.MainWindow(self.history)
        shown = []
        orig = q.QMessageBox.information
        q.QMessageBox.information = staticmethod(lambda *a: shown.append(a[1]))
        try:
            from tidy.watch import WatchSettings
            fake = FakeWatcher(WatchSettings())
            dialog = q.WatchDialog(win, fake)
            self.assertEqual(dialog.button.text(), "Start")
            dialog.button.click()
            self.wait(lambda: dialog.button.text() == "Stop", "the watcher to start")
            self.assertTrue(fake.running)
            dialog.button.click()
            self.wait(lambda: dialog.button.text() == "Start", "the watcher to stop")
            dialog.autostart.setChecked(True)
            self.assertTrue(fake.autostart)
            self.assertEqual(dialog.list.count(), 0)
            dialog.add_folder(self.folder)
            dialog.add_folder(os.path.expanduser("~"))
            self.assertEqual(shown, ["Tidy Can't Watch This Folder"])
            self.assertEqual(dialog.list.count(), 1)
            dialog.threshold.setValue(7)
            dialog.minutes.setValue(30)
            self.assertIn("tell me after 7 new files every 30 minutes", rules_text())
            dialog.list.item(0).setSelected(True)
            dialog.remove.click()
            self.assertEqual(dialog.list.count(), 0)
            self.assertNotIn("Watch", rules_text().replace("# Watch", ""))
        finally:
            q.QMessageBox.information = orig
        dialog.close()
        win.close()

    def test_protected_folder_is_refused(self):
        os.makedirs(os.path.join(self.folder, ".git"))
        q = self.q
        shown = []
        orig = q.QMessageBox.information
        q.QMessageBox.information = staticmethod(lambda *a: shown.append(a[1]))
        try:
            win = q.MainWindow(self.history)
            win.scan(self.folder)
            self.wait(lambda: shown, "the refusal")
            self.assertIs(win.stack.currentWidget(), win.home)
        finally:
            q.QMessageBox.information = orig
        self.assertEqual(snapshot(self.folder), self.before)


class GtkFrontend(Frontend, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            import tidy.gtk.app as g
        except (ImportError, ValueError) as e:
            raise unittest.SkipTest(f"GTK frontend unavailable: {e}")
        from gi.repository import Gdk, GLib
        if Gdk.Display.get_default() is None:
            raise unittest.SkipTest("GTK frontend needs a display (run under Xvfb or a desktop session)")
        cls.g, cls.ctx = g, GLib.MainContext.default()
        cls.app = g.App()
        cls.app.set_flags(cls.app.get_flags() | g.Gio.ApplicationFlags.NON_UNIQUE)
        cls.app.register(None)

    def pump(self):
        while self.ctx.pending():
            self.ctx.iteration(False)

    def test_scan_apply_undo(self):
        g = self.g
        self.app.history = self.history
        win = g.Window(self.app)
        win.present()
        win.scan(self.folder)
        self.wait(lambda: isinstance(win.nav.get_visible_page(), g.PlanPage), "the plan")
        page = win.nav.get_visible_page()
        self.assertGreaterEqual(len(page.plan.groups), 4)
        self.assertIn("items", page.after_count.get_label())

        paper = next(i for gr in page.plan.groups for i in gr.items if i.path.endswith("paper.pdf"))
        page.items[str(id(paper))] = paper
        page.move(str(id(paper)), "sort:Documents")
        self.pump()
        page.go.emit("clicked")
        self.wait(lambda: isinstance(win.nav.get_visible_page(), g.DonePage), "the done page")
        self.check_applied()

        dialog = g.HistoryDialog(win)
        dialog.present(win)
        self.pump()
        dialog.close()

        win.undo(self.history.runs()[0]["id"])
        self.wait(lambda: win.nav.get_visible_page().get_tag() == "start", "undo")
        self.wait(lambda: snapshot(self.folder) == self.before, "files to come back", timeout=5)
        self.check_restored()
        win.destroy()

    def test_watch_dialog(self):
        g = self.g
        self.app.history = self.history
        win = g.Window(self.app)
        alerts = []
        win.alert = lambda heading, body: alerts.append(heading)
        win.present()
        from tidy.watch import WatchSettings
        fake = FakeWatcher(WatchSettings())
        dialog = g.WatchDialog(win, fake)
        dialog.present(win)
        self.pump()
        self.assertEqual(dialog.button.get_label(), "Start")
        dialog.button.emit("clicked")
        self.wait(lambda: dialog.button.get_label() == "Stop", "the watcher to start")
        dialog.button.emit("clicked")
        self.wait(lambda: dialog.button.get_label() == "Start", "the watcher to stop")
        dialog.autostart.set_active(True)
        self.assertTrue(fake.autostart)
        dialog.add_folder(self.folder)
        dialog.add_folder(os.path.expanduser("~"))
        self.assertEqual(alerts, ["Tidy Can't Watch This Folder"])
        self.assertEqual([r.get_subtitle() for r in dialog.rows], [g.tilde(self.folder)])
        dialog.threshold.set_value(7)
        dialog.minutes.set_value(30)
        self.pump()
        self.assertIn("tell me after 7 new files every 30 minutes", rules_text())
        name = dialog.settings.folders()[0][0]
        dialog.settings.remove(name)
        dialog.fill()
        self.assertEqual(dialog.rows[0].get_title(), "No folders yet")
        dialog.close()
        win.destroy()

    def test_protected_folder_is_refused(self):
        os.makedirs(os.path.join(self.folder, ".git"))
        g = self.g
        self.app.history = self.history
        win = g.Window(self.app)
        alerts = []
        win.alert = lambda heading, body: alerts.append(heading)
        win.present()
        win.scan(self.folder)
        self.wait(lambda: alerts, "the refusal")
        self.assertEqual(win.nav.get_visible_page().get_tag(), "start")
        self.assertEqual(snapshot(self.folder), self.before)
        win.destroy()


if __name__ == "__main__":
    unittest.main()
