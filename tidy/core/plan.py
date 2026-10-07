from dataclasses import dataclass, field

HOLD = "hold"
MOVE = "move"
HOLD_DAYS = 30


@dataclass
class Item:
    path: str
    size: int
    mtime: float
    reason: str
    is_dir: bool = False
    enabled: bool = True


@dataclass
class Group:
    key: str
    title: str
    description: str
    action: str
    items: list = field(default_factory=list)
    dest: str | None = None
    enabled: bool = True

    @property
    def size(self):
        return sum(i.size for i in self.items)

    @property
    def selected(self):
        return [i for i in self.items if i.enabled] if self.enabled else []


@dataclass
class Plan:
    folder: str
    scanned: int
    total_size: int
    groups: list = field(default_factory=list)
    skipped: list = field(default_factory=list)

    @property
    def selected(self):
        return [(g, i) for g in self.groups for i in g.selected]

    @property
    def item_count(self):
        return sum(len(g.items) for g in self.groups)

    @property
    def freeable(self):
        return sum(i.size for g in self.groups if g.action == HOLD for i in g.selected)
