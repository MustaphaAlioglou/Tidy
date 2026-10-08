import os
import shutil
import sys
import tempfile
import time
import unittest
import zlib

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tidy.core import History, Protected, build_plan, check_folder, scan
from tidy.core import ops
from tidy.core.learn import signature
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

    def plan(self, installed=(), learned=None):
        return build_plan(scan(self.dir), Settings(), now=self.now, installed=set(installed), learned=learned)

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


class Sorting(Base):
    def test_groups_by_kind(self):
        self.make("a.png", b"1", age_days=2)
        self.make("b.PDF", b"2", age_days=2)
        self.make("song.flac", b"3", age_days=2)
        self.make("noext", b"%PDF-1.7 hello", age_days=2)
        self.make("weird.xyz", b"4", age_days=2)
        self.make("tool.AppImage", b"5", age_days=2)
        plan = self.plan()
        self.assertEqual(self.paths(plan, "sort:Pictures"), ["a.png"])
        self.assertEqual(self.paths(plan, "sort:Documents"), ["b.PDF", "noext"])
        self.assertEqual(self.paths(plan, "sort:Music"), ["song.flac"])
        self.assertNotIn("weird.xyz", str(plan.groups))
        self.assertNotIn("tool.AppImage", str(plan.groups))

    def test_sorted_folder_is_left_alone_next_time(self):
        self.make("a.png", b"1", age_days=2)
        self.history.apply(self.plan())
        plan = self.plan()
        self.assertEqual(plan.groups, [])
        self.assertIn("made by Tidy", [r for _, r in plan.skipped])

    def test_existing_folder_is_reused_not_marked(self):
        self.make("a.png", b"1", age_days=2)
        self.make("Pictures/old.png", b"2", age_days=2)
        res = self.history.apply(self.plan())
        self.assertEqual(sorted(os.listdir(os.path.join(self.dir, "Pictures"))), ["a.png", "old.png"])
        self.history.undo(res.run)
        self.assertEqual(os.listdir(os.path.join(self.dir, "Pictures")), ["old.png"])

    def test_preview_counts(self):
        self.make("a.png", b"1", age_days=2)
        self.make("b.png", b"2", age_days=2)
        self.make("c.pdf", b"3", age_days=2)
        self.make("c copy.pdf", b"3", age_days=2)
        self.make("keep.xyz", b"4", age_days=2)
        p = self.plan().preview()
        self.assertEqual((p.before_count, p.after_count), (5, 3))
        self.assertEqual((p.held_count, p.held_size), (1, 1))
        self.assertEqual([(os.path.basename(d.path), d.count, d.new) for d in p.destinations],
                         [("Pictures", 2, True), ("Documents", 1, True)])

    def test_move_item_between_groups(self):
        self.make("a.png", b"1", age_days=2)
        self.make("b.pdf", b"2", age_days=2)
        plan = self.plan()
        pics, docs = self.group(plan, "sort:Pictures"), self.group(plan, "sort:Documents")
        docs.enabled = False
        plan.move_item(pics.items[0], docs)
        self.assertEqual(pics.items, [])
        self.assertTrue(docs.enabled)
        self.assertEqual(sorted(os.path.basename(i.path) for i in docs.items), ["a.png", "b.pdf"])
        self.history.apply(plan)
        self.assertEqual(sorted(os.listdir(os.path.join(self.dir, "Documents"))), [".tidy-folder", "a.png", "b.pdf"])

    def test_largest(self):
        self.make("big.bin", b"x" * 5000, age_days=2)
        self.make("dir/a.bin", b"y" * 3000, age_days=2)
        self.make("small.bin", b"z", age_days=2)
        self.assertEqual([(os.path.basename(p), n) for p, n, _ in self.plan().largest],
                         [("big.bin", 5000), ("dir", 3000), ("small.bin", 1)])


def pdf(text):
    body = zlib.compress(b"BT /F1 12 Tf " + b" ".join(b"(%s) Tj" % w for w in text.split()) + b" ET")
    return b"%PDF-1.7\n1 0 obj\n<< /Filter /FlateDecode >>\nstream\n" + body + b"\nendstream\nendobj\n%%EOF\n"


class Topics(Base):
    def test_screenshots_by_year_from_name(self):
        self.make("Screenshot from 2025-03-01 10-00-00.png", b"a", age_days=2)
        self.make("Screenshot_20261007_101500.png", b"b", age_days=2)
        self.make("Στιγμιότυπο οθόνης 2026-01-02.png", b"c", age_days=2)
        self.make("holiday.png", b"d", age_days=2)
        plan = self.plan()
        self.assertEqual(self.paths(plan, "topic:Screenshots:2025"), ["Screenshot from 2025-03-01 10-00-00.png"])
        self.assertEqual(len(self.paths(plan, "topic:Screenshots:2026")), 2)
        self.assertEqual(self.paths(plan, "sort:Pictures"), ["holiday.png"])
        g = self.group(plan, "topic:Screenshots:2025")
        self.assertEqual((g.title, g.dest), ("Screenshots 2025", os.path.join(self.dir, "Screenshots 2025")))

    def test_receipts_and_tax_by_name(self):
        self.make("Invoice-0042.pdf", b"x1", age_days=2)
        self.make("amazon_receipt.jpg", b"x2", age_days=2)
        self.make("tax-return-2024.pdf", b"x3", age_days=2)
        self.make("W-2 2024.pdf", b"x4", age_days=2)
        self.make("syntax notes.pdf", b"x5", age_days=2)
        plan = self.plan()
        year = time.localtime(self.now).tm_year
        self.assertEqual(self.paths(plan, f"topic:Receipts:{year}"), ["Invoice-0042.pdf", "amazon_receipt.jpg"])
        self.assertEqual(self.paths(plan, "topic:Tax:2024"), ["W-2 2024.pdf", "tax-return-2024.pdf"])
        self.assertEqual(self.paths(plan, "sort:Documents"), ["syntax notes.pdf"])

    def test_pdf_contents(self):
        self.make("scan0001.pdf", pdf(b"ACME Ltd Invoice Number 1234 Amount Due 10.00"), age_days=2)
        self.make("scan0002.pdf", pdf(b"Notes about invoices in general"), age_days=2)
        plan = self.plan()
        year = time.localtime(self.now).tm_year
        self.assertEqual(self.paths(plan, f"topic:Receipts:{year}"), ["scan0001.pdf"])
        self.assertEqual(self.paths(plan, "sort:Documents"), ["scan0002.pdf"])

    def test_binary_pdf_is_quick(self):
        noise = bytes((i * 7919) % 251 for i in range(1 << 20)).replace(b")", b"(")
        self.make("photo-scan.pdf", b"%PDF-1.4\nstream\n" + noise + b"\nendstream\n", age_days=2)
        start = time.time()
        self.plan()
        self.assertLess(time.time() - start, 2)


class Learning(Base):
    def apply_and_rescan(self, plan):
        self.history.apply(plan)
        for name in os.listdir(self.dir):
            p = os.path.join(self.dir, name)
            (shutil.rmtree if os.path.isdir(p) else os.unlink)(p)
        return self.history.learned()

    def test_signature(self):
        self.assertEqual(signature("IMG_2041.JPG"), "img_#.jpg")
        self.assertEqual(signature("report (2).pdf"), "report.pdf")
        self.assertIsNone(signature("20261007.jpg"))

    def test_learns_moved_files(self):
        self.make("IMG_2041.jpg", b"a", age_days=2)
        self.make("Invoice-1.pdf", b"b", age_days=2)
        plan = self.plan()
        year = time.localtime(self.now).tm_year
        img = self.group(plan, "sort:Pictures").items[0]
        plan.move_item(img, self.group(plan, f"topic:Receipts:{year}"))
        learned = self.apply_and_rescan(plan)
        self.assertEqual(learned.sigs, {"img_#.jpg": "topic:Receipts"})

        self.make("IMG_2077.jpg", b"c", age_days=2)
        self.make("IMG_1990.jpg", b"d", age_days=2 * 365 + 30)
        plan = self.plan(learned=learned)
        self.assertEqual(self.paths(plan, f"topic:Receipts:{year}"), ["IMG_2077.jpg"])
        self.assertEqual(self.paths(plan, f"topic:Receipts:{year - 2}"), ["IMG_1990.jpg"])
        self.assertIn("before", self.group(plan, f"topic:Receipts:{year}").items[0].reason)
        self.assertIsNone(self.group(plan, "sort:Pictures"))

    def test_learns_destinations(self):
        pictures = os.path.join(self.tmp, "Pictures")
        os.makedirs(pictures)
        self.make("cat.png", b"a", age_days=2)
        self.make("Screenshot from 2025-01-01.png", b"b", age_days=2)
        plan = self.plan()
        self.group(plan, "sort:Pictures").dest = pictures
        self.group(plan, "topic:Screenshots:2025").dest = os.path.join(self.dir, "Shots", "2025")
        os.makedirs(os.path.join(self.dir, "Shots"))
        learned = self.apply_and_rescan(plan)
        self.assertEqual(learned.dests, {"sort:Pictures": pictures, "topic:Screenshots": os.path.join("Shots", "{year}")})

        os.makedirs(os.path.join(self.dir, "Shots"))
        self.make("dog.png", b"c", age_days=2)
        self.make("Screenshot from 2026-02-02.png", b"d", age_days=2)
        plan = self.plan(learned=learned)
        self.assertEqual(self.group(plan, "sort:Pictures").dest, pictures)
        self.assertEqual(self.group(plan, "topic:Screenshots:2026").dest, os.path.join(self.dir, "Shots", "2026"))

    def test_unchanged_plan_learns_nothing_and_forget(self):
        self.make("IMG_1.jpg", b"a", age_days=2)
        learned = self.apply_and_rescan(self.plan())
        self.assertEqual((learned.dests, learned.sigs), ({}, {}))
        self.assertEqual(self.history.forget(), 0)


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
        self.assertEqual(res.done, 4)
        self.assertEqual(sorted(os.listdir(self.dir)), ["Archive", "Pictures"])
        self.assertEqual(self.history.undo(res.run).done, 4)
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
        self.assertEqual(statuses, {"photo (1).jpg": "expired", "setup.AppImage": "expired", "old.txt": "moved", "photo.jpg": "moved"})
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
