import os
import sys
import threading
import time

from PySide6.QtCore import QDateTime, QFileInfo, QLocale, QMimeDatabase, QObject, Qt, QTimer, QUrl, Signal
from PySide6.QtDBus import QDBus, QDBusConnection, QDBusMessage
from PySide6.QtGui import QAction, QDesktopServices, QFont, QIcon, QKeySequence, QPalette
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QComboBox, QDialog, QDialogButtonBox,
                               QFileDialog, QFileIconProvider, QHBoxLayout, QHeaderView, QLabel,
                               QMainWindow, QMenu, QMessageBox, QProgressBar, QPushButton, QSizePolicy,
                               QStackedWidget, QToolButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout,
                               QWidget)

from ..core import APP_ID, HOLD, HOLD_DAYS, VERSION, Cancelled, History, Protected, make_plan, places
from ..core import fmt


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


class PlanView(QWidget):
    def __init__(self, win):
        super().__init__()
        self.win = win
        self.plan = None
        self.rows = []
        self.icons = QFileIconProvider()
        layout = QVBoxLayout(self)
        self.headline = QLabel()
        font = self.headline.font()
        font.setPointSizeF(font.pointSizeF() * 1.3)
        self.headline.setFont(font)
        layout.addWidget(self.headline)
        self.summary = QLabel(wordWrap=True)
        layout.addWidget(self.summary)

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Name", "Size", "Modified", "Why"])
        self.tree.setUniformRowHeights(True)
        self.tree.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self.context_menu)
        self.tree.itemDoubleClicked.connect(self.reveal)
        self.tree.itemChanged.connect(lambda *_: self.pending.start())
        header = self.tree.header()
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        for col in (1, 2, 3):
            header.setSectionResizeMode(col, QHeaderView.ResizeToContents)
        header.setStretchLastSection(False)
        layout.addWidget(self.tree, 1)

        self.pending = QTimer(self, singleShot=True, interval=0)
        self.pending.timeout.connect(self.sync)

        buttons = QHBoxLayout()
        self.hint = dim(QLabel("Nothing moves until you click Tidy."))
        buttons.addWidget(self.hint, 1)
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
        self.rows = []
        self.tree.blockSignals(True)
        self.tree.clear()
        bold = QFont(self.tree.font())
        bold.setBold(True)
        locale = QLocale()
        for group in plan.groups:
            top = QTreeWidgetItem(self.tree, [f"{group.title} ({len(group.items)})", size(group.size), "",
                                              "Moves to Archive" if group.action != HOLD
                                              else f"Holding area, {HOLD_DAYS} days"])
            top.setToolTip(0, group.description)
            top.setToolTip(3, group.description)
            top.setFont(0, bold)
            top.setFlags(top.flags() | Qt.ItemIsUserCheckable | Qt.ItemIsAutoTristate)
            top.setTextAlignment(1, Qt.AlignRight | Qt.AlignVCenter)
            for item in group.items:
                rel = os.path.relpath(item.path, plan.folder) + ("/" if item.is_dir else "")
                when = locale.toString(time_to_qdt(item.mtime), QLocale.ShortFormat) if item.mtime else ""
                child = QTreeWidgetItem(top, [rel, size(item.size), when, item.reason])
                child.setIcon(0, self.icons.icon(QFileInfo(item.path)))
                child.setToolTip(0, item.path)
                child.setTextAlignment(1, Qt.AlignRight | Qt.AlignVCenter)
                child.setFlags(child.flags() | Qt.ItemIsUserCheckable)
                child.setCheckState(0, Qt.Checked if item.enabled and group.enabled else Qt.Unchecked)
                self.rows.append((child, item, group, top))
            top.setExpanded(len(plan.groups) == 1 or len(group.items) <= 8)
        if plan.skipped:
            top = QTreeWidgetItem(self.tree, [f"Left alone ({len(plan.skipped)})", "", "",
                                              "Protected, never touched"])
            top.setFont(0, bold)
            top.setIcon(0, icon("security-high", "emblem-locked"))
            for path, reason in plan.skipped:
                QTreeWidgetItem(top, [os.path.relpath(path, plan.folder), "", "", reason]).setDisabled(True)
        self.tree.blockSignals(False)
        self.headline.setText(f"<b>{plan.item_count:,} items → {len(plan.groups)} groups</b> "
                              f"in {tilde(plan.folder)}")
        self.sync()

    def sync(self):
        for child, item, group, top in self.rows:
            item.enabled = child.checkState(0) == Qt.Checked
            group.enabled = top.checkState(0) != Qt.Unchecked
        chosen = self.plan.selected
        moved = sum(1 for g, _ in chosen if g.action != HOLD)
        text = f"Looked at {self.plan.scanned:,} files ({size(self.plan.total_size)}). " \
               f"{size(self.plan.freeable)} can be freed"
        if moved:
            text += f", {fmt.items(moved)} archived"
        self.summary.setText(text + ".")
        self.go.setText(f"Tidy {fmt.items(len(chosen))}")
        self.go.setEnabled(bool(chosen))

    def _path(self, qitem):
        return next((item.path for child, item, _, _ in self.rows if child is qitem), None)

    def reveal(self, qitem, _col=0):
        path = self._path(qitem)
        if path:
            show_in_folder(path)

    def context_menu(self, pos):
        qitem = self.tree.itemAt(pos)
        path = qitem and self._path(qitem)
        if not path:
            return
        menu = QMenu(self)
        menu.addAction(icon("document-open-folder", "folder-open"), "Show in Folder", lambda: show_in_folder(path))
        menu.addAction(icon("document-open"), "Open", lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(path)))
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

        run_async(lambda: make_plan(path, cancel, progress), done, failed)

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
