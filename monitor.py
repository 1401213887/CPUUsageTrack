"""CPU 监控引擎 - QThread 工作线程

核心设计：极低 CPU 开销的分级采集
- 总体 CPU：优先使用 Windows PDH Processor Utility（与任务管理器一致），
  fallback 到 psutil cpu_percent
- 常规路径（每秒）：只采集总体 CPU，不遍历任何进程（O(1) 开销）
- 告警路径（极低频）：仅超阈值+冷却期后，启动自包含的两次采样收集 Top N 进程
- 停止响应：通过 Event.wait(timeout) 实现 ~100ms 级别的停止响应
"""
import time
import threading
import os as _os

import psutil
from PyQt5.QtCore import QThread, pyqtSignal

# Windows PDH 模块（可选，非 Windows 或初始化失败时 fallback）
try:
    from win_pdh import init_cpu_utility, get_cpu_utility, cleanup_cpu_utility
    _HAS_PDH = True
except ImportError:
    _HAS_PDH = False


class CPUMonitorThread(QThread):
    """CPU 监控工作线程

    Signals:
        cpu_data_signal(float, float): (timestamp, cpu_percent)
        alert_signal(float, list): (cpu_percent, top10_processes)
    """

    # 常规数据信号：每次采集都发射，只传两个 float
    cpu_data_signal = pyqtSignal(float, float)
    # 告警信号：仅超阈值时发射，携带进程列表
    alert_signal = pyqtSignal(float, list)

    def __init__(self, parent=None):
        super().__init__(parent)
        # 采集配置（可运行时更新）
        self._interval = 1.0
        self._threshold = 90.0
        self._log_cooldown = 30.0

        # 线程控制标志
        self._stop_event = threading.Event()
        self._pause_event = threading.Event()
        self._pause_event.set()  # 初始为非暂停状态

        # 上次告警时间（防刷）
        self._last_alert_time = 0.0

    def update_config(self, interval: float, threshold: float, log_cooldown: float = 30.0):
        """运行时更新配置（从主线程调用，通过信号触发安全）"""
        self._interval = max(0.1, interval)  # 最低 100ms，保证采样窗口有意义
        self._threshold = max(1.0, min(100.0, threshold))
        self._log_cooldown = log_cooldown

    def pause(self):
        """暂停采集"""
        self._pause_event.clear()

    def resume(self):
        """恢复采集"""
        self._pause_event.set()

    def stop(self):
        """停止线程"""
        self._stop_event.set()
        self._pause_event.set()  # 唤醒可能在暂停等待的线程

    def _interruptible_sleep(self, duration: float) -> bool:
        """可中断的精确睡眠

        使用 Event.wait(timeout) 实现，被 stop 时立即返回。
        Returns:
            True 如果正常完成睡眠，False 如果被中断
        """
        # Event.wait 内部用高效的 OS 原语，无需手动分片
        # stop_event 被 set 时立即唤醒
        interrupted = self._stop_event.wait(timeout=duration)
        return not interrupted  # True = 正常超时完成, False = 被中断

    def run(self):
        """采集主循环

        总体 CPU 数据源优先级：
        1. Windows PDH: % Processor Utility（与任务管理器一致，反映 Turbo Boost）
        2. psutil cpu_percent（fallback，传统 % Processor Time）

        关键优化：常规路径零进程遍历，仅在告警时按需采集进程数据。
        """
        # 初始化 PDH（如果可用）
        use_pdh = False
        if _HAS_PDH:
            use_pdh = init_cpu_utility()

        # psutil 预热（仅 fallback 模式需要）
        if not use_pdh:
            psutil.cpu_percent(interval=None)

        while not self._stop_event.is_set():
            # 检查暂停状态
            self._pause_event.wait()
            if self._stop_event.is_set():
                break

            # 可中断地等待采样间隔
            if not self._interruptible_sleep(self._interval):
                break  # 被中断，退出

            # ── 常规路径：只采集总体 CPU，不遍历任何进程 ──
            if use_pdh:
                cpu_pct = get_cpu_utility()
                if cpu_pct < 0:
                    # PDH 偶发失败，fallback 到 psutil
                    cpu_pct = psutil.cpu_percent(interval=None)
            else:
                cpu_pct = psutil.cpu_percent(interval=None)

            now = time.time()
            self.cpu_data_signal.emit(now, cpu_pct)

            # ── 告警路径：仅超阈值+冷却期后，启动独立两次采样 ──
            if cpu_pct >= self._threshold:
                if (now - self._last_alert_time) >= self._log_cooldown:
                    self._last_alert_time = now
                    result = self._collect_top_processes(10, use_pdh)
                    if result is not None:
                        alert_cpu, processes = result
                        self.alert_signal.emit(alert_cpu, processes)

    # 不应出现在 Top 列表中的系统伪进程
    _SKIP_NAMES = {'System Idle Process', 'Idle', 'kernel_task'}

    def _collect_top_processes(self, top_n: int = 10, use_pdh: bool = False) -> tuple[float, list] | None:
        """收集 CPU 占用 Top N 进程（自包含两次采样模式）

        不依赖外部预热或校正系数。内部完成：
        1. 第一轮遍历：预热所有进程的 cpu_percent 基线 + psutil 总体基线
        2. 等待采样窗口（~1 秒，可中断）
        3. 第二轮遍历：读取真实 CPU 使用值
        4. 按比例分配：每个进程 CPU = (raw_pct / raw_sum) * total_cpu
           这保证 Top N 进程的占比关系正确，且累加值与总体一致

        Args:
            top_n: 返回 Top N 个进程
            use_pdh: 是否使用 PDH Utility 作为总体基准

        Returns:
            (total_cpu, list of dict) 或 None（被中断时返回 None）
            total_cpu 是采样窗口内的真实总体 CPU，与进程列表同一时刻
        """
        num_cores = psutil.cpu_count(logical=True) or 1

        # ── 第一轮：预热所有进程 + psutil 总体基线 ──
        psutil.cpu_percent(interval=None)
        for _p in psutil.process_iter(attrs=['cpu_percent']):
            pass

        # ── 等待采样窗口（可中断）──
        if not self._interruptible_sleep(1.0):
            return None  # 被中断

        # ── 第二轮：读取真实值 ──
        # 先获取总体值（两种口径）
        psu_time = psutil.cpu_percent(interval=None)
        total_cpu = psu_time  # fallback
        if use_pdh and _HAS_PDH:
            pdh_util = get_cpu_utility()
            if pdh_util >= 0:
                total_cpu = pdh_util

        # 收集所有进程的原始 CPU（归一化到 0-100%）
        raw_procs = []
        raw_sum = 0.0
        for proc in psutil.process_iter(attrs=['pid', 'name', 'cpu_percent', 'exe']):
            try:
                info = proc.info
                if info['cpu_percent'] is None:
                    continue

                pid = info['pid']
                if pid == 0:
                    continue

                # 多级 fallback 获取进程名，杜绝 Unknown
                raw_name = info['name'] or ''
                if not raw_name:
                    try:
                        raw_name = proc.name() or ''
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        raw_name = ''
                if not raw_name:
                    exe_path = info.get('exe', '') or ''
                    if exe_path:
                        raw_name = _os.path.basename(exe_path)
                if not raw_name:
                    raw_name = f'[PID:{pid}]'

                if raw_name in self._SKIP_NAMES:
                    continue

                # 归一化到 0-100% 单核口径
                raw_pct = info['cpu_percent'] / num_cores
                raw_sum += raw_pct
                raw_procs.append({
                    'pid': pid,
                    'name': raw_name,
                    'raw_pct': raw_pct
                })
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue

        # 按比例分配：进程 CPU = (raw_pct / raw_sum) * total_cpu
        # 这样进程累加值天然 = total_cpu，不存在乘法校正的波动问题
        procs = []
        for p in raw_procs:
            if raw_sum > 0.01:
                adjusted_pct = p['raw_pct'] / raw_sum * total_cpu
            else:
                adjusted_pct = p['raw_pct']
            procs.append({
                'pid': p['pid'],
                'name': p['name'],
                'cpu_percent': round(min(100.0, adjusted_pct), 2)
            })

        # 按 CPU 占用率降序排序，取 Top N
        procs.sort(key=lambda x: x['cpu_percent'], reverse=True)
        return total_cpu, procs[:top_n]
