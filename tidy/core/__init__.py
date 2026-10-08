from .history import History
from .places import places
from .plan import HOLD, HOLD_DAYS, MOVE, Group, Item, Plan
from .rules import Settings, build_plan
from .safety import Protected, check_folder
from .scan import Cancelled, scan

APP_ID = "app.tidy.Tidy"
VERSION = "0.2.0"


def make_plan(folder, cancel=None, progress=None, settings=None):
    folder = check_folder(folder)
    return build_plan(scan(folder, cancel, progress), settings, cancel=cancel)
