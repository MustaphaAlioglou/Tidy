import os
import shutil
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tidy.core import History, Protected, build_plan, check_folder, scan
from tidy.core import ops
from tidy.core.packages import package_name
from tidy.core.rules import Settings

DAY = 86400


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(dir=os.path.expanduser("~"), prefix="tidy-test-")
        self.dir = os.path.join(self.tmp, "Downloads")
        os.makedirs(self.dir)
        self.history = History(os.path.join(self.tmp, "data"))
        self.now = time.time()

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def make(self, rel, data=b"x", age_days=0):
        path = os.path.join(self.dir, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as fh:
            fh.write(data)
        t = self.now - age_days * DAY
        os.utime(path, (t, t))
        return path

    def plan(self, installed=()):
        return build_plan(scan(self.dir), Settings(), now=self.now, installed=set(installed))

    def group(self, plan, key):
        return next((g for g in plan.groups if g.key == key), None)

    def paths(self, plan, key):
        g = self.group(plan, key)
        return sorted(os.path.relpath(i.path, self.dir) for i in g.items) if g else []


class Safety(Base):
    def test_refuses_system_and_home(self):
        for path in ("/", "/etc", "/usr/share", os.path.expanduser("~")):
            with self.assertRaises(Protected):
                check_folder(path)

    def test_refuses_hidden_and_projects(self):
        hidden = os.path.join(self.tmp, ".config")
        os.makedirs(hidden)
        with self.assertRaises(Protected):
            check_folder(hidden)
        os.makedirs(os.path.join(self.dir, ".git"))
        with self.assertRaises(Protected):
            check_folder(self.dir)
        self.assertTrue(check_folder(self.dir, allow_project=True))

    def test_skips_repos_and_hidden_inside(self):
        self.make("repo/.git/HEAD", age_days=400)
        self.make("repo/main.c", age_days=400)
        self.make(".hidden", age_days=400)
        plan = self.plan()
        self.assertEqual(plan.groups, [])
        self.assertIn("git repository", [r for _, r in plan.skipped])


class Rules(Base):
    def test_duplicates_keep_original(self):
        self.make("photo.jpg", b"same" * 100, age_days=3)
        self.make("photo (1).jpg", b"same" * 100, age_days=2)
        self.make("photo (2).jpg", b"same" * 100, age_days=1)
        self.make("other.jpg", b"diff" * 100, age_days=1)
        self.assertEqual(self.paths(self.plan(), "duplicates"), ["photo (1).jpg", "photo (2).jpg"])

    def test_duplicate_inside_subfolder_never_removed(self):
        self.make("a.pdf", b"doc" * 50000, age_days=1)
        self.make("Papers/a.pdf", b"doc" * 50000, age_days=5)
        self.make("lib/x.so", b"so", age_days=1)
        self.make("lib/y.so", b"so", age_days=1)
        self.assertEqual(self.paths(self.plan(), "duplicates"), ["a.pdf"])

    def test_installers(self):
        self.make("firefox-130.0-1-x86_64.pkg.tar.zst", b"ff", age_days=2)
        self.make("tool.AppImage", b"tool", age_days=60)
        self.make("fresh.deb", b"deb", age_days=2)
        plan = self.plan(installed={"firefox"})
        self.assertEqual(self.paths(plan, "installers"), ["firefox-130.0-1-x86_64.pkg.tar.zst", "tool.AppImage"])

    def test_clutter(self):
        self.make("movie.mkv.part", b"1", age_days=3)
        self.make("now.iso.part", b"2", age_days=0)
        self.make("stuff.zip", b"3", age_days=2)
        self.make("stuff/readme.txt", b"4", age_days=2)
        os.makedirs(os.path.join(self.dir, "empty"))
        old = self.now - 2 * DAY
        os.utime(os.path.join(self.dir, "empty"), (old, old))
        self.assertEqual(self.paths(self.plan(), "clutter"), ["empty", "movie.mkv.part", "stuff.zip"])

    def test_old_files_and_folders(self):
        self.make("ancient.txt", b"1", age_days=400)
        self.make("recent.txt", b"2", age_days=10)
        self.make("oldproj/a.txt", b"3", age_days=300)
        self.make("mixed/a.txt", b"4", age_days=300)
        self.make("mixed/b.txt", b"5", age_days=1)
        self.assertEqual(self.paths(self.plan(), "old"), ["ancient.txt", "oldproj"])

    def test_scan_does_not_bump_atime(self):
        path = self.make("big.bin", b"z" * 200000, age_days=400)
        self.make("big copy.bin", b"z" * 200000, age_days=400)
        self.plan()
        self.assertLess(os.stat(path).st_atime, self.now - 300 * DAY)

    def test_package_names(self):
        self.assertEqual(package_name("code_1.90.0-1_amd64.deb"), "code")
        self.assertEqual(package_name("htop-3.3.0-1.fc40.x86_64.rpm"), "htop")
        self.assertEqual(package_name("yay-bin-12.3.5-1-x86_64.pkg.tar.zst"), "yay-bin")


class Ops(Base):
    def test_move_never_clobbers(self):
        a = self.make("a.txt", b"a")
        b = self.make("b.txt", b"b")
        with self.assertRaises(FileExistsError):
            ops.move(a, b)
        with open(b, "rb") as fh:
            self.assertEqual(fh.read(), b"b")

    def test_unique_path(self):
        self.make("x.tar.gz")
        self.make("x (2).tar.gz")
        self.assertEqual(os.path.basename(ops.unique_path(os.path.join(self.dir, "x.tar.gz"))), "x (3).tar.gz")


class Roundtrip(Base):
    def setup_mess(self):
        self.make("photo.jpg", b"p" * 10, age_days=3)
        self.make("photo (1).jpg", b"p" * 10, age_days=2)
        self.make("old.txt", b"o", age_days=400)
        self.make("setup.AppImage", b"i", age_days=90)

    def test_apply_then_undo_restores_everything(self):
        self.setup_mess()
        before = sorted(os.listdir(self.dir))
        res = self.history.apply(self.plan())
        self.assertEqual(res.done, 3)
        self.assertEqual(sorted(os.listdir(self.dir)), ["Archive", "photo.jpg"])
        self.assertEqual(self.history.undo(res.run).done, 3)
        self.assertEqual(sorted(os.listdir(self.dir)), before)
        self.assertEqual(os.listdir(self.history.holding), [])

    def test_unticked_items_stay(self):
        self.setup_mess()
        plan = self.plan()
        self.group(plan, "old").enabled = False
        self.group(plan, "installers").items[0].enabled = False
        self.history.apply(plan)
        self.assertIn("old.txt", os.listdir(self.dir))
        self.assertIn("setup.AppImage", os.listdir(self.dir))

    def test_changed_file_is_skipped(self):
        self.setup_mess()
        plan = self.plan()
        with open(os.path.join(self.dir, "old.txt"), "ab") as fh:
            fh.write(b"edited")
        res = self.history.apply(plan)
        self.assertEqual([os.path.basename(p) for p, _ in res.skipped], ["old.txt"])
        self.assertIn("old.txt", os.listdir(self.dir))

    def test_undo_single_file_and_name_collision(self):
        self.setup_mess()
        res = self.history.apply(self.plan())
        moves = {os.path.basename(m["src"]): m for m in self.history.moves(res.run)}
        self.make("old.txt", b"new one")
        self.history.undo(ids=[moves["old.txt"]["id"]])
        self.assertIn("old (2).txt", os.listdir(self.dir))
        self.assertNotIn("setup.AppImage", os.listdir(self.dir))
        self.assertFalse(os.path.exists(os.path.join(self.dir, "Archive")))

    def test_purge_expires_only_held_items(self):
        self.setup_mess()
        res = self.history.apply(self.plan())
        self.assertEqual(self.history.purge(now=time.time() + 31 * DAY), 2)
        statuses = {os.path.basename(m["src"]): m["status"] for m in self.history.moves(res.run)}
        self.assertEqual(statuses, {"photo (1).jpg": "expired", "setup.AppImage": "expired", "old.txt": "moved"})
        self.assertEqual(os.listdir(self.history.holding), [])

    def test_recover_interrupted_move(self):
        src = self.make("x.bin", b"x")
        with self.history._db() as db:
            db.execute("INSERT INTO runs(id, folder, started) VALUES (1, ?, 0)", (self.dir,))
            dst = os.path.join(self.history.holding, "1", "1", "x.bin")
            db.execute("INSERT INTO moves(run, src, dst, action, size, is_dir, status, at)"
                       " VALUES (1, ?, ?, 'hold', 1, 0, 'pending', 0)", (src, dst))
        ops.move(src, dst)
        History(self.history.root)
        self.assertEqual(self.history.moves(1)[0]["status"], "moved")
        self.assertEqual(self.history.undo(1).done, 1)
        self.assertTrue(os.path.exists(src))


if __name__ == "__main__":
    unittest.main()
