import os
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
    origin: str | None = None


@dataclass
class Group:
    key: str
    title: str
    description: str
    action: str
    items: list = field(default_factory=list)
    dest: str | None = None
    enabled: bool = True
    year: int | None = None
    suggested: str | None = None

    def __post_init__(self):
        self.suggested = self.suggested or self.dest
        for i in self.items:
            i.origin = i.origin or self.key

    @property
    def size(self):
        return sum(i.size for i in self.items)

    @property
    def selected(self):
        return [i for i in self.items if i.enabled] if self.enabled else []


@dataclass
class Destination:
    path: str
    count: int
    size: int
    new: bool


@dataclass
class Preview:
    before_count: int
    before_size: int
    after_count: int
    after_size: int
    held_count: int
    held_size: int
    destinations: list


@dataclass
class Plan:
    folder: str
    scanned: int
    total_size: int
    groups: list = field(default_factory=list)
    skipped: list = field(default_factory=list)
    top_count: int = 0
    largest: list = field(default_factory=list)

    @property
    def selected(self):
        return [(g, i) for g in self.groups for i in g.selected]

    @property
    def item_count(self):
        return sum(len(g.items) for g in self.groups)

    @property
    def freeable(self):
        return sum(i.size for g in self.groups if g.action == HOLD for i in g.selected)

    def group_of(self, item):
        return next(g for g in self.groups if any(i is item for i in g.items))

    def move_item(self, item, target):
        source = self.group_of(item)
        if source is target:
            return
        source.items = [i for i in source.items if i is not item]
        target.items.append(item)
        target.items.sort(key=lambda i: -i.size)
        item.enabled = True
        target.enabled = True

    def preview(self):
        chosen = self.selected
        held = [i for g, i in chosen if g.action == HOLD]
        dests = {}
        for g, i in chosen:
            if g.action == MOVE:
                d = dests.setdefault(g.dest, [0, 0])
                d[0] += 1
                d[1] += i.size
        destinations = []
        new_here = 0
        for path, (count, size) in dests.items():
            new = not os.path.isdir(path)
            if new and os.path.dirname(path) == self.folder:
                new_here += 1
            destinations.append(Destination(path, count, size, new))
        moved_out = sum(i.size for g, i in chosen if g.action == MOVE
                        and os.path.dirname(g.dest) != self.folder)
        return Preview(
            before_count=self.top_count, before_size=self.total_size,
            after_count=self.top_count - len(chosen) + new_here,
            after_size=self.total_size - sum(i.size for i in held) - moved_out,
            held_count=len(held), held_size=sum(i.size for i in held),
            destinations=sorted(destinations, key=lambda d: -d.count))
