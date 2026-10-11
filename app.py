import sys
import os
import platform
import time
import threading
import psutil
import subprocess
import datetime

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QFrame, QCheckBox, QSizePolicy, QSystemTrayIcon,
    QMenu, QTableWidget, QTableWidgetItem, QHeaderView, QTextEdit,
    QAbstractItemView, QMessageBox
)
from PySide6.QtCore import Qt, QTimer, Signal, QRectF, QPointF
from PySide6.QtGui import (
    QFont, QColor, QPainter, QPen, QBrush, QConicalGradient,
    QLinearGradient, QRadialGradient, QPalette, QPainterPath, QIcon, QPixmap, QAction
)


# ---------------------------------------------------------------------------
#  Circular Gauge Widget
# ---------------------------------------------------------------------------
class CircularGauge(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._value = 0.0
        self._max_value = 100.0
        self._color = QColor(0, 180, 255)
        self._bg_color = QColor(50, 50, 60)
        self._label = ""
        self.setMinimumSize(160, 160)
        self.setMaximumSize(200, 200)

    def setValue(self, value):
        self._value = max(0, min(value, self._max_value))
        self.update()

    def setColor(self, color):
        self._color = color
        self.update()

    def setLabel(self, text):
        self._label = text
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        side = min(self.width(), self.height())
        painter.viewport()
        painter.setViewport(
            (self.width() - side) // 2, (self.height() - side) // 2, side, side
        )
        painter.setWindow(0, 0, 200, 200)

        # glow
        glow_pen = QPen(QColor(self._color.red(), self._color.green(), self._color.blue(), 40), 18, Qt.SolidLine, Qt.RoundCap)
        painter.setPen(glow_pen)
        rect_glow = QRectF(15, 15, 170, 170)
        span = int((self._value / self._max_value) * 360)
        painter.drawArc(rect_glow, 90 * 16, -span * 16)

        # background
        painter.setPen(QPen(self._bg_color, 10, Qt.SolidLine, Qt.RoundCap))
        rect_arc = QRectF(25, 25, 150, 150)
        painter.drawArc(rect_arc, 0, 360 * 16)

        # value
        gradient = QConicalGradient(100, 100, 90)
        gradient.setColorAt(0.0, self._color.lighter(130))
        gradient.setColorAt(0.5, self._color)
        gradient.setColorAt(1.0, self._color.darker(130))
        painter.setPen(QPen(QBrush(gradient), 10, Qt.SolidLine, Qt.RoundCap))
        painter.drawArc(rect_arc, 90 * 16, -span * 16)

        # text
        painter.setPen(QColor(240, 240, 250))
        painter.setFont(QFont("Segoe UI", 28, QFont.Bold))
        painter.drawText(rect_arc, Qt.AlignCenter, f"{int(self._value)}%")

        if self._label:
            painter.setPen(QColor(160, 160, 180))
            painter.setFont(QFont("Segoe UI", 10))
            sub_rect = QRectF(25, 115, 150, 40)
            painter.drawText(sub_rect, Qt.AlignCenter, self._label)
        painter.end()


# ---------------------------------------------------------------------------
#  RAM History Sparkline
# ---------------------------------------------------------------------------
class RAMHistoryWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._data: list[float] = []
        self.setMinimumHeight(80)
        self.setMaximumHeight(110)

    def set_data(self, data: list[float]):
        self._data = data
        self.update()

    def paintEvent(self, event):
        if len(self._data) < 2:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        m = 6

        painter.fillRect(self.rect(), QColor(30, 30, 44))

        painter.setPen(QPen(QColor(55, 55, 75), 1, Qt.DotLine))
        for pct in (25, 50, 75):
            y = m + (h - 2 * m) * (1 - pct / 100)
            painter.drawLine(m, int(y), w - m, int(y))

        painter.setPen(QColor(90, 90, 110))
        painter.setFont(QFont("Segoe UI", 7))
        for pct, anchor in [(75, 0), (50, 0.5), (25, 1)]:
            y = m + (h - 2 * m) * (1 - pct / 100)
            painter.drawText(2, int(y) - 2, f"{pct}%")

        n = len(self._data)
        pts = []
        for i, v in enumerate(self._data):
            x = m + (w - 2 * m) * i / (n - 1)
            y = m + (h - 2 * m) * (1 - v / 100)
            pts.append(QPointF(x, y))

        fill = QPainterPath()
        fill.moveTo(pts[0])
        for p in pts[1:]:
            fill.lineTo(p)
        fill.lineTo(pts[-1].x(), h - m)
        fill.lineTo(pts[0].x(), h - m)
        fill.closeSubpath()
        grad = QLinearGradient(0, 0, 0, h)
        grad.setColorAt(0, QColor(0, 180, 255, 90))
        grad.setColorAt(1, QColor(0, 180, 255, 5))
        painter.setPen(Qt.NoPen)
        painter.setBrush(QBrush(grad))
        painter.drawPath(fill)

        painter.setPen(QPen(QColor(0, 180, 255), 2))
        painter.setBrush(Qt.NoBrush)
        line = QPainterPath()
        line.moveTo(pts[0])
        for p in pts[1:]:
            line.lineTo(p)
        painter.drawPath(line)
        painter.end()


# ---------------------------------------------------------------------------
#  VRAM Monitor
# ---------------------------------------------------------------------------
class VRAMMonitor:
    @staticmethod
    def get_info() -> list[dict] | None:
        try:
            r = subprocess.run(
                ["nvidia-smi", "--query-gpu=name,memory.total,memory.used,memory.free,utilization.gpu,temperature.gpu",
                 "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=5,
            )
            if r.returncode != 0: return None
            gpus = []
            for line in r.stdout.strip().splitlines():
                parts = [p.strip() for p in line.split(",")]
                if len(parts) >= 6:
                    gpus.append({
                        "name": parts[0], "total_mb": float(parts[1]),
                        "used_mb": float(parts[2]), "free_mb": float(parts[3]),
                        "gpu_util": float(parts[4]), "temp_c": float(parts[5]),
                    })
            return gpus if gpus else None
        except Exception:
            return None


# ---------------------------------------------------------------------------
#  Memory Cleaner
# ---------------------------------------------------------------------------
class MemoryCleaner:
    @staticmethod
    def clean() -> int:
        before = psutil.virtual_memory().used
        if platform.system() == "Windows":
            MemoryCleaner._clean_windows()
        else:
            MemoryCleaner._clean_linux()
        time.sleep(0.3)
        after = psutil.virtual_memory().used
        return max(0, before - after)

    @staticmethod
    def _clean_windows():
        import ctypes
        kernel32 = ctypes.windll.kernel32
        psapi = ctypes.windll.psapi
        current_pid = os.getpid()
        PROCESS_SET_QUOTA = 0x0100
        PROCESS_QUERY_INFORMATION = 0x0400

        for proc in psutil.process_iter(["pid"]):
            try:
                pid = proc.info["pid"]
                if pid == current_pid: continue
                handle = kernel32.OpenProcess(PROCESS_SET_QUOTA | PROCESS_QUERY_INFORMATION, False, pid)
                if handle:
                    psapi.EmptyWorkingSet(handle)
                    kernel32.CloseHandle(handle)
            except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
                continue
        handle = kernel32.GetCurrentProcess()
        psapi.EmptyWorkingSet(handle)

    @staticmethod
    def _clean_linux():
        try:
            with open("/proc/sys/vm/drop_caches", "w") as f: f.write("3\n")
            return
        except PermissionError:
            pass
        avail = psutil.virtual_memory().available
        target = int(avail * 0.80)
        chunk_sz = 4 * 1024 * 1024
        blobs: list[bytearray] = []
        allocated = 0
        try:
            while allocated < target:
                blobs.append(bytearray(chunk_sz))
                allocated += chunk_sz
        except MemoryError:
            pass
        blobs.clear()


# ---------------------------------------------------------------------------
#  Main Window
# ---------------------------------------------------------------------------
class MainWindow(QMainWindow):
    _cleaning_done = Signal(int)
    _log_signal = Signal(str)

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Memory Optimizer  —  RAM & VRAM")
        self.setMinimumSize(680, 850)
        self.resize(680, 850)

        self._cleaning = False
        self._ram_history: list[float] = []
        self._max_history = 60

        self._build_ui()
        self._apply_global_style()
        self._setup_tray()

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._refresh)
        self._timer.start(2000)

        self._cleaning_done.connect(self._on_clean_done)
        self._log_signal.connect(self._append_log)

        self._refresh()

    # ------------------------------------------------------------------ UI
    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        main_vbox = QVBoxLayout(central)
        main_vbox.setSpacing(12)
        main_vbox.setContentsMargins(22, 22, 22, 22)

        # Header
        hdr = QLabel("⚡ Memory Optimizer")
        hdr.setFont(QFont("Segoe UI", 24, QFont.Bold))
        hdr.setStyleSheet("color:#E0E4F0;")
        main_vbox.addWidget(hdr)

        # System Info Bar
        self.lbl_sysinfo = QLabel()
        self.lbl_sysinfo.setStyleSheet("color:#8899AA; font-size:11px; margin-bottom:8px;")
        main_vbox.addWidget(self.lbl_sysinfo)

        # Gauges Row (RAM + VRAM side by side)
        gauges_row = QHBoxLayout()
        
        # RAM Card
        ram_card = self._card()
        rl = QVBoxLayout(ram_card)
        rl.addWidget(self.ram_gauge := CircularGauge(), alignment=Qt.AlignCenter)
        self.ram_gauge.setLabel("RAM")
        self.lbl_ram_total = self._stat_label()
        self.lbl_ram_used = self._stat_label()
        self.lbl_ram_free = self._stat_label()
        for w in (self.lbl_ram_total, self.lbl_ram_used, self.lbl_ram_free):
            rl.addWidget(w)
        rl.addStretch()
        gauges_row.addWidget(ram_card, 1)

        # VRAM Card
        vram_card = self._card()
        vl = QVBoxLayout(vram_card)
        vl.addWidget(self.vram_gauge := CircularGauge(), alignment=Qt.AlignCenter)
        self.vram_gauge.setLabel("VRAM")
        self.vram_gauge.setColor(QColor(170, 0, 255))
        self.lbl_gpu_name = self._stat_label()
        self.lbl_vram_total = self._stat_label()
        self.lbl_vram_used = self._stat_label()
        self.lbl_vram_free = self._stat_label()
        self.lbl_vram_extra = self._stat_label()
        for w in (self.lbl_gpu_name, self.lbl_vram_total, self.lbl_vram_used, self.lbl_vram_free, self.lbl_vram_extra):
            vl.addWidget(w)
        vl.addStretch()
        gauges_row.addWidget(vram_card, 1)

        main_vbox.addLayout(gauges_row)

        # History Card
        hist_card = self._card()
        hl = QVBoxLayout(hist_card)
        hl.addWidget(QLabel("  📈 RAM Usage History"))
        self.history_w = RAMHistoryWidget()
        hl.addWidget(self.history_w)
        main_vbox.addWidget(hist_card)

        # Top Processes Card
        proc_card = self._card()
        proc_layout = QVBoxLayout(proc_card)
        proc_header = QHBoxLayout()
        proc_header.addWidget(QLabel("  🔍 Top RAM Processes"))
        proc_header.addStretch()
        self.btn_kill = QPushButton("End Task")
        self.btn_kill.setStyleSheet("""
            QPushButton { background:#D43A3A; color:white; border:none; border-radius:4px; padding:4px 12px; font-weight:bold; }
            QPushButton:hover { background:#E85C5C; }
            QPushButton:disabled { background:#555; color:#888; }
        """)
        self.btn_kill.clicked.connect(self._kill_process)
        proc_header.addWidget(self.btn_kill)
        proc_layout.addLayout(proc_header)

        self.proc_table = QTableWidget(0, 3)
        self.proc_table.setHorizontalHeaderLabels(["PID", "Name", "RAM"])
        self.proc_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.proc_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.proc_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.proc_table.verticalHeader().setVisible(False)
        self.proc_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.proc_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.proc_table.setFixedHeight(160)
        self.proc_table.setStyleSheet("""
            QTableWidget { background: #2A2A3C; border: none; color: #C4C8D8; font-size: 12px; }
            QHeaderView::section { background: #23233A; color: #8888AA; border: none; padding: 4px; font-weight: bold; }
            QTableWidget::item:selected { background: #3D3D5C; }
        """)
        proc_layout.addWidget(self.proc_table)
        main_vbox.addWidget(proc_card)

        # Clean Log Card
        log_card = self._card()
        log_layout = QVBoxLayout(log_card)
        log_layout.addWidget(QLabel("  📋 Cleaning Log"))
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setFixedHeight(80)
        self.log_text.setStyleSheet("background:#23233A; border:none; color:#AAB; font-size:11px; padding:4px;")
        log_layout.addWidget(self.log_text)
        main_vbox.addWidget(log_card)

        # Clean Button Row
        brow = QHBoxLayout()
        self.btn_clean = QPushButton("🧹  Clean RAM")
        self.btn_clean.setFont(QFont("Segoe UI", 14, QFont.Bold))
        self.btn_clean.setMinimumHeight(54)
        self.btn_clean.setCursor(Qt.PointingHandCursor)
        self.btn_clean.setStyleSheet("""
            QPushButton{background:#0078D4;color:#fff;border:none;border-radius:10px;padding:10px 28px}
            QPushButton:hover{background:#1A8AE8}
            QPushButton:pressed{background:#005A9E}
            QPushButton:disabled{background:#444;color:#888}
        """)
        self.btn_clean.clicked.connect(self._start_clean)
        brow.addWidget(self.btn_clean, 1)

        self.cb_auto = QCheckBox("Auto-clean\nwhen > 80%")
        self.cb_auto.setStyleSheet("color:#AAA;font-size:12px;spacing:6px")
        brow.addWidget(self.cb_auto)
        main_vbox.addLayout(brow)

        # Status
        self.lbl_status = QLabel("Ready")
        self.lbl_status.setStyleSheet("color:#777;font-size:11px;margin-top:4px")
        main_vbox.addWidget(self.lbl_status)

    # helpers
    @staticmethod
    def _card() -> QFrame:
        f = QFrame()
        f.setStyleSheet("QFrame{background:#2A2A3C;border-radius:14px;padding:10px}")
        return f

    @staticmethod
    def _stat_label() -> QLabel:
        l = QLabel()
        l.setFont(QFont("Segoe UI", 11))
        l.setStyleSheet("color:#C4C8D8;")
        return l

    # ---------------------------------------------------------------- Tray
    def _setup_tray(self):
        # Create a simple icon programmatically
        pixmap = QPixmap(32, 32)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setBrush(QBrush(QColor(0, 120, 212)))
        painter.setPen(Qt.NoPen)
        painter.drawRoundedRect(2, 2, 28, 28, 8, 8)
        painter.setPen(QColor("white"))
        painter.setFont(QFont("Segoe UI", 16, QFont.Bold))
        painter.drawText(QRectF(2, 2, 28, 28), Qt.AlignCenter, "M")
        painter.end()
        icon = QIcon(pixmap)

        self.tray_icon = QSystemTrayIcon(icon, self)
        tray_menu = QMenu()
        show_action = tray_menu.addAction("Show Optimizer")
        clean_action = tray_menu.addAction("Clean RAM Now")
        quit_action = tray_menu.addAction("Quit")

        show_action.triggered.connect(self.showNormal)
        clean_action.triggered.connect(self._start_clean)
        quit_action.triggered.connect(self._quit_app)

        self.tray_icon.setContextMenu(tray_menu)
        self.tray_icon.activated.connect(self._tray_activated)
        self.tray_icon.show()

    def _tray_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self.showNormal()

    def closeEvent(self, event):
        # Minimize to tray instead of closing
        event.ignore()
        self.hide()
        self.tray_icon.showMessage("Memory Optimizer", "Running in the background. Double-click to open.", QSystemTrayIcon.MessageIcon.Information, 2000)

    def _quit_app(self):
        self.tray_icon.hide()
        QApplication.quit()

    # -------------------------------------------------------------- Refresh
    def _refresh(self):
        mem = psutil.virtual_memory()
        pct = mem.percent

        # Color coding
        col = QColor(255, 55, 55) if pct > 85 else (QColor(255, 180, 0) if pct > 70 else QColor(0, 180, 255))
        self.ram_gauge.setColor(col)
        self.ram_gauge.setValue(pct)
        self.lbl_ram_total.setText(f"📦 Total:     {self._fmt(mem.total)}")
        self.lbl_ram_used.setText(f"📊 Used:      {self._fmt(mem.used)}")
        self.lbl_ram_free.setText(f"✅ Available: {self._fmt(mem.available)}")

        # History
        self._ram_history.append(pct)
        if len(self._ram_history) > self._max_history:
            self._ram_history.pop(0)
        self.history_w.set_data(self._ram_history)

        # VRAM
        gpus = VRAMMonitor.get_info()
        if gpus:
            g = gpus[0]
            vpct = g["used_mb"] / g["total_mb"] * 100
            vc = QColor(255, 55, 55) if vpct > 85 else (QColor(255, 180, 0) if vpct > 70 else QColor(170, 0, 255))
            self.vram_gauge.setColor(vc)
            self.vram_gauge.setValue(vpct)
            self.lbl_gpu_name.setText(f"🖥️ {g['name']}")
            self.lbl_vram_total.setText(f"📦 Total:  {g['total_mb']:.0f} MB")
            self.lbl_vram_used.setText(f"📊 Used:   {g['used_mb']:.0f} MB")
            self.lbl_vram_free.setText(f"✅ Free:   {g['free_mb']:.0f} MB")
            self.lbl_vram_extra.setText(f"⚡ GPU: {g['gpu_util']:.0f}% | 🌡️ {g['temp_c']:.0f}°C")
        else:
            self.vram_gauge.setValue(0)
            self.lbl_gpu_name.setText("🖥️ No NVIDIA GPU")
            self.lbl_vram_total.setText("📦 Total:  —")
            self.lbl_vram_used.setText("📊 Used:   —")
            self.lbl_vram_free.setText("✅ Free:   —")
            self.lbl_vram_extra.setText("")

        # Top Processes
        self._update_processes()

        # System Info
        cpu_pct = psutil.cpu_percent()
        boot_time = datetime.datetime.fromtimestamp(psutil.boot_time())
        uptime = datetime.datetime.now() - boot_time
        uptime_str = f"{uptime.days}d {uptime.seconds // 3600}h {(uptime.seconds // 60) % 60}m"
        self.lbl_sysinfo.setText(
            f"💻 {platform.node()}  |  🪟 {platform.system()} {platform.release()}  |  "
            f"🧠 CPU: {cpu_pct}%  |  ⏱️ Uptime: {uptime_str}"
        )

        # Auto-clean
        if self.cb_auto.isChecked() and pct > 80 and not self._cleaning:
            self._start_clean()

    # --------------------------------------------------------- Processes
    def _update_processes(self):
        procs = []
        for p in psutil.process_iter(['pid', 'name', 'memory_percent']):
            try:
                procs.append((p.info['pid'], p.info['name'], p.info['memory_percent']))
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        
        # Sort by memory, take top 7
        procs.sort(key=lambda x: x[2], reverse=True)
        top = procs[:7]

        self.proc_table.setRowCount(len(top))
        for i, (pid, name, mem) in enumerate(top):
            self.proc_table.setItem(i, 0, QTableWidgetItem(str(pid)))
            self.proc_table.setItem(i, 1, QTableWidgetItem(name))
            self.proc_table.setItem(i, 2, QTableWidgetItem(f"{mem:.1f} %"))

    def _kill_process(self):
        selected = self.proc_table.selectedItems()
        if not selected:
            QMessageBox.warning(self, "No Selection", "Please select a process to end.")
            return
        
        row = selected[0].row()
        pid = int(self.proc_table.item(row, 0).text())
        name = self.proc_table.item(row, 1).text()

        reply = QMessageBox.question(self, "Confirm End Task", 
                                     f"Are you sure you want to end PID {pid} ({name})?\nThis may cause data loss.",
                                     QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply == QMessageBox.Yes:
            try:
                p = psutil.Process(pid)
                p.terminate()
                self._log_signal.emit(f"Terminated process {name} (PID: {pid})")
                self._refresh()
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Failed to terminate process:\n{str(e)}")

    # --------------------------------------------------------------- Clean
    def _start_clean(self):
        if self._cleaning: return
        self._cleaning = True
        self.btn_clean.setEnabled(False)
        self.btn_clean.setText("⏳  Cleaning…")
        self.lbl_status.setText("Cleaning RAM – please wait …")
        self.lbl_status.setStyleSheet("color:#FFB800;font-size:11px;margin-top:4px")

        def _work():
            freed = MemoryCleaner.clean()
            time.sleep(0.4)
            self._cleaning_done.emit(freed)

        threading.Thread(target=_work, daemon=True).start()

    def _on_clean_done(self, freed: int):
        self._cleaning = False
        self.btn_clean.setEnabled(True)
        self.btn_clean.setText("🧹  Clean RAM")
        txt = self._fmt(freed) if freed else "0 B"
        self.lbl_status.setText(f"✅  Done — freed {txt}")
        self.lbl_status.setStyleSheet("color:#00CC66;font-size:11px;margin-top:4px")
        self._log_signal.emit(f"Cleaned {txt} at {datetime.datetime.now().strftime('%H:%M:%S')}")
        self.tray_icon.showMessage("Cleaning Complete", f"Freed {txt} of RAM.", QSystemTrayIcon.MessageIcon.Information, 2000)
        self._refresh()

    def _append_log(self, text):
        self.log_text.append(f"• {text}")
        self.log_text.verticalScrollBar().setValue(self.log_text.verticalScrollBar().maximum())

    # -------------------------------------------------------------- Util
    @staticmethod
    def _fmt(b: float) -> str:
        for u in ("B", "KB", "MB", "GB", "TB"):
            if b < 1024: return f"{b:.1f} {u}"
            b /= 1024
        return f"{b:.1f} PB"

    def _apply_global_style(self):
        self.setStyleSheet("""
            QMainWindow{background:#1E1E2E}
            QCheckBox{color:#C0C0C0;spacing:6px}
            QCheckBox::indicator{width:18px;height:18px;border-radius:4px;border:2px solid #555;background:#333}
            QCheckBox::indicator:checked{background:#0078D4;border-color:#0078D4}
            QLabel{color:#C4C8D8}
        """)


def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setQuitOnLastWindowClosed(False)  # Keep running in tray

    p = QPalette()
    p.setColor(QPalette.Window, QColor(30, 30, 46))
    p.setColor(QPalette.WindowText, QColor(205, 214, 244))
    p.setColor(QPalette.Base, QColor(30, 30, 46))
    p.setColor(QPalette.Text, QColor(205, 214, 244))
    p.setColor(QPalette.Button, QColor(45, 45, 58))
    p.setColor(QPalette.ButtonText, QColor(205, 214, 244))
    p.setColor(QPalette.Highlight, QColor(0, 120, 212))
    app.setPalette(p)

    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()