import argparse
import os
import sys
import time

from .core import HOLD_DAYS, History, Protected, make_plan, places
from .core import fmt


def _show_plan(plan):
    print(f"{plan.folder}: {plan.scanned} files, {fmt.size(plan.total_size)}")
    if not plan.groups:
        print("Nothing to tidy.")
        return
    print(f"{plan.item_count} items -> {len(plan.groups)} groups, {fmt.size(plan.freeable)} can be freed\n")
    for g in plan.groups:
        print(f"{g.title} ({fmt.items(len(g.items))}, {fmt.size(g.size)})")
        for i in g.items:
            print(f"  {os.path.relpath(i.path, plan.folder)}{'/' if i.is_dir else ''}  [{fmt.size(i.size)}] {i.reason}")
        print()
    for path, reason in plan.skipped:
        print(f"left alone: {os.path.relpath(path, plan.folder)} ({reason})")


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
    args = p.parse_args(argv)
    history = History()

    if args.cmd == "scan":
        folder = args.folder or (places()[0][1] if places() else ".")
        try:
            plan = make_plan(folder)
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


if __name__ == "__main__":
    main()
