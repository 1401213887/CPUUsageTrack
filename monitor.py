"""CPU 监控引擎 - QThread 工作线程

核心设计：分级采集，极低 CPU 开销
- 总体 CPU：优先使用 Windows PDH Processor Utility（与任务管理器一致），
  fallback 到 psutil cpu_percent
- 告警路径：仅超阈值+冷却期后才遍历进程
- 停止响应：通过分片 sleep 实现 ~100ms 级别的停止响应
"""
import time
import threading

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

        将长 sleep 切分为 ≤100ms 的小片段，每片段检查 stop 信号。
        Returns:
            True 如果正常完成睡眠，False 如果被中断
        """
        slice_sec = 0.1  # 100ms 检查粒度
        remaining = duration
        while remaining > 0:
            if self._stop_event.is_set():
                return False
            wait_time = min(slice_sec, remaining)
            self._stop_event.wait(timeout=wait_time)
            remaining -= wait_time
        return True

    def run(self):
        """采集主循环

        总体 CPU 数据源优先级：
        1. Windows PDH: % Processor Utility（与任务管理器一致，反映 Turbo Boost）
        2. psutil cpu_percent（fallback，传统 % Processor Time）

        进程级 CPU 使用 psutil process_iter，但通过动态频率校正系数
        (PDH Utility / psutil Time) 对齐到 Utility 口径。
        """
        # 初始化 PDH（如果可用）
        use_pdh = False
        if _HAS_PDH:
            use_pdh = init_cpu_utility()

        # psutil 预热（始终需要，进程级 CPU 依赖它）
        psutil.cpu_percent(interval=None)
        for _p in psutil.process_iter(attrs=['cpu_percent']):
            pass

        while not self._stop_event.is_set():
            # 检查暂停状态
            self._pause_event.wait()
            if self._stop_event.is_set():
                break

            # 可中断地等待采样间隔
            if not self._interruptible_sleep(self._interval):
                break  # 被中断，退出

            # 采样总体 CPU
            # 始终调用 psutil 维护基线（进程级 CPU 依赖它）
            psu_time = psutil.cpu_percent(interval=None)
            freq_factor = 1.0  # 频率校正系数

            if use_pdh:
                pdh_utility = get_cpu_utility()
                if pdh_utility >= 0:
                    cpu_pct = pdh_utility
                    # 动态频率校正系数：Utility / Time
                    if psu_time > 0.1:
                        freq_factor = pdh_utility / psu_time
                else:
                    cpu_pct = psu_time
            else:
                cpu_pct = psu_time

            now = time.time()
            self.cpu_data_signal.emit(now, cpu_pct)

            # --- 告警路径：仅超阈值+冷却期后 ---
            if cpu_pct >= self._threshold:
                if (now - self._last_alert_time) >= self._log_cooldown:
                    self._last_alert_time = now
                    processes = self._collect_top_processes(10, freq_factor)
                    self.alert_signal.emit(cpu_pct, processes)
                else:
                    # 超阈值但在冷却期内，仍需遍历进程为下次预热
                    for _p in psutil.process_iter(attrs=['cpu_percent']):
                        pass
            else:
                # 非告警时也遍历一次进程，为下个周期预热 cpu_percent 基线
                for _p in psutil.process_iter(attrs=['cpu_percent']):
                    pass

    # 不应出现在 Top 列表中的系统伪进程
    _SKIP_NAMES = {'System Idle Process', 'Idle', 'kernel_task'}

    def _collect_top_processes(self, top_n: int = 10, freq_factor: float = 1.0) -> list:
        """收集 CPU 占用 Top N 进程

        前提：主循环每个周期都遍历过 process_iter（预热了各进程的
        cpu_percent 基线），所以这里直接读取即可获得与总体 CPU
        同一采样窗口的数据。

        Args:
            top_n: 返回 Top N 个进程
            freq_factor: 频率校正系数 = PDH Utility / psutil Time，
                         用于将进程 CPU（基于 Processor Time）校正到
                         Processor Utility 口径（与任务管理器一致）

        Returns:
            list of dict: [{'pid': int, 'name': str, 'cpu_percent': float}, ...]
        """
        import os as _os

        num_cores = psutil.cpu_count(logical=True) or 1
        procs = []
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

                # 归一化到单核百分比，并乘以频率校正系数
                normalized_pct = info['cpu_percent'] / num_cores * freq_factor
                normalized_pct = round(min(100.0, normalized_pct), 2)
                procs.append({
                    'pid': pid,
                    'name': raw_name,
                    'cpu_percent': normalized_pct
                })
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue

        # 按 CPU 占用率降序排序，取 Top N
        procs.sort(key=lambda x: x['cpu_percent'], reverse=True)
        return procs[:top_n]
