"""配置管理模块"""
from dataclasses import dataclass, field


@dataclass
class AppConfig:
    """应用配置"""
    # 采样间隔（秒）
    interval: float = 1.0
    # 告警阈值（百分比）
    threshold: float = 90.0
    # 数据窗口大小（最多保留多少个数据点）
    max_data_points: int = 300
    # 日志最小间隔（秒），防刷机制
    log_cooldown: float = 30.0
    # 日志输出目录
    log_dir: str = "logs"


def default_config() -> AppConfig:
    """返回默认配置"""
    return AppConfig()
