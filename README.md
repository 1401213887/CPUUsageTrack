# CPU Usage Track

一款轻量级 Windows CPU 实时监控桌面应用。基于 PyQt5 深色主题界面，使用 Windows PDH Processor Utility 提供与任务管理器一致的 CPU 数据，自身 CPU 占用极低（< 0.5%）。

![Python](https://img.shields.io/badge/Python-3.10+-blue) ![PyQt5](https://img.shields.io/badge/PyQt5-5.15-green) ![Platform](https://img.shields.io/badge/Platform-Windows-0078D4) ![License](https://img.shields.io/badge/License-MIT-yellow)

---

## ✨ 功能特性

- 🖥️ **实时 CPU 曲线** — pyqtgraph 绘制，最多保留 300 个数据点（约 5 分钟 @1s 采样）
- 🔔 **智能告警** — CPU 超阈值时自动抓取 Top 10 进程，生成 Markdown 告警日志
- 📋 **手动快照** — 一键抓取 Top 20 进程 CPU 占用，支持中文进程描述名
- 📊 **数据导出** — 监控数据一键保存为 CSV 文件
- ⚡ **极低开销** — 分级采集策略，常规路径 CPU 占用 ≈ 0.05%–0.15%
- 🎯 **数据精准** — Windows PDH Processor Utility，与任务管理器完全一致
- 🎨 **深色主题** — GitHub Dark 风格界面，长时间使用不刺眼
- ⏸️ **暂停/恢复** — 随时中断和继续监控，运行时长精确扣除暂停时间

---

## 🛠️ 技术架构

```
主线程（PyQt5 事件循环）              工作线程（QThread）
┌──────────────────────┐           ┌──────────────────────┐
│  MainWindow          │           │  CPUMonitorThread     │
│  ├── 曲线更新 (numpy) │  ◄─────── │  ├── PDH Utility 采集 │  常规路径：O(1)
│  ├── 状态栏更新       │  Signal   │  │  (或 psutil 降级)  │
│  ├── 告警列表         │  ◄─────── │  └── Top N 进程采集   │  告警路径：按需
│  └── 日志生成         │  Signal   │                      │
├──────────────────────┤           └──────────────────────┘
│  _SnapshotThread     │
│  └── Top 20 快照采集  │  独立线程，手动触发
└──────────────────────┘
```

### CPU 数据源

| 数据 | 来源 | 说明 |
|------|------|------|
| **总体 CPU** | Windows PDH `% Processor Utility` | 与任务管理器一致，反映 Turbo Boost 频率变化 |
| **进程 CPU** | psutil + 按比例分配 | `进程 CPU = (raw_pct / raw_sum) × total_cpu`，保证进程累加 = 总体值 |
| **降级方案** | psutil `cpu_percent` | PDH 不可用时自动降级（非 Windows / 初始化失败） |

### 为什么不直接用 psutil？

psutil 使用 `% Processor Time`（基于 CPU 时间片），而 Windows 任务管理器使用 `% Processor Utility`（基于 CPU 频率）。在高核心数 + 变频 CPU 上，两者差距可达 **3~20 倍**。本项目通过 `win_pdh.py` 直接调用 Windows PDH API，确保数据与任务管理器一致。

---

## 📦 安装

### 环境要求

- **Python** 3.10+
- **系统** Windows 10 1903+ / Windows 11（PDH Processor Utility 需要此版本）
- 非 Windows 系统可运行，但会降级到 psutil 数据源

### 安装步骤

```bash
# 克隆项目
git clone https://github.com/1401213887/CPUUsageTrack.git
cd CPUUsageTrack

# 安装依赖
pip install -r requirements.txt
```

### 依赖清单

| 包名 | 最低版本 | 用途 |
|------|---------|------|
| PyQt5 | 5.15 | GUI 框架 |
| pyqtgraph | 0.13 | 实时曲线绘制 |
| psutil | 5.9 | 进程信息采集 |
| numpy | — | 高性能数据缓冲区 |

---

## 🚀 启动

```bash
python main.py
```

### 打包为 exe

```bash
build.bat
```

输出路径：`dist/CPUUsageTrack.exe`（单文件，无需安装 Python）

---

## 📐 项目结构

```
CPUUsageTrack/
├── main.py           # 应用入口，高 DPI 支持
├── app.py            # 主窗口 UI + 快照线程 + 控制逻辑
├── monitor.py        # CPU 监控工作线程（QThread）
├── win_pdh.py        # Windows PDH Processor Utility 模块（ctypes，零外部依赖）
├── logger.py         # Markdown 告警/快照日志生成器
├── config.py         # 配置数据类
├── build.bat         # PyInstaller 打包脚本
├── requirements.txt  # Python 依赖
├── USER_MANUAL.md    # 操作手册
└── logs/             # 告警/快照日志输出目录（自动创建）
```

---

## ⚙️ 默认配置

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `interval` | 1.0 秒 | 采样间隔 |
| `threshold` | 90.0% | 告警阈值 |
| `max_data_points` | 300 | 曲线最多保留数据点数 |
| `log_cooldown` | 30.0 秒 | 告警最小间隔（防刷） |
| `log_dir` | `logs` | 日志输出目录 |

如需修改默认值，编辑 `config.py` 中的 `AppConfig` 数据类。

---

## 📖 文档

- **[操作手册 (USER_MANUAL.md)](USER_MANUAL.md)** — 完整的使用指南，含界面说明、操作流程、日志格式、FAQ

---

## 📄 License

MIT

---

*CPU Usage Track — 轻量、精准、低开销的 Windows CPU 监控工具*
