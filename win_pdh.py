"""Windows Performance Counter 采集器

通过 PDH API 读取 Windows Performance Counter，获取与任务管理器一致的 CPU 数据。

关键区别：
- psutil 使用 NtQuerySystemInformation → cpu_times → 计算 % Processor Time
- 任务管理器 (Win10 1903+) 使用 % Processor Utility，能反映 CPU 频率变化（Turbo Boost）
- 在高核心数 + 变频 CPU 上，Utility 可能比 Time 高 3~20 倍

本模块提供 PDH 查询封装，monitor.py 可直接调用。
"""
import ctypes
import ctypes.wintypes as wintypes
import sys

# PDH 常量
PDH_FMT_DOUBLE = 0x00000200
PDH_MORE_DATA = 0x800007D2

# 加载 pdh.dll
try:
    _pdh = ctypes.windll.pdh
except OSError:
    _pdh = None


class PDH_FMT_COUNTERVALUE(ctypes.Structure):
    _fields_ = [
        ("CStatus", wintypes.DWORD),
        ("doubleValue", ctypes.c_double),
    ]


class PdhQuery:
    """PDH Performance Counter 查询封装

    用法：
        query = PdhQuery(r'\\Processor Information(_Total)\\% Processor Utility')
        query.open()
        # ... 等待采样间隔 ...
        value = query.collect()  # 返回 float
        query.close()
    """

    def __init__(self, counter_path: str):
        self._counter_path = counter_path
        self._query_handle = wintypes.HANDLE()
        self._counter_handle = wintypes.HANDLE()
        self._opened = False

    def open(self) -> bool:
        """打开 PDH 查询，添加计数器

        Returns:
            True 如果成功，False 如果 PDH 不可用或计数器路径无效
        """
        if _pdh is None:
            return False

        # PdhOpenQueryW
        status = _pdh.PdhOpenQueryW(None, 0, ctypes.byref(self._query_handle))
        if status != 0:
            return False

        # PdhAddEnglishCounterW（使用英文路径，不受系统语言影响）
        status = _pdh.PdhAddEnglishCounterW(
            self._query_handle,
            self._counter_path,
            0,
            ctypes.byref(self._counter_handle)
        )
        if status != 0:
            _pdh.PdhCloseQuery(self._query_handle)
            return False

        # 首次 collect 建立基线（返回值无意义）
        _pdh.PdhCollectQueryData(self._query_handle)
        self._opened = True
        return True

    def collect(self) -> float:
        """采集一次数据

        调用前需要等待至少一个采样间隔。
        Returns:
            float: 计数器值，失败返回 -1.0
        """
        if not self._opened:
            return -1.0

        status = _pdh.PdhCollectQueryData(self._query_handle)
        if status != 0:
            return -1.0

        value = PDH_FMT_COUNTERVALUE()
        status = _pdh.PdhGetFormattedCounterValue(
            self._counter_handle,
            PDH_FMT_DOUBLE,
            None,
            ctypes.byref(value)
        )
        if status != 0:
            return -1.0

        return value.doubleValue

    def close(self):
        """关闭 PDH 查询，释放资源"""
        if self._opened and _pdh is not None:
            _pdh.PdhCloseQuery(self._query_handle)
            self._opened = False

    def __del__(self):
        self.close()


# ─── 便捷接口 ────────────────────────────────────────────────

_cpu_utility_query: PdhQuery | None = None


def init_cpu_utility() -> bool:
    """初始化 CPU Utility 计数器（进程生命周期调用一次）

    Returns:
        True 如果成功（可以用 get_cpu_utility()），
        False 如果失败（应 fallback 到 psutil）
    """
    global _cpu_utility_query
    if _cpu_utility_query is not None:
        return True

    q = PdhQuery(r'\Processor Information(_Total)\% Processor Utility')
    if q.open():
        _cpu_utility_query = q
        return True
    return False


def get_cpu_utility() -> float:
    """获取总体 CPU Utility 值（与任务管理器一致）

    需要先调用 init_cpu_utility()。
    每次调用之间需要间隔 ≥ 采样周期（通常 1 秒），否则返回 0。

    Returns:
        float: CPU Utility 百分比 (0~100)，失败返回 -1.0
    """
    if _cpu_utility_query is None:
        return -1.0
    raw = _cpu_utility_query.collect()
    if raw < 0:
        return -1.0
    # Processor Utility 可能超过 100%（Turbo Boost），截断到 100
    return min(100.0, max(0.0, raw))


def cleanup_cpu_utility():
    """清理资源"""
    global _cpu_utility_query
    if _cpu_utility_query is not None:
        _cpu_utility_query.close()
        _cpu_utility_query = None
