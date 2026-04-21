"""Markdown 告警日志生成器

超阈值时生成 .md 日志，记录 CPU 使用率和 Top 10 进程信息。
内置防刷机制（由 monitor.py 控制冷却期）。
"""
import os
from datetime import datetime


class AlertLogger:
    """告警日志生成器"""

    def __init__(self, log_dir: str = "logs"):
        self._log_dir = log_dir
        os.makedirs(self._log_dir, exist_ok=True)

    def write_alert(self, cpu_percent: float, processes: list) -> str:
        """生成告警日志文件

        Args:
            cpu_percent: 当前总体 CPU 使用率
            processes: Top 10 进程列表 [{'pid', 'name', 'cpu_percent'}, ...]

        Returns:
            str: 生成的日志文件路径
        """
        now = datetime.now()
        filename = f"cpu_alert_{now.strftime('%Y%m%d_%H%M%S')}.md"
        filepath = os.path.join(self._log_dir, filename)

        lines = [
            f"# CPU 告警日志",
            f"",
            f"- **时间**: {now.strftime('%Y-%m-%d %H:%M:%S')}",
            f"- **CPU 使用率**: {cpu_percent:.2f}%",
            f"- **告警阈值**: 已超过设定阈值",
            f"",
            f"## CPU 占用率 Top 10 进程",
            f"",
            f"| 排名 | 进程名 | PID | CPU 占用率 |",
            f"|------|--------|-----|-----------|",
        ]

        for i, proc in enumerate(processes, 1):
            lines.append(
                f"| {i} | {proc['name']} | {proc['pid']} | {proc['cpu_percent']:.2f}% |"
            )

        if not processes:
            lines.append("| - | 无可用进程数据 | - | - |")

        lines.append("")
        lines.append("---")
        lines.append(f"*由 CPU Usage Track 自动生成*")
        lines.append("")

        with open(filepath, 'w', encoding='utf-8') as f:
            f.write('\n'.join(lines))

        return filepath

    def write_snapshot(self, cpu_percent: float, processes: list) -> str:
        """生成 CPU 进程快照日志文件

        Args:
            cpu_percent: 当前总体 CPU 使用率
            processes: Top N 进程列表 [{'pid', 'name', 'cpu_percent'}, ...]

        Returns:
            str: 生成的日志文件路径
        """
        now = datetime.now()
        filename = f"cpu_snapshot_{now.strftime('%Y%m%d_%H%M%S')}.md"
        filepath = os.path.join(self._log_dir, filename)

        lines = [
            f"# CPU 进程快照",
            f"",
            f"- **时间**: {now.strftime('%Y-%m-%d %H:%M:%S')}",
            f"- **CPU 使用率**: {cpu_percent:.2f}%",
            f"",
            f"## CPU 占用率 Top {len(processes)} 进程",
            f"",
            f"| 排名 | 进程名 | PID | CPU 占用率 |",
            f"|------|--------|-----|-----------|",
        ]

        for i, proc in enumerate(processes, 1):
            lines.append(
                f"| {i} | {proc['name']} | {proc['pid']} | {proc['cpu_percent']:.2f}% |"
            )

        if not processes:
            lines.append("| - | 无可用进程数据 | - | - |")

        lines.append("")
        lines.append("---")
        lines.append(f"*由 CPU Usage Track 手动快照生成*")
        lines.append("")

        with open(filepath, 'w', encoding='utf-8') as f:
            f.write('\n'.join(lines))

        return filepath
