"""CPU Usage Track - 主窗口

深色主题 PyQt5 桌面应用，pyqtgraph 实时曲线，
控制面板 + 配置区 + 告警日志列表。
"""
import csv
import time
from datetime import datetime

import numpy as np

from PyQt5.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QSpinBox, QLineEdit, QListWidget, QListWidgetItem,
    QFileDialog, QFrame, QSizePolicy, QApplication
)
from PyQt5.QtCore import Qt, QTimer, QThread, pyqtSignal
from PyQt5.QtGui import QFont, QColor, QIcon

import pyqtgraph as pg

from config import AppConfig, default_config
from monitor import CPUMonitorThread
from logger import AlertLogger

# Windows PDH 模块（可选）
try:
    from win_pdh import init_cpu_utility, get_cpu_utility
    _HAS_PDH = True
except ImportError:
    _HAS_PDH = False


# ─── 颜色常量 ─────────────────────────────────────────────
BG_DARK = "#0D1117"
BG_MID = "#161B22"
BG_CARD = "#1C2333"
BG_BORDER = "#21262D"
TEXT_PRIMARY = "#E6EDF3"
TEXT_SECONDARY = "#8B949E"
TEXT_DIM = "#484F58"
COLOR_PRIMARY = "#00D4FF"
COLOR_GREEN = "#3FB950"
COLOR_RED = "#F85149"
COLOR_ORANGE = "#D29922"
COLOR_BLUE = "#0078D4"


# ─── 全局 QSS 样式 ────────────────────────────────────────
GLOBAL_QSS = f"""
QMainWindow {{
    background-color: {BG_DARK};
}}
QWidget {{
    color: {TEXT_PRIMARY};
    font-family: "Segoe UI", "Microsoft YaHei", sans-serif;
    font-size: 13px;
}}
QLabel {{
    background: transparent;
}}
QPushButton {{
    border: 1px solid {BG_BORDER};
    border-radius: 6px;
    padding: 8px 20px;
    font-weight: 600;
    font-size: 13px;
    min-width: 80px;
}}
QPushButton:hover {{
    border-color: {COLOR_PRIMARY};
}}
QPushButton:disabled {{
    background-color: {BG_BORDER};
    color: {TEXT_DIM};
    border-color: {BG_BORDER};
}}
QSpinBox {{
    background-color: {BG_CARD};
    border: 1px solid {BG_BORDER};
    border-radius: 4px;
    padding: 4px 8px;
    color: {TEXT_PRIMARY};
    min-width: 60px;
}}
QSpinBox:focus {{
    border-color: {COLOR_PRIMARY};
}}
QSpinBox::up-button, QSpinBox::down-button {{
    background-color: {BG_BORDER};
    border: none;
    width: 16px;
}}
QSpinBox::up-button:hover, QSpinBox::down-button:hover {{
    background-color: {COLOR_PRIMARY};
}}
QListWidget {{
    background-color: {BG_MID};
    border: 1px solid {BG_BORDER};
    border-radius: 4px;
    outline: none;
}}
QListWidget::item {{
    padding: 6px 10px;
    border-bottom: 1px solid {BG_BORDER};
}}
QListWidget::item:hover {{
    background-color: {BG_CARD};
}}
"""


class _SnapshotThread(QThread):
    """后台线程：采集 CPU Top 20 进程快照

    psutil 的进程 cpu_percent 需要两次调用才有有效值，
    中间需要 sleep ~1 秒。用 QThread + pyqtSignal 避免阻塞主线程。
    """
    finished_signal = pyqtSignal(float, list)

    # 不应出现在 Top 列表中的系统伪进程
    _SKIP_NAMES = {'System Idle Process', 'Idle', 'kernel_task'}

    @staticmethod
    def _get_file_description(exe_path: str) -> str:
        """读取 exe 文件的 FileDescription（中文进程描述名）

        使用 ctypes 调 Windows Version API，零外部依赖。
        失败时返回空字符串。
        """
        if not exe_path:
            return ''
        try:
            import ctypes
            from ctypes import wintypes

            version_dll = ctypes.windll.version

            # GetFileVersionInfoSizeW
            size = version_dll.GetFileVersionInfoSizeW(exe_path, None)
            if not size:
                return ''

            # GetFileVersionInfoW
            buf = ctypes.create_string_buffer(size)
            if not version_dll.GetFileVersionInfoW(exe_path, 0, size, buf):
                return ''

            # VerQueryValueW - 先查翻译表
            p_translate = ctypes.c_void_p()
            translate_len = wintypes.UINT()
            if not version_dll.VerQueryValueW(
                buf, r'\VarFileInfo\Translation',
                ctypes.byref(p_translate), ctypes.byref(translate_len)
            ):
                return ''

            if translate_len.value < 4:
                return ''

            # 读取 language + codepage
            lang = ctypes.cast(p_translate, ctypes.POINTER(wintypes.WORD))[0]
            codepage = ctypes.cast(p_translate, ctypes.POINTER(wintypes.WORD))[1]

            # VerQueryValueW - 查 FileDescription
            sub_block = f'\\StringFileInfo\\{lang:04x}{codepage:04x}\\FileDescription'
            p_desc = ctypes.c_wchar_p()
            desc_len = wintypes.UINT()
            if not version_dll.VerQueryValueW(
                buf, sub_block,
                ctypes.byref(p_desc), ctypes.byref(desc_len)
            ):
                return ''

            return p_desc.value.strip() if p_desc.value else ''
        except Exception:
            return ''

    def run(self):
        import psutil as _psutil
        import time as _time
        import os as _os

        num_cores = _psutil.cpu_count(logical=True) or 1

        # 初始化 PDH（用于总体 CPU，与任务管理器一致）
        use_pdh = False
        if _HAS_PDH:
            use_pdh = init_cpu_utility()

        # ── 第一轮：预热，收集进程快照 ──
        _psutil.cpu_percent(interval=None)

        proc_snapshot = {}  # pid -> (proc, name, exe_path)
        for proc in _psutil.process_iter():
            try:
                pid = proc.pid
                if pid == 0:
                    continue
                proc.cpu_percent()
                try:
                    raw_name = proc.name() or ''
                except (_psutil.NoSuchProcess, _psutil.AccessDenied):
                    raw_name = ''
                try:
                    exe_path = proc.exe() or ''
                except (_psutil.NoSuchProcess, _psutil.AccessDenied):
                    exe_path = ''
                proc_snapshot[pid] = (proc, raw_name, exe_path)
            except (_psutil.NoSuchProcess, _psutil.AccessDenied, _psutil.ZombieProcess):
                continue

        # ── 等待采样窗口 ──
        _time.sleep(1.0)

        # ── 第二轮：采集真实值 ──
        # 总体 CPU：优先 PDH Utility（与任务管理器一致）
        psu_time = _psutil.cpu_percent(interval=None)

        if use_pdh:
            cpu_pct = get_cpu_utility()
            if cpu_pct < 0:
                cpu_pct = psu_time
        else:
            cpu_pct = psu_time

        # 先收集所有进程的原始 CPU 值
        raw_procs = []
        raw_sum = 0.0
        for pid, (proc, raw_name, exe_path) in proc_snapshot.items():
            try:
                pct = proc.cpu_percent()
                if pct is None:
                    continue

                name = raw_name
                if not name and exe_path:
                    name = _os.path.basename(exe_path)
                if not name:
                    name = f'[PID:{pid}]'

                if name in self._SKIP_NAMES:
                    continue

                raw_pct = pct / num_cores
                raw_sum += raw_pct

                display_name = self._get_file_description(exe_path) or name
                raw_procs.append({
                    'pid': pid,
                    'name': display_name,
                    'raw_pct': raw_pct
                })
            except (_psutil.NoSuchProcess, _psutil.AccessDenied, _psutil.ZombieProcess):
                continue

        # 按比例分配：进程 CPU = (raw_pct / raw_sum) * total_cpu
        # 保证进程累加值 = 总体 CPU，不存在 freq_factor 波动
        procs = []
        for p in raw_procs:
            if raw_sum > 0.01:
                adjusted_pct = p['raw_pct'] / raw_sum * cpu_pct
            else:
                adjusted_pct = p['raw_pct']
            procs.append({
                'pid': p['pid'],
                'name': p['name'],
                'cpu_percent': round(min(100.0, adjusted_pct), 2)
            })

        procs.sort(key=lambda x: x['cpu_percent'], reverse=True)
        self.finished_signal.emit(cpu_pct, procs[:20])


class MainWindow(QMainWindow):
    """主窗口"""

    def __init__(self):
        super().__init__()
        self._config = default_config()
        self._monitor_thread = None
        self._alert_logger = AlertLogger(self._config.log_dir)

        # 数据存储（预分配 numpy 环形缓冲区，避免每秒 deque→list 转换）
        self._max_points = self._config.max_data_points
        self._ts_buf = np.empty(self._max_points, dtype=np.float64)
        self._cpu_buf = np.empty(self._max_points, dtype=np.float64)
        self._buf_count = 0  # 当前缓冲区中的有效数据点数
        self._start_time = None
        self._alert_count = 0
        self._is_running = False
        self._is_paused = False
        self._paused_duration = 0.0  # 暂停期间累积的时长（秒）
        self._pause_start = None     # 本次暂停开始时间
        self._last_cpu_color = None  # CPU 显示颜色缓存，避免每秒重设样式

        # 运行时长定时器
        self._duration_timer = QTimer(self)
        self._duration_timer.setInterval(1000)
        self._duration_timer.timeout.connect(self._update_duration)

        self._init_ui()
        self._apply_styles()
        self._update_button_states()

    def _init_ui(self):
        """构建 UI 布局"""
        self.setWindowTitle("CPU Usage Track")
        self.setMinimumSize(1000, 700)
        self.resize(1000, 700)

        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(16, 12, 16, 12)
        main_layout.setSpacing(10)

        # ── 区块1：顶部标题与数据概览 ──
        header = self._create_header()
        main_layout.addWidget(header)

        # ── 区块2：实时曲线图 ──
        self._chart = self._create_chart()
        main_layout.addWidget(self._chart, stretch=6)

        # ── 区块3：告警日志列表 ──
        alert_section = self._create_alert_section()
        main_layout.addWidget(alert_section, stretch=2)

        # ── 区块4：底部控制面板 ──
        control = self._create_control_panel()
        main_layout.addWidget(control)

    # ─── 区块1：顶部标题与数据卡片 ─────────────────────────

    def _create_header(self) -> QWidget:
        header = QFrame()
        header.setStyleSheet(f"background-color: {BG_MID}; border-radius: 8px;")
        header.setFixedHeight(80)
        layout = QHBoxLayout(header)
        layout.setContentsMargins(20, 0, 20, 0)

        # 标题
        title = QLabel("CPU Usage Track")
        title.setFont(QFont("Segoe UI", 22, QFont.Bold))
        title.setStyleSheet(f"color: {TEXT_PRIMARY};")
        layout.addWidget(title)

        layout.addStretch()

        # 数据卡片
        self._cpu_label = self._make_stat_card("CPU", "0.0%")
        self._duration_label = self._make_stat_card("运行时长", "00:00:00")
        self._alert_label = self._make_stat_card("告警次数", "0")

        layout.addWidget(self._cpu_label['frame'])
        layout.addWidget(self._duration_label['frame'])
        layout.addWidget(self._alert_label['frame'])

        return header

    def _make_stat_card(self, title: str, value: str) -> dict:
        frame = QFrame()
        frame.setFixedSize(140, 56)
        frame.setStyleSheet(
            f"background-color: {BG_CARD}; border: 1px solid {BG_BORDER}; border-radius: 8px;"
        )
        vbox = QVBoxLayout(frame)
        vbox.setContentsMargins(10, 4, 10, 4)
        vbox.setSpacing(0)

        lbl_title = QLabel(title)
        lbl_title.setFont(QFont("Segoe UI", 9))
        lbl_title.setStyleSheet(f"color: {TEXT_SECONDARY}; border: none;")
        lbl_title.setAlignment(Qt.AlignCenter)

        lbl_value = QLabel(value)
        lbl_value.setFont(QFont("Segoe UI", 18, QFont.Bold))
        lbl_value.setStyleSheet(f"color: {COLOR_PRIMARY}; border: none;")
        lbl_value.setAlignment(Qt.AlignCenter)

        vbox.addWidget(lbl_title)
        vbox.addWidget(lbl_value)
        return {'frame': frame, 'title': lbl_title, 'value': lbl_value}

    # ─── 区块2：实时曲线图 ─────────────────────────────────

    def _create_chart(self) -> pg.PlotWidget:
        # 配置 pyqtgraph 低开销模式
        pg.setConfigOptions(antialias=False, useOpenGL=False)

        chart = pg.PlotWidget()
        chart.setBackground(BG_DARK)
        chart.showGrid(x=True, y=True, alpha=0.15)
        chart.setYRange(0, 100, padding=0.02)
        chart.setXRange(0, 60, padding=0)          # 初始 X 范围，避免无数据时出现负数
        chart.enableAutoRange(axis='x', enable=False)  # 禁用 X 轴自动缩放，手动管理
        chart.setMouseEnabled(x=False, y=False)
        chart.hideButtons()

        # 坐标轴样式
        for axis_name in ['left', 'bottom']:
            axis = chart.getAxis(axis_name)
            axis.setPen(pg.mkPen(color=BG_BORDER))
            axis.setTextPen(pg.mkPen(color=TEXT_SECONDARY))
            axis.setStyle(tickFont=QFont("Segoe UI", 9))

        chart.getAxis('left').setLabel('CPU %', color=TEXT_SECONDARY)
        chart.getAxis('bottom').setLabel('时间', color=TEXT_SECONDARY)

        # 曲线（启用 clipToView 减少不可见数据点的渲染开销）
        pen = pg.mkPen(color=COLOR_PRIMARY, width=2)
        self._curve = chart.plot([], [], pen=pen, clipToView=True, skipFiniteCheck=True)

        # 填充基线（预创建，避免每次 _on_cpu_data 新建对象导致内存泄漏）
        self._baseline = pg.PlotDataItem([0], [0])

        # 填充区域（半透明）
        self._fill = pg.FillBetweenItem(
            self._curve,
            self._baseline,
            brush=pg.mkBrush(0, 212, 255, 30)
        )
        chart.addItem(self._fill)

        # 阈值线（可拖动，范围 1~100）
        self._threshold_line = pg.InfiniteLine(
            pos=self._config.threshold,
            angle=0,
            movable=True,
            bounds=[1, 100],
            pen=pg.mkPen(color=COLOR_RED, width=1, style=Qt.DashLine),
            hoverPen=pg.mkPen(color=COLOR_RED, width=2, style=Qt.DashLine),
            label=f'阈值 {self._config.threshold:.0f}%',
            labelOpts={'color': COLOR_RED, 'position': 0.95, 'fill': (13, 17, 23, 180)}
        )
        self._threshold_line.sigPositionChanged.connect(self._on_threshold_dragging)
        self._threshold_line.sigPositionChangeFinished.connect(self._on_threshold_drag_finished)
        chart.addItem(self._threshold_line)

        return chart

    # ─── 区块3：告警日志列表 ────────────────────────────────

    def _create_alert_section(self) -> QWidget:
        container = QFrame()
        container.setStyleSheet(f"background-color: transparent;")
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        label = QLabel("告警日志")
        label.setFont(QFont("Segoe UI", 12, QFont.Bold))
        label.setStyleSheet(f"color: {TEXT_SECONDARY};")
        layout.addWidget(label)

        self._alert_list = QListWidget()
        self._alert_list.setMinimumHeight(80)
        self._alert_list.itemDoubleClicked.connect(self._on_alert_item_double_clicked)
        # 占位提示
        self._alert_placeholder = QLabel("暂无告警记录")
        self._alert_placeholder.setAlignment(Qt.AlignCenter)
        self._alert_placeholder.setStyleSheet(f"color: {TEXT_DIM}; font-size: 12px;")

        layout.addWidget(self._alert_list)
        layout.addWidget(self._alert_placeholder)
        self._alert_placeholder.setVisible(True)
        self._alert_list.setVisible(True)

        return container

    # ─── 区块4：底部控制面板 ────────────────────────────────

    def _create_control_panel(self) -> QWidget:
        panel = QFrame()
        panel.setFixedHeight(60)
        panel.setStyleSheet(
            f"background-color: {BG_MID}; border-radius: 8px;"
        )
        layout = QHBoxLayout(panel)
        layout.setContentsMargins(16, 0, 16, 0)

        # ── 按钮组 ──
        self._btn_start = QPushButton("▶ 开始")
        self._btn_start.setStyleSheet(
            f"background-color: {COLOR_GREEN}; color: white; border: none;"
        )
        self._btn_start.clicked.connect(self._on_start)

        self._btn_pause = QPushButton("⏸ 中断")
        self._btn_pause.setStyleSheet(
            f"background-color: {COLOR_ORANGE}; color: white; border: none;"
        )
        self._btn_pause.clicked.connect(self._on_pause_resume)

        self._btn_save = QPushButton("💾 保存")
        self._btn_save.setStyleSheet(
            f"background-color: {COLOR_BLUE}; color: white; border: none;"
        )
        self._btn_save.clicked.connect(self._on_save)

        self._btn_snapshot = QPushButton("📋 快照")
        self._btn_snapshot.setStyleSheet(
            f"background-color: #6E40C9; color: white; border: none;"
        )
        self._btn_snapshot.clicked.connect(self._on_snapshot)

        self._btn_stop = QPushButton("⏹ 停止")
        self._btn_stop.setStyleSheet(
            f"background-color: {COLOR_RED}; color: white; border: none;"
        )
        self._btn_stop.clicked.connect(self._on_stop)

        for btn in [self._btn_start, self._btn_pause, self._btn_save, self._btn_snapshot, self._btn_stop]:
            layout.addWidget(btn)

        layout.addStretch()

        # ── 配置区 ──
        config_label1 = QLabel("采样间隔:")
        config_label1.setStyleSheet(f"color: {TEXT_SECONDARY}; font-size: 12px;")
        self._edit_interval = QLineEdit()
        self._edit_interval.setFixedWidth(80)
        self._edit_interval.setText(f"{self._config.interval}")
        self._edit_interval.setPlaceholderText("秒")
        self._edit_interval.setAlignment(Qt.AlignCenter)
        self._edit_interval.setStyleSheet(
            f"background-color: {BG_CARD}; border: 1px solid {BG_BORDER}; "
            f"border-radius: 4px; padding: 4px 8px; color: {TEXT_PRIMARY};"
        )
        self._edit_interval.returnPressed.connect(self._on_interval_confirmed)

        config_label2 = QLabel("告警阈值:")
        config_label2.setStyleSheet(f"color: {TEXT_SECONDARY}; font-size: 12px;")
        self._spin_threshold = QSpinBox()
        self._spin_threshold.setRange(1, 100)
        self._spin_threshold.setValue(int(self._config.threshold))
        self._spin_threshold.setSuffix(" %")
        self._spin_threshold.valueChanged.connect(self._on_threshold_changed)

        layout.addWidget(config_label1)
        layout.addWidget(self._edit_interval)
        layout.addSpacing(12)
        layout.addWidget(config_label2)
        layout.addWidget(self._spin_threshold)

        return panel

    # ─── 样式 ──────────────────────────────────────────────

    def _apply_styles(self):
        self.setStyleSheet(GLOBAL_QSS)

    # ─── 按钮状态管理 ──────────────────────────────────────

    def _update_button_states(self):
        if not self._is_running:
            # 停止状态
            self._btn_start.setEnabled(True)
            self._btn_pause.setEnabled(False)
            self._btn_save.setEnabled(self._buf_count > 0)
            self._btn_stop.setEnabled(False)
        elif self._is_paused:
            # 暂停状态
            self._btn_start.setEnabled(False)
            self._btn_pause.setEnabled(True)
            self._btn_pause.setText("▶ 恢复")
            self._btn_save.setEnabled(True)
            self._btn_stop.setEnabled(True)
        else:
            # 运行中
            self._btn_start.setEnabled(False)
            self._btn_pause.setEnabled(True)
            self._btn_pause.setText("⏸ 中断")
            self._btn_save.setEnabled(True)
            self._btn_stop.setEnabled(True)

    # ─── 控制操作 ──────────────────────────────────────────

    def _on_start(self):
        """开始监控"""
        self._buf_count = 0  # 重置缓冲区（无需清空数组，count 控制有效范围）
        self._alert_count = 0
        self._alert_label['value'].setText("0")
        self._start_time = time.time()
        self._is_running = True
        self._is_paused = False
        self._paused_duration = 0.0
        self._pause_start = None

        # 创建并启动工作线程
        self._monitor_thread = CPUMonitorThread()
        self._monitor_thread.update_config(
            self._config.interval,
            self._config.threshold,
            self._config.log_cooldown
        )
        self._monitor_thread.cpu_data_signal.connect(self._on_cpu_data)
        self._monitor_thread.alert_signal.connect(self._on_alert)
        self._monitor_thread.start()

        self._duration_timer.start()
        self._update_button_states()

    def _on_pause_resume(self):
        """中断/恢复"""
        if not self._monitor_thread:
            return
        if self._is_paused:
            # 恢复：累加本次暂停时长
            if self._pause_start is not None:
                self._paused_duration += time.time() - self._pause_start
                self._pause_start = None
            self._monitor_thread.resume()
            self._is_paused = False
            self._duration_timer.start()
        else:
            # 暂停：记录暂停开始时间
            self._pause_start = time.time()
            self._monitor_thread.pause()
            self._is_paused = True
            self._duration_timer.stop()
        self._update_button_states()

    def _on_save(self):
        """保存数据为 CSV"""
        if self._buf_count == 0:
            return
        filepath, _ = QFileDialog.getSaveFileName(
            self, "保存监控数据", f"cpu_data_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
            "CSV 文件 (*.csv)"
        )
        if filepath:
            count = self._buf_count
            with open(filepath, 'w', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)
                writer.writerow(['timestamp', 'datetime', 'cpu_percent'])
                for i in range(count):
                    ts = self._ts_buf[i]
                    cpu = self._cpu_buf[i]
                    dt = datetime.fromtimestamp(ts).strftime('%Y-%m-%d %H:%M:%S')
                    writer.writerow([f"{ts:.3f}", dt, f"{cpu:.1f}"])

    def _on_snapshot(self):
        """手动快照：抓取 Top 20 进程并写入 .md 文件

        进程的 cpu_percent 需要两次采样才有有效值：
        用 _SnapshotThread (QThread + pyqtSignal) 在后台执行，不阻塞 UI。
        """
        # 禁用按钮防重复点击
        self._btn_snapshot.setEnabled(False)
        self._btn_snapshot.setText("📋 采集中…")

        self._snapshot_thread = _SnapshotThread()
        self._snapshot_thread.finished_signal.connect(self._finish_snapshot)
        self._snapshot_thread.start()

    def _finish_snapshot(self, cpu_pct: float, top20: list):
        """快照完成回调（主线程，由 _SnapshotThread 信号触发）"""
        # 写入 .md 文件
        filepath = self._alert_logger.write_snapshot(cpu_pct, top20)
        filename = filepath.split('/')[-1].split('\\')[-1]

        # 在告警列表中显示反馈
        now_str = datetime.now().strftime('%H:%M:%S')
        item_text = f"📋 [{now_str}] 快照已保存 | Top {len(top20)} 进程 | {filename}"
        item = QListWidgetItem(item_text)
        item.setForeground(QColor("#6E40C9"))
        item.setData(Qt.UserRole, filepath)
        self._alert_list.insertItem(0, item)
        self._alert_placeholder.setVisible(False)

        # 恢复按钮
        self._btn_snapshot.setEnabled(True)
        self._btn_snapshot.setText("📋 快照")

        # 清理线程引用
        self._snapshot_thread = None

    def _on_stop(self):
        """停止监控"""
        if not self._monitor_thread:
            return

        # 先断开信号，防止线程在 stop/wait 期间发射信号到已销毁的 UI
        try:
            self._monitor_thread.cpu_data_signal.disconnect(self._on_cpu_data)
        except TypeError:
            pass
        try:
            self._monitor_thread.alert_signal.disconnect(self._on_alert)
        except TypeError:
            pass

        self._monitor_thread.stop()
        self._monitor_thread.wait(3000)  # 等待线程结束，最多 3 秒
        self._monitor_thread = None

        self._duration_timer.stop()
        self._is_running = False
        self._is_paused = False
        self._start_time = None
        self._paused_duration = 0.0
        self._pause_start = None
        self._update_button_states()

    # ─── 数据接收 Slots ────────────────────────────────────

    def _on_cpu_data(self, timestamp: float, cpu_percent: float):
        """接收 CPU 数据（主线程 slot）"""
        # 写入环形缓冲区
        n = self._buf_count
        if n < self._max_points:
            self._ts_buf[n] = timestamp
            self._cpu_buf[n] = cpu_percent
            self._buf_count = n + 1
        else:
            # 缓冲区满，左移一位丢弃最老数据
            self._ts_buf[:-1] = self._ts_buf[1:]
            self._cpu_buf[:-1] = self._cpu_buf[1:]
            self._ts_buf[-1] = timestamp
            self._cpu_buf[-1] = cpu_percent

        count = self._buf_count

        # 更新曲线（至少 2 个点才绘制）
        if count > 1:
            # numpy slice，零拷贝视图
            ts_slice = self._ts_buf[:count]
            cpu_slice = self._cpu_buf[:count]

            # 向量化计算相对时间
            t0 = ts_slice[0]
            x_data = ts_slice - t0

            self._curve.setData(x_data, cpu_slice)

            # 更新基线的 x 坐标（零数组只在长度变化时重建）
            if not hasattr(self, '_baseline_zeros') or len(self._baseline_zeros) != count:
                self._baseline_zeros = np.zeros(count, dtype=np.float64)
            self._baseline.setData(x_data, self._baseline_zeros)

            # X 轴滚动窗口
            x_max = x_data[-1]
            x_start = max(0, x_max - 300)
            self._chart.setXRange(x_start, x_max + 2, padding=0)

        # 更新 CPU 数值显示（仅颜色变化时重设样式）
        self._cpu_label['value'].setText(f"{cpu_percent:.2f}%")
        if cpu_percent < 60:
            color = COLOR_GREEN
        elif cpu_percent < 90:
            color = COLOR_ORANGE
        else:
            color = COLOR_RED
        if color != self._last_cpu_color:
            self._last_cpu_color = color
            self._cpu_label['value'].setStyleSheet(f"color: {color}; border: none;")

    def _on_alert_item_double_clicked(self, item: QListWidgetItem):
        """双击告警/快照条目，用系统默认程序打开日志文件"""
        filepath = item.data(Qt.UserRole)
        if filepath:
            import os
            os.startfile(filepath)

    def _on_alert(self, cpu_percent: float, processes: list):
        """接收告警信号（主线程 slot）"""
        self._alert_count += 1
        self._alert_label['value'].setText(str(self._alert_count))
        self._alert_label['value'].setStyleSheet(f"color: {COLOR_RED}; border: none;")

        # 生成日志文件
        filepath = self._alert_logger.write_alert(cpu_percent, processes)
        filename = filepath.split('/')[-1].split('\\')[-1]

        # 添加到告警列表
        now_str = datetime.now().strftime('%H:%M:%S')
        item_text = f"⚠ [{now_str}] CPU {cpu_percent:.1f}% | {filename}"
        item = QListWidgetItem(item_text)
        item.setForeground(QColor(COLOR_RED))
        item.setData(Qt.UserRole, filepath)
        self._alert_list.insertItem(0, item)
        self._alert_placeholder.setVisible(False)

        # 最多显示 20 条
        while self._alert_list.count() > 20:
            self._alert_list.takeItem(self._alert_list.count() - 1)

    def _update_duration(self):
        """更新运行时长（扣除暂停期间）"""
        if self._start_time:
            elapsed = int(time.time() - self._start_time - self._paused_duration)
            elapsed = max(0, elapsed)
            h = elapsed // 3600
            m = (elapsed % 3600) // 60
            s = elapsed % 60
            self._duration_label['value'].setText(f"{h:02d}:{m:02d}:{s:02d}")

    def _on_interval_confirmed(self):
        """采样间隔回车确认"""
        text = self._edit_interval.text().strip()
        try:
            val = float(text)
            if val <= 0:
                raise ValueError
        except ValueError:
            # 输入无效，恢复为当前配置值
            self._edit_interval.setText(f"{self._config.interval}")
            return

        self._config.interval = val
        # 规范化显示（去掉多余的零，比如 "1.0" → "1.0"，"0.50" → "0.5"）
        self._edit_interval.setText(f"{val}")

        # 如果正在运行，动态更新工作线程配置
        if self._monitor_thread:
            self._monitor_thread.update_config(
                self._config.interval,
                self._config.threshold,
                self._config.log_cooldown
            )

    def _on_threshold_changed(self):
        """告警阈值变更（SpinBox 触发）"""
        self._config.threshold = self._spin_threshold.value()

        # 更新阈值线位置
        self._threshold_line.setValue(self._config.threshold)
        self._threshold_line.label.setFormat(f'阈值 {self._config.threshold:.0f}%')

        # 如果正在运行，动态更新工作线程配置
        if self._monitor_thread:
            self._monitor_thread.update_config(
                self._config.interval,
                self._config.threshold,
                self._config.log_cooldown
            )

    def _on_threshold_dragging(self):
        """拖动阈值线过程中实时更新 label"""
        value = round(self._threshold_line.value())
        self._threshold_line.label.setFormat(f'阈值 {value:.0f}%')

    def _on_threshold_drag_finished(self):
        """拖动阈值线结束，同步 SpinBox / config / 监控线程"""
        value = round(self._threshold_line.value())
        value = max(1, min(100, value))

        # snap 到整数位置
        self._threshold_line.setValue(value)
        self._threshold_line.label.setFormat(f'阈值 {value:.0f}%')

        # 更新 config
        self._config.threshold = float(value)

        # 同步 SpinBox（屏蔽信号防循环）
        self._spin_threshold.blockSignals(True)
        self._spin_threshold.setValue(value)
        self._spin_threshold.blockSignals(False)

        # 如果正在运行，动态更新工作线程配置
        if self._monitor_thread:
            self._monitor_thread.update_config(
                self._config.interval,
                self._config.threshold,
                self._config.log_cooldown
            )

    def closeEvent(self, event):
        """窗口关闭时优雅停止"""
        if self._monitor_thread:
            # 先断开信号，防止线程发射信号到正在销毁的 UI
            try:
                self._monitor_thread.cpu_data_signal.disconnect(self._on_cpu_data)
            except TypeError:
                pass
            try:
                self._monitor_thread.alert_signal.disconnect(self._on_alert)
            except TypeError:
                pass
            self._monitor_thread.stop()
            self._monitor_thread.wait(3000)
        event.accept()
