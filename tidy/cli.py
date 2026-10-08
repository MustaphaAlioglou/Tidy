import argparse
import os
import sys
import time

from .core import HOLD_DAYS, MOVE, History, Protected, make_plan, places, sentences
from .core import fmt


def _show_plan(plan):
    print(f"{plan.folder}: {plan.scanned} files, {fmt.size(plan.total_size)}")
    if not plan.groups:
        print("Nothing to tidy.")
        return
    p = plan.preview()
    print(f"{plan.item_count} items -> {len(plan.groups)} groups, {fmt.size(plan.freeable)} can be freed")
    print(f"before: {fmt.items(p.before_count)}, after: {fmt.items(p.after_count)}\n")
    for g in plan.groups:
        where = f"-> {g.dest}" if g.action == MOVE else f"-> holding area ({HOLD_DAYS} days)"
        where += " [automatic when watched]" if g.auto else ""
        print(f"{g.title} ({fmt.items(len(g.items))}, {fmt.size(g.size)}) {where}")
        for i in g.items:
            print(f"  {os.path.relpath(i.path, plan.folder)}{'/' if i.is_dir else ''}  [{fmt.size(i.size)}] {i.reason}")
        print()
    for path, reason in plan.skipped:
        print(f"left alone: {os.path.relpath(path, plan.folder)} ({reason})")
    for note in plan.notes:
        print(note)


def _add_rule(sentence):
    rs = sentences.parse(sentence)
    if rs.errors:
        sys.exit(f"tidy: {rs.errors[0][2]}")
    path = sentences.rules_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fresh = not os.path.exists(path)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write((sentences.EXAMPLES if fresh else "") + sentence.strip() + "\n")


def _rules(args):
    path = sentences.rules_path()
    if args.add:
        _add_rule(" ".join(args.add))
    if args.edit:
        if not os.path.exists(path):
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(sentences.EXAMPLES)
        editor = os.environ.get("VISUAL") or os.environ.get("EDITOR")
        os.execvp(editor, [editor, path]) if editor else os.execvp("xdg-open", ["xdg-open", path])
    rs = sentences.load()
    print(f"{path}:")
    if not (rs.rules or rs.watch.folders or rs.errors):
        print('  no rules yet. Add one with: tidy-cli rules --add "Put screenshots in Pictures/Screenshots"')
    for r in rs.rules:
        print(f"  {r.line:>3}  {r.text}\n       = {sentences.describe(r)}")
    if rs.watch.folders:
        w = rs.watch
        print(f"  watching {', '.join(w.folders)} every {w.interval // 60} min, "
              f"notifying after {w.threshold} new files")
    for n, line, msg in rs.errors:
        print(f"  {n:>3}  {line}\n       ! {msg}")
    return 1 if rs.errors else 0


def _watch(args):
    from . import watch
    if args.on:
        watch.enable()
        print("Watching. Tidy starts with your session and checks your watched folders now and then.")
    elif args.off:
        pid = watch.disable()
        print("Stopped watching." if pid else "Watching was not running; autostart is off.")
    elif args.status:
        pid = watch.running_pid()
        auto = watch.is_enabled()
        rs = sentences.load()
        print(f"running: {'yes (pid ' + str(pid) + ')' if pid else 'no'}, starts with session: {'yes' if auto else 'no'}")
        print(f"folders: {', '.join(rs.watch.folders) or 'none'}, every {rs.watch.interval // 60} min, "
              f"notify after {rs.watch.threshold} new files")
    else:
        return watch.run(once=args.once)


def main(argv=None):
    p = argparse.ArgumentParser(prog="tidy-cli", description="Preview and tidy a messy folder.")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("scan", help="show the plan for a folder (dry run)")
    s.add_argument("folder", nargs="?")
    s.add_argument("--apply", action="store_true", help="carry the plan out")
    sub.add_parser("history", help="list past runs")
    u = sub.add_parser("undo", help="undo a run, or single moves with --move")
    u.add_argument("run", type=int, nargs="?")
    u.add_argument("--move", type=int, action="append", default=[])
    sub.add_parser("purge", help=f"delete held items older than {HOLD_DAYS} days")
    sub.add_parser("forget", help="forget the destinations and file patterns Tidy has learned")
    r = sub.add_parser("rules", help="show your rules and how Tidy reads them")
    r.add_argument("--add", nargs="+", metavar="SENTENCE", help='add a rule, e.g. "Move PDFs to Documents"')
    r.add_argument("--edit", action="store_true", help="open the rules file in your editor")
    w = sub.add_parser("watch", help="watch folders and notify when they need tidying")
    mode = w.add_mutually_exclusive_group()
    mode.add_argument("--on", action="store_true", help="start now and with every session")
    mode.add_argument("--off", action="store_true", help="stop, and don't start with the session")
    mode.add_argument("--status", action="store_true")
    mode.add_argument("--once", action="store_true", help="check once and exit")
    args = p.parse_args(argv)
    if args.cmd == "rules":
        return _rules(args)
    if args.cmd == "watch":
        return _watch(args)
    history = History()

    if args.cmd == "scan":
        folder = args.folder or (places()[0][1] if places() else ".")
        try:
            plan = make_plan(folder, history=history)
        except Protected as e:
            sys.exit(f"tidy: {e}")
        _show_plan(plan)
        if args.apply and plan.groups:
            res = history.apply(plan)
            print(f"Moved {fmt.items(res.done)}. Undo with: tidy-cli undo {res.run}")
            for path, why in res.skipped + res.failed:
                print(f"  not moved: {path} ({why})")
    elif args.cmd == "history":
        for r in history.runs():
            when = time.strftime("%Y-%m-%d %H:%M", time.localtime(r["started"]))
            print(f"#{r['id']}  {when}  {r['folder']}  {r['active'] or 0}/{r['total']} in place")
            for m in history.moves(r["id"]):
                print(f"    {m['id']:>5} {m['status']:<9} {m['src']}")
    elif args.cmd == "undo":
        if not args.run and not args.move:
            p.error("give a run number or --move")
        res = history.undo(run=args.run, ids=args.move)
        print(f"Restored {fmt.items(res.done)}.")
        for path, why in res.failed:
            print(f"  could not restore {path}: {why}")
    elif args.cmd == "purge":
        print(f"Expired {fmt.items(history.purge())}.")
    elif args.cmd == "forget":
        print(f"Forgot {fmt.items(history.forget())}.")


if __name__ == "__main__":
    main()
