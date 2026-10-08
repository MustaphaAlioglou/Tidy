import os
import sys
import threading
import time

from PySide6.QtCore import QDateTime, QFileInfo, QLocale, QMimeDatabase, QObject, Qt, QTimer, QUrl, Signal
from PySide6.QtDBus import QDBus, QDBusConnection, QDBusMessage
from PySide6.QtGui import QAction, QDesktopServices, QFont, QIcon, QKeySequence, QPalette
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
                               QFileDialog, QFileIconProvider, QFormLayout, QFrame, QGridLayout, QGroupBox,
                               QHBoxLayout, QHeaderView, QLabel, QListWidget, QListWidgetItem, QMainWindow, QMenu,
                               QMessageBox, QProgressBar, QPushButton, QSizePolicy, QSpinBox, QStackedWidget,
                               QTabWidget, QToolButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from ..core import (APP_ID, HOLD, HOLD_DAYS, MOVE, VERSION, Cancelled, History, Protected, check_folder,
                    make_plan, places)
from ..core import fmt
from ..watch import WatchSettings


class Bridge(QObject):
    call = Signal(object, object)

    def __init__(self):
        super().__init__()
        self.call.connect(lambda fn, arg: fn(arg))


BRIDGE = None


def run_async(fn, done, error):
    def work():
        try:
            result = fn()
        except Exception as e:
            BRIDGE.call.emit(error, e)
        else:
            BRIDGE.call.emit(done, result)
    threading.Thread(target=work, daemon=True).start()


def size(n):
    return QLocale().formattedDataSize(n)


def icon(*names):
    for name in names:
        if QIcon.hasThemeIcon(name):
            return QIcon.fromTheme(name)
    return QIcon()


def tilde(path):
    home = os.path.expanduser("~")
    return "~" + path[len(home):] if path.startswith(home + os.sep) else path


def show_in_folder(path):
    msg = QDBusMessage.createMethodCall("org.freedesktop.FileManager1", "/org/freedesktop/FileManager1",
                                        "org.freedesktop.FileManager1", "ShowItems")
    msg.setArguments([[QUrl.fromLocalFile(path).toString()], ""])
    reply = QDBusConnection.sessionBus().call(msg, QDBus.Block, 2000)
    if reply.type() == QDBusMessage.ErrorMessage:
        QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.dirname(path)))


def dim(label):
    pal = label.palette()
    pal.setColor(QPalette.WindowText, pal.color(QPalette.PlaceholderText))
    label.setPalette(pal)
    return label


def mime_icon(name, is_dir=False):
    if is_dir:
        return icon("folder")
    return QIcon.fromTheme(QMimeDatabase().mimeTypeForFile(name, QMimeDatabase.MatchExtension).iconName(),
                           icon("text-x-generic"))


def placeholder(icon_names, title, text="", widgets=()):
    page = QWidget()
    outer = QVBoxLayout(page)
    outer.addStretch(1)
    pic = QLabel(alignment=Qt.AlignCenter)
    pic.setPixmap(icon(*icon_names).pixmap(96, 96))
    pic.setEnabled(False)
    outer.addWidget(pic)
    head = QLabel(title, alignment=Qt.AlignCenter, wordWrap=True)
    font = head.font()
    font.setPointSizeF(font.pointSizeF() * 1.5)
    head.setFont(font)
    dim(head)
    outer.addWidget(head)
    body = dim(QLabel(text, alignment=Qt.AlignCenter, wordWrap=True))
    outer.addWidget(body)
    row = QHBoxLayout()
    row.addStretch(1)
    for w in widgets:
        row.addWidget(w)
    row.addStretch(1)
    outer.addSpacing(12)
    outer.addLayout(row)
    outer.addStretch(2)
    page.title, page.body = head, body
    return page


class PlanTree(QTreeWidget):
    dropped = Signal(object, object)

    def __init__(self):
        super().__init__()
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self.setDragDropMode(QAbstractItemView.DragDrop)
        self.setDefaultDropAction(Qt.MoveAction)

    def group_at(self, pos):
        it = self.itemAt(pos)
        top = it and (it.parent() or it)
        return top.data(0, Qt.UserRole) if top else None

    def dragEnterEvent(self, e):
        e.acceptProposedAction() if e.source() is self else e.ignore()

    def dragMoveEvent(self, e):
        if e.source() is self and self.group_at(e.position().toPoint()):
            e.acceptProposedAction()
        else:
            e.ignore()

    def dropEvent(self, e):
        key = self.group_at(e.position().toPoint())
        if e.source() is self and key:
            e.setDropAction(Qt.CopyAction)
            e.accept()
            moved = [i for i in self.selectedItems() if i.parent()]
            QTimer.singleShot(0, lambda: self.dropped.emit(moved, key))
        else:
            e.ignore()


class PlanView(QWidget):
    def __init__(self, win):
        super().__init__()
        self.win = win
        self.plan = None
        self.rows = {}
        self.expanded = None
        self.icons = QFileIconProvider()
        layout = QVBoxLayout(self)
        self.headline = QLabel()
        font = self.headline.font()
        font.setPointSizeF(font.pointSizeF() * 1.3)
        self.headline.setFont(font)
        layout.addWidget(self.headline)

        self.tabs = QTabWidget(documentMode=True)
        layout.addWidget(self.tabs, 1)
        plan_tab = QWidget()
        plan_layout = QVBoxLayout(plan_tab)
        plan_layout.setContentsMargins(0, 6, 0, 0)

        compare = QFrame(frameShape=QFrame.StyledPanel)
        grid = QGridLayout(compare)
        self.before = QLabel()
        self.after = QLabel()
        arrow = QLabel()
        arrow.setPixmap(icon("go-next", "arrow-right").pixmap(32, 32))
        grid.addWidget(self.before, 0, 0)
        grid.addWidget(arrow, 0, 1)
        grid.addWidget(self.after, 0, 2)
        self.changes = dim(QLabel(wordWrap=True))
        grid.addWidget(self.changes, 0, 3)
        grid.setColumnStretch(3, 1)
        grid.setHorizontalSpacing(18)
        plan_layout.addWidget(compare)

        self.tree = PlanTree()
        self.tree.setHeaderLabels(["Name", "Size", "Modified", "Why"])
        self.tree.setUniformRowHeights(True)
        self.tree.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self.context_menu)
        self.tree.itemDoubleClicked.connect(self.reveal)
        self.tree.itemChanged.connect(lambda *_: self.pending.start())
        self.tree.dropped.connect(self.move)
        header = self.tree.header()
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        for col in (1, 2, 3):
            header.setSectionResizeMode(col, QHeaderView.ResizeToContents)
        header.setStretchLastSection(False)
        plan_layout.addWidget(self.tree, 1)
        self.tabs.addTab(plan_tab, icon("view-list-tree", "view-list-details"), "Plan")

        self.big = QTreeWidget(rootIsDecorated=False, uniformRowHeights=True)
        self.big.setHeaderLabels(["Name", "Size", "Share"])
        self.big.header().setSectionResizeMode(0, QHeaderView.Stretch)
        self.big.header().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.big.header().setSectionResizeMode(2, QHeaderView.Fixed)
        self.big.header().resizeSection(2, 200)
        self.big.header().setStretchLastSection(False)
        self.big.itemDoubleClicked.connect(lambda it, _c: show_in_folder(it.data(0, Qt.UserRole)))
        self.tabs.addTab(self.big, icon("drive-harddisk", "folder"), "What's Big")

        self.pending = QTimer(self, singleShot=True, interval=0)
        self.pending.timeout.connect(self.sync)

        buttons = QHBoxLayout()
        buttons.addWidget(dim(QLabel("Nothing moves until you click Tidy. Drag files between groups to change "
                                     "what happens to them.")), 1)
        discard = QPushButton(icon("dialog-cancel"), "Discard")
        discard.clicked.connect(win.go_home)
        buttons.addWidget(discard)
        self.go = QPushButton(icon("dialog-ok-apply"), "Tidy")
        self.go.setDefault(True)
        self.go.clicked.connect(lambda: win.apply(self.plan))
        buttons.addWidget(self.go)
        layout.addLayout(buttons)

    def show_plan(self, plan):
        self.plan = plan
        self.expanded = None
        self.tabs.setCurrentIndex(0)
        self.headline.setText(f"<b>{plan.item_count:,} items → {len(plan.groups)} groups</b> "
                              f"in {tilde(plan.folder)}")
        self.fill_big()
        self.rebuild()

    def where(self, group):
        if group.action == HOLD:
            return f"Holding area, {HOLD_DAYS} days"
        name = os.path.basename(group.dest) if os.path.dirname(group.dest) == self.plan.folder else tilde(group.dest)
        return f"Moves to {name}"

    def rebuild(self):
        plan = self.plan
        if self.expanded is not None:
            for i in range(self.tree.topLevelItemCount()):
                top = self.tree.topLevelItem(i)
                key = top.data(0, Qt.UserRole)
                if key:
                    (self.expanded.add if top.isExpanded() else self.expanded.discard)(key)
        else:
            self.expanded = {g.key for g in plan.groups if len(plan.groups) == 1 or len(g.items) <= 8}
        scroll = self.tree.verticalScrollBar().value()
        self.rows = {}
        self.tree.blockSignals(True)
        self.tree.clear()
        bold = QFont(self.tree.font())
        bold.setBold(True)
        locale = QLocale()
        for group in plan.groups:
            top = QTreeWidgetItem(self.tree, [f"{group.title} ({len(group.items)})", size(group.size), "",
                                              self.where(group)])
            top.setData(0, Qt.UserRole, group.key)
            top.setToolTip(0, group.description)
            top.setToolTip(3, group.description)
            top.setFont(0, bold)
            top.setFlags((top.flags() | Qt.ItemIsUserCheckable | Qt.ItemIsAutoTristate) & ~Qt.ItemIsDragEnabled)
            top.setTextAlignment(1, Qt.AlignRight | Qt.AlignVCenter)
            for item in group.items:
                rel = os.path.relpath(item.path, plan.folder) + ("/" if item.is_dir else "")
                when = locale.toString(time_to_qdt(item.mtime), QLocale.ShortFormat) if item.mtime else ""
                child = QTreeWidgetItem(top, [rel, size(item.size), when, item.reason])
                child.setIcon(0, self.icons.icon(QFileInfo(item.path)))
                child.setToolTip(0, item.path)
                child.setTextAlignment(1, Qt.AlignRight | Qt.AlignVCenter)
                child.setFlags((child.flags() | Qt.ItemIsUserCheckable) & ~Qt.ItemIsDropEnabled)
                child.setCheckState(0, Qt.Checked if item.enabled and group.enabled else Qt.Unchecked)
                self.rows[id(child)] = (child, item, group, top)
            if not group.items:
                top.setCheckState(0, Qt.Unchecked)
            top.setExpanded(group.key in self.expanded)
        if plan.skipped:
            top = QTreeWidgetItem(self.tree, [f"Left alone ({len(plan.skipped)})", "", "",
                                              "Protected, never touched"])
            top.setFont(0, bold)
            top.setIcon(0, icon("security-high", "emblem-locked"))
            top.setFlags(top.flags() & ~(Qt.ItemIsDragEnabled | Qt.ItemIsDropEnabled))
            for path, reason in plan.skipped:
                QTreeWidgetItem(top, [os.path.relpath(path, plan.folder), "", "", reason]).setDisabled(True)
        self.tree.blockSignals(False)
        QTimer.singleShot(0, lambda: self.tree.verticalScrollBar().setValue(scroll))
        self.sync()

    def fill_big(self):
        self.big.clear()
        total = self.plan.total_size or 1
        for path, n, is_dir in self.plan.largest:
            row = QTreeWidgetItem(self.big, [os.path.basename(path) + ("/" if is_dir else ""), size(n)])
            row.setIcon(0, self.icons.icon(QFileInfo(path)))
            row.setData(0, Qt.UserRole, path)
            row.setToolTip(0, path)
            row.setTextAlignment(1, Qt.AlignRight | Qt.AlignVCenter)
            bar = QProgressBar(maximum=1000, value=round(n / total * 1000), format=f"{n / total:.0%}")
            self.big.setItemWidget(row, 2, bar)

    def sync(self):
        for child, item, group, top in self.rows.values():
            item.enabled = child.checkState(0) == Qt.Checked
            group.enabled = top.checkState(0) != Qt.Unchecked
        p = self.plan.preview()
        self.before.setText(f"<small>BEFORE</small><br><big><b>{fmt.items(p.before_count)}</b></big>"
                            f"<br>{size(p.before_size)}")
        self.after.setText(f"<small>AFTER</small><br><big><b>{fmt.items(p.after_count)}</b></big>"
                           f"<br>{size(p.after_size)}")
        parts = []
        for d in p.destinations:
            name = os.path.basename(d.path) if os.path.dirname(d.path) == self.plan.folder else tilde(d.path)
            parts.append(f"{name}{' (new)' if d.new else ''} +{d.count}")
        if p.held_count:
            parts.append(f"holding area {fmt.items(p.held_count)}, {size(p.held_size)}")
        self.changes.setText(" · ".join(parts))
        chosen = self.plan.selected
        self.go.setText(f"Tidy {fmt.items(len(chosen))}")
        self.go.setEnabled(bool(chosen))

    def _row(self, qitem):
        return self.rows.get(id(qitem)) if qitem else None

    def reveal(self, qitem, _col=0):
        row = self._row(qitem)
        if row:
            show_in_folder(row[1].path)

    def move(self, qitems, key):
        self.sync()
        target = next(g for g in self.plan.groups if g.key == key)
        for q in qitems:
            row = self._row(q)
            if row:
                self.plan.move_item(row[1], target)
        self.expanded.add(key)
        self.rebuild()

    def change_dest(self, group):
        path = QFileDialog.getExistingDirectory(self, f"Where Should {group.title} Go?", group.dest
                                                if os.path.isdir(group.dest) else self.plan.folder)
        if not path:
            return
        if os.path.realpath(path) == self.plan.folder:
            QMessageBox.information(self, "Pick Another Folder", "That is the folder being tidied.")
            return
        try:
            group.dest = check_folder(path)
        except Protected as e:
            QMessageBox.information(self, "Tidy Can't Move Files There", str(e))
            return
        self.sync()
        self.rebuild()

    def context_menu(self, pos):
        qitem = self.tree.itemAt(pos)
        if not qitem:
            return
        menu = QMenu(self)
        key = qitem.data(0, Qt.UserRole)
        group = next((g for g in self.plan.groups if g.key == key), None)
        if group and group.action == MOVE:
            menu.addAction(icon("folder-new", "folder"), "Change Destination…", lambda: self.change_dest(group))
        row = self._row(qitem)
        if row:
            path = row[1].path
            menu.addAction(icon("document-open-folder", "folder-open"), "Show in Folder", lambda: show_in_folder(path))
            menu.addAction(icon("document-open"), "Open", lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(path)))
            sub = menu.addMenu(icon("transform-move", "go-jump"), "Move to Group")
            picked = [i for i in self.tree.selectedItems() if self._row(i)] or [qitem]
            for g in self.plan.groups:
                if g is not row[2]:
                    sub.addAction(g.title, lambda k=g.key: self.move(picked, k))
        if not menu.isEmpty():
            menu.exec(self.tree.viewport().mapToGlobal(pos))


def time_to_qdt(ts):
    return QDateTime.fromSecsSinceEpoch(int(ts))


class HistoryDialog(QDialog):
    def __init__(self, win):
        super().__init__(win)
        self.win = win
        self.history = win.history
        self.setWindowTitle("Tidy History")
        self.resize(760, 520)
        layout = QVBoxLayout(self)
        note = dim(QLabel(f"Every run can be undone. Removed items are kept for {HOLD_DAYS} days."))
        layout.addWidget(note)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["When", "Folder", "Size", "Status"])
        self.tree.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.tree.header().setSectionResizeMode(1, QHeaderView.Stretch)
        self.tree.header().setStretchLastSection(False)
        for col in (0, 2, 3):
            self.tree.header().setSectionResizeMode(col, QHeaderView.ResizeToContents)
        self.tree.itemSelectionChanged.connect(self.update_buttons)
        layout.addWidget(self.tree)
        box = QDialogButtonBox(QDialogButtonBox.Close)
        self.undo_run = box.addButton("Undo Run", QDialogButtonBox.ActionRole)
        self.undo_run.setIcon(icon("edit-undo"))
        self.undo_run.clicked.connect(self.on_undo_run)
        self.put_back = box.addButton("Put Back Selected", QDialogButtonBox.ActionRole)
        self.put_back.setIcon(icon("edit-undo"))
        self.put_back.clicked.connect(self.on_put_back)
        box.rejected.connect(self.reject)
        layout.addWidget(box)
        self.reload()

    def reload(self):
        self.tree.clear()
        locale = QLocale()
        for run in self.history.runs():
            active = run["active"] or 0
            top = QTreeWidgetItem(self.tree, [
                locale.toString(time_to_qdt(run["started"]), QLocale.ShortFormat), tilde(run["folder"]),
                size(run["size"]), f"{fmt.items(active)} can be undone" if active else "Undone or expired"])
            top.setData(0, Qt.UserRole, ("run", run["id"], active))
            top.setIcon(1, icon("folder"))
            for m in self.history.moves(run["id"]):
                child = QTreeWidgetItem(top, ["", os.path.basename(m["src"]), size(m["size"]), self.describe(m)])
                child.setToolTip(1, m["src"])
                child.setData(0, Qt.UserRole, ("move", m["id"], m["status"] == "moved"))
                child.setIcon(1, mime_icon(m["src"], m["is_dir"]))
        if self.tree.topLevelItemCount():
            self.tree.topLevelItem(0).setExpanded(True)
        self.update_buttons()

    @staticmethod
    def describe(m):
        if m["status"] == "moved":
            if m["action"] == HOLD:
                return "Held until " + QLocale().toString(time_to_qdt(m["expires"]).date(), QLocale.ShortFormat)
            return f"In {tilde(os.path.dirname(m['dst']))}"
        return {"restored": "Put back", "expired": "Expired", "missing": "Missing",
                "failed": f"Not moved: {m['error'] or 'error'}"}.get(m["status"], m["status"])

    def _selected(self, kind):
        return [d for d in (i.data(0, Qt.UserRole) for i in self.tree.selectedItems()) if d[0] == kind and d[2]]

    def update_buttons(self):
        self.undo_run.setEnabled(bool(self._selected("run")))
        self.put_back.setEnabled(bool(self._selected("move")))

    def on_undo_run(self):
        runs = [d[1] for d in self._selected("run")]
        self._undo(lambda: [self.history.undo(run=r) for r in runs])

    def on_put_back(self):
        ids = [d[1] for d in self._selected("move")]
        self._undo(lambda: [self.history.undo(ids=ids)])

    def _undo(self, fn):
        self.setEnabled(False)

        def done(results):
            self.setEnabled(True)
            self.reload()
            self.win.report_undo(results)

        def failed(e):
            self.setEnabled(True)
            QMessageBox.warning(self, "Could Not Undo", str(e))
        run_async(fn, done, failed)


class WatchDialog(QDialog):
    def __init__(self, win, settings=None):
        super().__init__(win)
        self.win = win
        self.settings = settings or WatchSettings()
        self.setWindowTitle("Watch Folders")
        self.resize(560, 480)
        layout = QVBoxLayout(self)

        self.enabled = QCheckBox("Watch folders and tell me when they need tidying")
        self.enabled.setChecked(self.settings.enabled)
        self.enabled.toggled.connect(self.toggled)
        layout.addWidget(self.enabled)
        layout.addWidget(dim(QLabel("Tidy checks now and then and starts with your session. Only rules that end "
                                    "in \u201cautomatically\u201d move files without asking.", wordWrap=True)))

        box = QGroupBox("Folders")
        folders = QVBoxLayout(box)
        self.list = QListWidget()
        self.list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.list.itemSelectionChanged.connect(self.update_buttons)
        folders.addWidget(self.list)
        buttons = QHBoxLayout()
        add = QPushButton(icon("list-add", "folder-new"), "Add Folder…")
        add.clicked.connect(self.choose)
        self.remove = QPushButton(icon("list-remove", "edit-delete"), "Stop Watching")
        self.remove.clicked.connect(self.remove_selected)
        buttons.addWidget(add)
        buttons.addWidget(self.remove)
        buttons.addStretch()
        folders.addLayout(buttons)
        layout.addWidget(box)

        form = QFormLayout()
        self.threshold = QSpinBox(minimum=1, maximum=500, suffix=" new files")
        self.threshold.setValue(self.settings.threshold)
        self.threshold.valueChanged.connect(lambda v: setattr(self.settings, "threshold", v))
        self.minutes = QSpinBox(minimum=5, maximum=1440, singleStep=5, suffix=" minutes")
        self.minutes.setValue(self.settings.minutes)
        self.minutes.valueChanged.connect(lambda v: setattr(self.settings, "minutes", v))
        form.addRow("Notify after:", self.threshold)
        form.addRow("Check every:", self.minutes)
        layout.addLayout(form)

        close = QDialogButtonBox(QDialogButtonBox.Close)
        close.rejected.connect(self.reject)
        layout.addWidget(close)
        self.fill()

    def fill(self):
        self.list.clear()
        for name, path in self.settings.folders():
            missing = "" if os.path.isdir(path) else "  (not found)"
            item = QListWidgetItem(icon("folder"), tilde(path) + missing)
            item.setData(Qt.UserRole, name)
            self.list.addItem(item)
        self.update_buttons()

    def update_buttons(self):
        self.remove.setEnabled(bool(self.list.selectedItems()))

    def toggled(self, on):
        if on != self.settings.enabled:
            self.settings.enabled = on
            self.fill()

    def add_folder(self, path):
        try:
            added = self.settings.add(path)
        except Protected as e:
            QMessageBox.information(self, "Tidy Can't Watch This Folder", str(e))
            return
        if not added:
            QMessageBox.information(self, "Already Watched", f"{tilde(path)} is already watched.")
        self.fill()

    def choose(self):
        path = QFileDialog.getExistingDirectory(self, "Choose a Folder to Watch", os.path.expanduser("~"))
        if path:
            self.add_folder(path)

    def remove_selected(self):
        for item in self.list.selectedItems():
            self.settings.remove(item.data(Qt.UserRole))
        self.fill()


class MainWindow(QMainWindow):
    def __init__(self, history):
        super().__init__()
        self.history = history
        self.cancel = None
        self.last_run = None
        self.setWindowTitle("Tidy")
        self.resize(900, 640)

        bar = self.addToolBar("Main")
        bar.setObjectName("mainToolBar")
        bar.setMovable(False)
        bar.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        bar.addWidget(QLabel(" Folder: "))
        self.folders = QComboBox()
        self.folders.setMinimumContentsLength(18)
        for label, path, ic in places():
            self.folders.addItem(icon(ic, "folder"), label, path)
        self.folders.insertSeparator(self.folders.count())
        self.folders.addItem(icon("document-open-folder", "folder-open"), "Other Folder…", None)
        self.folders.activated.connect(self.folder_picked)
        bar.addWidget(self.folders)
        self.scan_action = QAction(icon("edit-find", "system-search"), "Scan", self)
        self.scan_action.setShortcut(QKeySequence.Refresh)
        self.scan_action.triggered.connect(lambda: self.scan(self.folders.currentData()))
        bar.addAction(self.scan_action)
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        bar.addWidget(spacer)
        history = QAction(icon("view-history", "document-open-recent"), "History", self)
        history.triggered.connect(lambda: HistoryDialog(self).exec())
        bar.addAction(history)
        menu = QMenu(self)
        menu.addAction(icon("view-refresh", "folder-sync"), "Watch Folders…", lambda: WatchDialog(self).exec())
        menu.addAction(icon("help-about"), "About Tidy", self.about)
        menu.addSeparator()
        quit_action = menu.addAction(icon("application-exit"), "Quit", self.close)
        quit_action.setShortcut(QKeySequence.Quit)
        self.addAction(quit_action)
        burger = QToolButton(icon=icon("application-menu", "open-menu"), popupMode=QToolButton.InstantPopup,
                             toolTip="Main Menu", toolButtonStyle=Qt.ToolButtonIconOnly)
        burger.setMenu(menu)
        bar.addWidget(burger)

        self.stack = QStackedWidget()
        self.setCentralWidget(self.stack)
        scan_button = QPushButton(icon("edit-find", "system-search"), "Scan")
        scan_button.clicked.connect(self.scan_action.trigger)
        self.home = placeholder(("edit-clear-all",), "Tidy up a folder",
                                "Pick a folder and press Scan. You get a plan first: nothing moves until you "
                                "approve it, and every change can be undone.", (scan_button,))
        self.busy_bar = QProgressBar(minimumWidth=280, textVisible=False)
        self.busy_bar.setRange(0, 0)
        stop = QPushButton(icon("dialog-cancel"), "Cancel")
        stop.clicked.connect(self.stop)
        self.busy = placeholder(("edit-find", "system-search"), "Scanning…", "", (self.busy_bar, stop))
        self.planview = PlanView(self)
        undo = QPushButton(icon("edit-undo"), "Undo")
        undo.clicked.connect(lambda: self.undo(self.last_run))
        finish = QPushButton(icon("dialog-ok"), "Done")
        finish.clicked.connect(self.go_home)
        self.done_undo = undo
        self.done = placeholder(("dialog-positive", "emblem-success", "checkmark"), "All tidy", "", (undo, finish))
        for w in (self.home, self.busy, self.planview, self.done):
            self.stack.addWidget(w)

    def go_home(self):
        self.stack.setCurrentWidget(self.home)

    def folder_picked(self, index):
        if self.folders.itemData(index) is None:
            path = QFileDialog.getExistingDirectory(self, "Choose a Folder to Tidy", os.path.expanduser("~"))
            if not path:
                self.folders.setCurrentIndex(0)
                return
            self.folders.insertItem(index, icon("folder"), os.path.basename(path) or path, path)
            self.folders.setCurrentIndex(index)
            self.scan(path)

    def select_folder(self, path):
        i = self.folders.findData(path)
        if i < 0:
            i = self.folders.count() - 2
            self.folders.insertItem(i, icon("folder"), os.path.basename(path.rstrip(os.sep)) or path, path)
        self.folders.setCurrentIndex(i)

    def stop(self):
        if self.cancel:
            self.cancel.set()
        self.go_home()

    def scan(self, path):
        if not path:
            return
        if self.cancel:
            self.cancel.set()
        cancel = self.cancel = threading.Event()
        self.busy.title.setText(f"Scanning {os.path.basename(path.rstrip(os.sep)) or path}…")
        self.busy.body.setText("Looking at file types, ages and sizes")
        self.busy_bar.setRange(0, 0)
        self.stack.setCurrentWidget(self.busy)
        self.scan_action.setEnabled(False)

        def progress(n):
            BRIDGE.call.emit(self.busy.body.setText, f"Looked at {n:,} files")

        def done(plan):
            self.scan_action.setEnabled(True)
            if cancel.is_set():
                return
            if not plan.groups:
                self.done_undo.hide()
                self.done.title.setText("Already tidy")
                self.done.body.setText(f"Looked at {plan.scanned:,} files ({size(plan.total_size)}). "
                                       "Nothing needs tidying.")
                self.stack.setCurrentWidget(self.done)
                return
            self.planview.show_plan(plan)
            self.stack.setCurrentWidget(self.planview)

        def failed(e):
            self.scan_action.setEnabled(True)
            if isinstance(e, Cancelled):
                return
            self.go_home()
            if isinstance(e, Protected):
                QMessageBox.information(self, "Tidy Leaves This Folder Alone", str(e))
            else:
                QMessageBox.warning(self, "Could Not Scan", str(e))

        run_async(lambda: make_plan(path, cancel, progress, history=self.history), done, failed)

    def apply(self, plan):
        self.busy.title.setText("Tidying…")
        self.busy.body.setText("")
        self.stack.setCurrentWidget(self.busy)

        def progress(n, total):
            BRIDGE.call.emit(lambda _: (self.busy_bar.setRange(0, total), self.busy_bar.setValue(n),
                                        self.busy.body.setText(f"{n} of {total}")), None)

        def done(res):
            self.last_run = res.run
            self.done_undo.setVisible(bool(res.run))
            self.done.title.setText("All tidy" if res.done else "Nothing was moved")
            text = (f"Moved {fmt.items(res.done)} ({size(res.size)}). Removed items stay in the holding area "
                    f"for {HOLD_DAYS} days, and History can put anything back.") if res.done else ""
            problems = res.skipped + res.failed
            if problems:
                text += "\n\nNot moved:\n" + "\n".join(f"{os.path.basename(p)}: {w}" for p, w in problems[:10])
                if len(problems) > 10:
                    text += f"\n…and {len(problems) - 10} more"
            self.done.body.setText(text)
            self.stack.setCurrentWidget(self.done)

        def failed(e):
            self.stack.setCurrentWidget(self.planview)
            QMessageBox.warning(self, "Could Not Tidy", str(e))

        run_async(lambda: self.history.apply(plan, progress), done, failed)

    def undo(self, run):
        run_async(lambda: [self.history.undo(run=run)],
                  lambda results: (self.go_home(), self.report_undo(results)),
                  lambda e: QMessageBox.warning(self, "Could Not Undo", str(e)))

    def report_undo(self, results):
        restored = sum(r.done for r in results)
        failed = [f for r in results for f in r.failed]
        msg = f"Put back {fmt.items(restored)}."
        if failed:
            msg += f" {len(failed)} could not be restored."
        self.statusBar().showMessage(msg, 8000)

    def about(self):
        QMessageBox.about(self, "About Tidy",
                          f"<h3>Tidy {VERSION}</h3><p>Tidy a messy folder: see the plan, approve it, "
                          "undo anything.</p><p>Local only. No cloud, no account, no telemetry.</p>"
                          "<p>© 2026 Mustapha Alioglou</p>")

    def closeEvent(self, event):
        if self.cancel:
            self.cancel.set()
        super().closeEvent(event)


def main(argv=None):
    global BRIDGE
    argv = argv if argv is not None else sys.argv
    app = QApplication(argv)
    app.setApplicationName("Tidy")
    app.setApplicationVersion(VERSION)
    app.setDesktopFileName(APP_ID)
    app.setWindowIcon(icon("edit-clear-all"))
    BRIDGE = Bridge()
    history = History()
    threading.Thread(target=history.purge, daemon=True).start()
    win = MainWindow(history)
    win.show()
    folder = next((a for a in argv[1:] if not a.startswith("-")), None)
    if folder:
        path = QUrl(folder).toLocalFile() if folder.startswith("file://") else os.path.abspath(folder)
        win.select_folder(path)
        win.scan(path)
    return app.exec()
