import os
import shutil
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tidy.core import HOLD, MOVE, History, build_plan, scan, sentences
from tidy.core.rules import Settings
from tidy.watch import Watcher

DAY = 86400


class Sentences(unittest.TestCase):
    def rule(self, text):
        rs = sentences.parse(text)
        self.assertEqual(rs.errors, [])
        return rs.rules[0]

    def test_move_with_every_clause(self):
        r = self.rule("Move PDFs older than 30 days from Downloads to Documents/Old PDFs.")
        self.assertEqual((r.action, r.exts, r.older_days, r.source, r.dest, r.auto),
                         (MOVE, {"pdf"}, 30, "Downloads", "Documents/Old PDFs", False))

    def test_clause_order_and_auto(self):
        r = self.rule("Always move pictures and jpg files to Pictures from Desktop")
        self.assertEqual((r.kinds, r.exts, r.source, r.dest, r.auto), ({"Pictures"}, {"jpg"}, "Desktop", "Pictures", True))
        r = self.rule("Put screenshots in Pictures/Screenshots automatically")
        self.assertEqual((r.topics, r.dest, r.source, r.auto), ({"Screenshots"}, "Pictures/Screenshots", None, True))

    def test_hold_and_named(self):
        r = self.rule("Get rid of installers older than two weeks")
        self.assertEqual((r.action, r.installers, r.older_days), (HOLD, True, 14))
        r = self.rule('Move files named "*.torrent" to Torrents')
        self.assertEqual(r.patterns, ["*.torrent"])
        self.assertEqual(self.rule("Move files called invoice to Bills").patterns, ["*invoice*"])

    def test_watch_and_notify(self):
        rs = sentences.parse("Watch Downloads and Desktop, and tell me after 30 new files every 2 hours\n"
                             "# a comment\nNotify me after 12 new files")
        self.assertEqual((rs.watch.folders, rs.watch.threshold, rs.watch.interval), (["Downloads", "Desktop"], 12, 7200))

    def test_errors_explain(self):
        rs = sentences.parse("Move kittens to X\nMove PDFs\nHold PDFs to Documents\nDance\nMove PDFs older than many days to X")
        self.assertEqual([n for n, _, _ in rs.errors], [1, 2, 3, 4, 5])
        self.assertIn("kittens", rs.errors[0][2])


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(dir=os.path.expanduser("~"), prefix="tidy-test-")
        self.dir = os.path.join(self.tmp, "Downloads")
        os.makedirs(self.dir)
        self.history = History(os.path.join(self.tmp, "data"))
        self.now = time.time()

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def make(self, name, data=b"x", age_days=1):
        path = os.path.join(self.dir, name)
        with open(path, "wb") as fh:
            fh.write(data)
        os.utime(path, (self.now - age_days * DAY,) * 2)
        return path

    def rules(self, text):
        rs = sentences.parse(text.replace("ELSEWHERE", os.path.join(self.tmp, "Elsewhere")).replace("HERE", self.dir))
        self.assertEqual(rs.errors, [])
        return rs


class Plans(Base):
    def plan(self, rs):
        return build_plan(scan(self.dir), Settings(), now=self.now, installed=set(), user_rules=rs.rules)

    def test_rules_come_first(self):
        self.make("a.pdf", b"1", 40)
        self.make("b.pdf", b"2", 2)
        self.make("Screenshot_20260101_101010.png", b"3", 2)
        rs = self.rules("Move PDFs older than 30 days from HERE to ELSEWHERE/Old\nHold screenshots")
        plan = self.plan(rs)
        first, second = plan.groups[:2]
        self.assertEqual(([os.path.basename(i.path) for i in first.items], first.dest),
                         (["a.pdf"], os.path.join(self.tmp, "Elsewhere", "Old")))
        self.assertEqual((second.action, len(second.items)), (HOLD, 1))
        self.assertEqual([os.path.basename(i.path) for i in next(g for g in plan.groups if g.key == "sort:Documents").items],
                         ["b.pdf"])

    def test_other_folders_and_bad_destinations(self):
        self.make("a.pdf")
        self.assertEqual(self.plan(self.rules("Move PDFs from Desktop to ELSEWHERE"))
                         .groups[0].key, "sort:Documents")
        plan = self.plan(self.rules("Move PDFs to /etc"))
        self.assertEqual(plan.groups[0].key, "sort:Documents")
        self.assertIn("outside your home folder", plan.notes[0])

    def test_relative_destination_stays_inside_folder(self):
        self.make("x.torrent")
        plan = self.plan(self.rules('Move files named "*.torrent" to Tidy Test Torrents Zq'))
        self.assertEqual(plan.groups[0].dest, os.path.join(self.dir, "Tidy Test Torrents Zq"))


class Watching(Base):
    def setUp(self):
        super().setUp()
        self.sent = []
        self.watcher = Watcher(self.history, notifier=lambda *a: self.sent.append(a), opener=lambda f: None)

    def test_notifies_only_about_new_files_and_only_once(self):
        for n in range(3):
            self.make(f"old{n}.png", bytes([n]), 2)
        rs = self.rules("Watch HERE and tell me after 3 new files")
        self.assertEqual(self.watcher.check(self.dir, rs)[1:], (0, False))
        for n in range(3):
            self.make(f"new{n}.png", bytes([10 + n]), 0.01)
        self.assertEqual(self.watcher.check(self.dir, rs)[1:], (3, True))
        self.assertEqual(self.sent[0][0], "3 new files in Downloads. Tidy up?")
        self.assertEqual(self.watcher.check(self.dir, rs)[1:], (0, False))
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(sorted(os.listdir(self.dir)), sorted(f"{p}{n}.png" for p in ("old", "new") for n in range(3)))

    def test_auto_rule_moves_and_can_be_undone(self):
        shot = self.make("Screenshot_20260101_101010.png", b"s", 1)
        self.make("notes.txt", b"n", 1)
        rs = self.rules("Watch HERE\nPut screenshots in ELSEWHERE/Shots automatically")
        res, _, _ = self.watcher.check(self.dir, rs)
        self.assertEqual(res.done, 1)
        self.assertFalse(os.path.exists(shot))
        self.assertTrue(os.path.exists(os.path.join(self.tmp, "Elsewhere", "Shots", os.path.basename(shot))))
        self.assertTrue(os.path.exists(os.path.join(self.dir, "notes.txt")))
        summary, body, actions, on_action = self.sent[0]
        self.assertEqual((summary, actions), ("Tidied 1 item in Downloads", [("undo", "Undo")]))
        on_action("undo")
        self.assertTrue(os.path.exists(shot))

    def test_rule_errors_are_reported_once(self):
        rs = sentences.parse("Move kittens to X")
        self.watcher.check_all(rs)
        self.watcher.check_all(rs)
        self.assertEqual(len(self.sent), 1)
        self.assertIn("Line 1", self.sent[0][1])


if __name__ == "__main__":
    unittest.main()
