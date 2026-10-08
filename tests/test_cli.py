import contextlib
import io
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from tidy import cli, watch  # noqa: E402
from tidy.core import History, make_plan, sentences  # noqa: E402
from tidy.core.safety import dest_problem  # noqa: E402
from tidy.core.topics import year_of  # noqa: E402

DAY = 86400


class Isolated(unittest.TestCase):
    """A fake home with its own config and data folders."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="tidy-cli-test-")
        self.home = os.path.join(self.tmp, "home")
        self.config = os.path.join(self.home, ".config")
        self.data = os.path.join(self.home, ".local", "share")
        for d in (self.config, self.data):
            os.makedirs(d)
        env = {"HOME": self.home, "XDG_CONFIG_HOME": self.config, "XDG_DATA_HOME": self.data}
        patcher = mock.patch.dict(os.environ, env)
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def folder(self, rel):
        path = os.path.join(self.home, rel)
        os.makedirs(path, exist_ok=True)
        return path

    def make(self, rel, data=b"x", age_days=1):
        path = os.path.join(self.home, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as fh:
            fh.write(data)
        t = time.time() - age_days * DAY
        os.utime(path, (t, t))
        return path

    def cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        code = 0
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                code = cli.main(list(argv)) or 0
            except SystemExit as e:
                code = e.code
        return code, out.getvalue() + err.getvalue()


class Places(Isolated):
    def test_standard_and_localised_folders(self):
        downloads = self.folder("Λήψεις")
        with open(os.path.join(self.config, "user-dirs.dirs"), "w") as fh:
            fh.write('XDG_DOWNLOAD_DIR="$HOME/Λήψεις"\n')
        self.assertEqual(sentences.resolve_place("Downloads"), downloads)
        self.assertEqual(sentences.resolve_place("Λήψεις/Old"), os.path.join(downloads, "Old"))
        self.assertEqual(sentences.resolve_place("~/x"), os.path.join(self.home, "x"))
        self.assertEqual(sentences.resolve_place("/media/usb"), "/media/usb")

    def test_case_insensitive_home_folder(self):
        music = self.folder("Music")
        self.assertEqual(sentences.resolve_place("music/live"), os.path.join(music, "live"))

    def test_destination_in_home_or_inside_folder(self):
        docs = self.folder("Documents")
        dl = self.folder("Downloads")
        self.assertEqual(sentences.resolve_dest("Documents/Old PDFs", dl), os.path.join(docs, "Old PDFs"))
        self.assertEqual(sentences.resolve_dest("Torrents", dl), os.path.join(dl, "Torrents"))
        self.assertEqual(sentences.resolve_dest("Downloads/Sorted", dl), os.path.join(dl, "Sorted"))
        self.assertEqual(sentences.resolve_dest("/media/usb/x", dl), "/media/usb/x")

    def test_dest_problem(self):
        dl = self.folder("Downloads")
        self.assertIsNone(dest_problem(os.path.join(self.home, "Documents", "New"), dl))
        self.assertIsNone(dest_problem("/media/usb/stuff", dl))
        self.assertIn("being tidied", dest_problem(dl, dl))
        self.assertIn("hidden", dest_problem(os.path.join(self.home, ".cache", "x"), dl))
        self.assertIn("outside", dest_problem("/etc/x", dl))
        self.assertIn("outside", dest_problem(self.home, dl))
        self.assertIn("outside", dest_problem("/media", dl))


class Years(unittest.TestCase):
    def test_year_from_name_or_date(self):
        jan_2026 = time.mktime((2026, 1, 15, 12, 0, 0, 0, 0, -1))
        self.assertEqual(year_of("Screenshot_20240301_101010.png", jan_2026), 2024)
        self.assertEqual(year_of("tax-2023.pdf", jan_2026), 2023)
        self.assertEqual(year_of("IMG_1990.jpg", jan_2026), 2026)
        self.assertEqual(year_of("report-2099.pdf", jan_2026), 2026)
        self.assertEqual(year_of("notes.txt", jan_2026), 2026)


class Commands(Isolated):
    def test_rules_add_list_and_errors(self):
        code, out = self.cli("rules")
        self.assertEqual(code, 0)
        self.assertIn("no rules yet", out)
        self.assertEqual(self.cli("rules", "--add", "Hold installers older than 2 weeks")[0], 0)
        code, out = self.cli("rules", "--add", "Move", "kittens", "to", "X")
        self.assertIn("kittens", str(code) + out)
        with open(sentences.rules_path()) as fh:
            text = fh.read()
        self.assertTrue(text.startswith("# Tidy rules"))
        self.assertTrue(text.endswith("Hold installers older than 2 weeks\n"))
        self.assertNotIn("kittens", text)
        with open(sentences.rules_path(), "a") as fh:
            fh.write("Dance PDFs\n")
        code, out = self.cli("rules")
        self.assertEqual(code, 1)
        self.assertIn("installers older than 14 days in any folder -> holding area", out)
        self.assertIn("! Start with what to do", out)

    def test_scan_shows_rules_notes_and_auto(self):
        dl = self.folder("Downloads")
        self.folder("Documents")
        self.make("Downloads/a.pdf")
        self.make("Downloads/b.mp3")
        self.make("Downloads/c.png")
        os.makedirs(os.path.dirname(sentences.rules_path()))
        with open(sentences.rules_path(), "w") as fh:
            fh.write("Move PDFs to Documents/Papers automatically\nMove music to /etc/music\n")
        code, out = self.cli("scan", dl)
        self.assertEqual(code, 0)
        self.assertIn(f"-> {os.path.join(self.home, 'Documents', 'Papers')} [automatic when watched]", out)
        self.assertIn("Skipped \"Move music to /etc/music\"", out)
        self.assertIn("Music (1 item", out)
        self.assertTrue(os.path.exists(os.path.join(dl, "a.pdf")), "a scan without --apply moves nothing")

    def test_scan_apply_undo_and_forget(self):
        dl = self.folder("Downloads")
        pic = self.make("Downloads/IMG_1.jpg")
        code, out = self.cli("scan", dl, "--apply")
        self.assertIn("Undo with: tidy-cli undo 1", out)
        self.assertFalse(os.path.exists(pic))
        self.assertEqual(self.cli("undo", "1")[1].strip(), "Restored 1 item.")
        self.assertTrue(os.path.exists(pic))
        self.assertIn("#1", self.cli("history")[1])
        self.assertEqual(self.cli("forget")[1].strip(), "Forgot 0 items.")

    def test_refuses_protected_folder(self):
        code, out = self.cli("scan", self.home)
        self.assertIn("not the home folder itself", str(code) + out)


class WatchControl(Isolated):
    def test_on_status_off(self):
        self.folder("Downloads")
        with mock.patch.object(watch.subprocess, "Popen") as popen:
            code, out = self.cli("watch", "--on")
        self.assertEqual(code, 0)
        cmd = popen.call_args[0][0]
        self.assertEqual(cmd, [os.path.join(ROOT, "bin", "tidy-cli"), "watch"])
        with open(watch.autostart_path()) as fh:
            entry = fh.read()
        self.assertIn(f'Exec="{os.path.join(ROOT, "bin", "tidy-cli")}" watch', entry)
        self.assertEqual(sentences.load().watch.folders, ["Downloads"], "--on adds a Watch line when there is none")
        self.assertIn("starts with session: yes", self.cli("watch", "--status")[1])
        self.assertIn("not running", self.cli("watch", "--off")[1])
        self.assertFalse(os.path.exists(watch.autostart_path()))

    def test_only_one_watcher(self):
        self.assertIsNone(watch.running_pid())
        lock = watch._take_lock()
        self.addCleanup(lock.close)
        self.assertIsNone(watch._take_lock())
        self.assertEqual(watch.running_pid(), os.getpid())
        self.assertEqual(watch.run(once=True), 1)

    def test_watched_since_counts_from_last_run(self):
        history = History()
        dl = self.folder("Downloads")
        self.assertEqual(history.watched_since(dl, now=100), 100)
        self.assertEqual(history.watched_since(dl, now=500), 100)
        history.set_watched_since(dl, 200)
        self.assertEqual(history.watched_since(dl), 200)
        self.make("Downloads/x.png")
        history.apply(make_plan(dl, rules=sentences.RuleSet()))
        self.assertGreater(history.watched_since(dl), 200)


class Install(Isolated):
    def run_script(self, name):
        stubs = os.path.join(self.tmp, "stubs")
        os.makedirs(stubs, exist_ok=True)
        for tool in ("kbuildsycoca6", "update-desktop-database"):
            path = os.path.join(stubs, tool)
            with open(path, "w") as fh:
                fh.write("#!/bin/sh\nexit 0\n")
            os.chmod(path, 0o755)
        env = dict(os.environ, PATH=f"{stubs}:{os.environ['PATH']}",
                   XDG_BIN_HOME=os.path.join(self.home, ".local", "bin"))
        return subprocess.run([os.path.join(ROOT, name)], env=env, capture_output=True, text=True)

    @unittest.skipUnless(os.path.exists("/usr/bin/python3"), "install.sh uses the system Python")
    def test_install_and_uninstall(self):
        res = self.run_script("install.sh")
        if res.returncode and "needs GTK4" in res.stderr:
            self.skipTest("no GUI toolkit for the system Python")
        self.assertEqual(res.returncode, 0, res.stderr)
        bin_dir = os.path.join(self.home, ".local", "bin")
        self.assertEqual(os.readlink(os.path.join(bin_dir, "tidy")), os.path.join(ROOT, "bin", "tidy"))
        desktop = os.path.join(self.data, "applications", "app.tidy.Tidy.desktop")
        with open(desktop) as fh:
            self.assertIn(f'Exec="{bin_dir}/tidy" %u', fh.read(), "menu entries need a full path")
        os.makedirs(os.path.dirname(watch.autostart_path()))
        open(watch.autostart_path(), "w").close()
        res = self.run_script("uninstall.sh")
        self.assertEqual(res.returncode, 0, res.stderr)
        for path in (os.path.join(bin_dir, "tidy"), desktop, watch.autostart_path()):
            self.assertFalse(os.path.lexists(path), path)


if __name__ == "__main__":
    unittest.main()
