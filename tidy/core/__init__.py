from . import sentences
from .history import History
from .places import places
from .plan import HOLD, HOLD_DAYS, MOVE, Group, Item, Plan
from .rules import Settings, build_plan
from .safety import Protected, check_folder
from .scan import Cancelled, scan

APP_ID = "app.tidy.Tidy"
VERSION = "0.4.0"


def make_plan(folder, cancel=None, progress=None, settings=None, history=None, rules=None):
    folder = check_folder(folder)
    learned = history.learned() if history else None
    rules = sentences.load() if rules is None else rules
    return build_plan(scan(folder, cancel, progress), settings, cancel=cancel, learned=learned,
                      user_rules=rules.rules)
