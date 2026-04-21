# CPUUsageTrack 代码 Review 报告

> **生成时间**：2026-04-21 16:28  
> **Review 范围**：config.py, monitor.py, logger.py, app.py, main.py  
> **技术栈**：Python 3.14 + PyQt5 5.15 + pyqtgraph 0.14 + psutil 7.2

---

## 一、代码逻辑正确性

### 🔴 严重问题（可能导致崩溃或功能异常）

#### 1. `FillBetweenItem.setCurves()` 每次创建新 `PlotDataItem` 导致内存泄漏
- **文件**：`app.py` 第 469-470 行
- **问题**：`_on_cpu_data` 每次调用时都 `pg.PlotDataItem(x_data, [0] * len(x_data))` 创建新的 baseline 对象，但旧的 `PlotDataItem` 从未被移除或释放。每秒产生一个孤立的 `PlotDataItem`，长时间运行（如 300 秒后有 300 个废弃对象）会导致内存持续增长。
- **风险**：长时间运行后内存泄漏明显，可能导致卡顿甚至 OOM。

```python
# 问题代码
baseline = pg.PlotDataItem(x_data, [0] * len(x_data))
self._fill.setCurves(self._curve, baseline)
```

- **建议**：在 `_create_chart` 中预创建一个 `_baseline` 对象，后续只调用 `_baseline.setData()` 更新数据，而不是每次都新建。

#### 2. `closeEvent` 中信号未断开
- **文件**：`app.py` 第 530-535 行
- **问题**：`closeEvent` 只调用了 `stop()` + `wait()`，但没有 `disconnect` 信号。如果线程在 `wait(3000)` 超时前还发射了信号，而主线程的 UI 已经部分销毁，可能触发段错误（segfault）。
- **风险**：窗口关闭时小概率崩溃（竞态条件）。

#### 3. `disconnect` 可能抛异常
- **文件**：`app.py` 第 442-443 行
- **问题**：`_on_stop` 中先 `wait(3000)`，再 `disconnect`。但如果线程在 `wait` 期间没有成功结束（超时），信号可能还在被使用。另外，如果 `_on_stop` 被重复调用，第二次 `disconnect` 会因信号已断开而抛 `TypeError`。
- **风险**：重复点击"停止"按钮时可能崩溃。

### 🟡 中等问题（可能导致非预期行为）

#### 4. `_on_pause_resume` 暂停期间时长计算不准
- **文件**：`app.py` 第 407-419 行 / 第 504-511 行
- **问题**：暂停时 `_duration_timer` 停了，但 `_start_time` 不变。恢复后，`_update_duration` 用 `time.time() - self._start_time` 计算时长——这包含了暂停期间的时间。所以运行时长显示会比实际监控时长偏大。
- **影响**：运行时长显示不准确（用户困惑但不崩溃）。

#### 5. `update_config` 没有线程安全保护
- **文件**：`monitor.py` 第 42-46 行
- **问题**：`update_config` 从主线程调用，直接写 `_interval`、`_threshold`、`_log_cooldown`，而工作线程的 `run()` 在读这些值。Python 的 GIL 对 float 赋值是原子的，所以不会崩溃，但理论上存在读到半更新状态的可能（比如 interval 已更新但 threshold 还没更新）。
- **影响**：实际风险很低（GIL 保护 + 单赋值语句都是原子的），但不符合线程安全最佳实践。

#### 6. 运行时修改采样间隔不立即生效
- **文件**：`monitor.py` 第 85 行
- **问题**：`_stop_event.wait(timeout=self._interval)` 使用的是当前 `_interval` 值。如果在一次 `wait` 刚开始时更新了 `_interval`（比如从 1s 改到 10s），当前这次 `wait` 不会受影响，但下次生效。如果从 10s 改到 1s，则需要等当前 10s 的 `wait` 结束后才生效。
- **影响**：UX 上有短暂延迟，不影响功能。

### 🟢 轻微问题（代码质量或健壮性建议）

#### 7. CSV 导出时 `zip(deque, deque)` 的数据快照问题
- **文件**：`app.py` 第 433 行
- **问题**：导出期间如果监控仍在运行，`_timestamps` 和 `_cpu_values` 可能被并发修改（虽然在 Qt 主线程中，如果导出是同步操作就没问题——`QFileDialog` 阻塞了事件循环）。当前实现是安全的，因为 `QFileDialog.getSaveFileName` 阻塞了主线程事件循环。
- **建议**：加个注释说明为什么安全。

#### 8. 首次启动 `cpu_percent(interval=None)` 返回 0.0
- **文件**：`monitor.py` 第 64 行
- **问题**：已经有预热调用 `psutil.cpu_percent(interval=None)`，但预热和首次采集之间没有 sleep——第一个数据点可能不准确（接近 0.0）。
- **影响**：第一个数据点可能偏低，但影响极小，曲线上几乎看不出来。

#### 9. 告警日志列表的 placeholder 逻辑
- **文件**：`app.py` 第 281-282 行
- **问题**：`_alert_placeholder` 和 `_alert_list` 同时 visible。停止后重新开始时，如果之前有告警记录，列表不会清空。
- **影响**：如果用户期望每次开始都是干净的告警列表，当前行为不符合预期。

### 逻辑正确性结论

- **总体评价**：代码逻辑整体扎实，架构设计合理。分级采集策略实现正确，QThread + Signal/Slot 通信模式符合 Qt 最佳实践。
- **严重问题**：3 个（内存泄漏、关闭竞态、重复断连）
- **中等问题**：3 个（时长计算、线程安全、配置延迟生效）
- **轻微问题**：3 个（数据快照、预热精度、UI 状态）

---

## 二、性能开销分析

### 当前设计评估

- **预估 CPU 占用**：常规路径 ≈ 0.05%–0.15%，告警路径短暂升至 1%–3%（但被冷却期限制频率）
- **评价**：常规路径下完全能达到 < 1% 的目标

### 主要开销来源分析

| 开销来源 | 频率 | 单次开销 | 影响 |
|----------|------|----------|------|
| `psutil.cpu_percent(interval=None)` | 1 次/秒 | < 0.1ms | 极低 ✅ |
| `psutil.process_iter()` | 仅告警 + 冷却期 | 10–50ms | 低（受冷却期保护） |
| `list(deque)` 拷贝 × 2 | 1 次/秒 | < 0.01ms（300 元素） | 极低 ✅ |
| `PlotDataItem` 新建（baseline） | 1 次/秒 | < 0.5ms | 中等 ⚠️（累积） |
| `[t - t0 for t in ts_list]` 列表推导 | 1 次/秒 | < 0.01ms（300 元素） | 极低 ✅ |
| pyqtgraph `setData()` 渲染 | 1 次/秒 | 1–3ms | 低 ✅ |
| `QTimer` 运行时长更新 | 1 次/秒 | < 0.01ms | 极低 ✅ |
| `setStyleSheet` CPU 颜色更新 | 1 次/秒 | < 0.1ms | 极低 ✅ |

### 🔴 可优化项（高优先级）

#### 1. `PlotDataItem` 每秒新建 — 内存泄漏 + 不必要的对象分配
- **文件**：`app.py` 第 469-470 行
- **开销**：每秒创建一个新 `PlotDataItem` 对象，从不释放。300 秒后有 300 个废弃对象，占用 ~数 MB 内存。更重要的是 pyqtgraph 内部 scene 引用可能阻止 GC。
- **优化方案**：预创建 baseline `PlotDataItem`，只更新数据。
- **预计收益**：消除内存泄漏，减少 GC 压力。

### 🟡 可优化项（中优先级）

#### 2. `list(deque)` 双重拷贝
- **文件**：`app.py` 第 461-462 行
- **开销**：每秒对两个 deque 各做一次 `list()` 拷贝。300 个 float 的拷贝开销约 0.005ms，微乎其微。
- **可替代方案**：使用 `numpy` 数组替代 deque，直接 slice 操作，但增加了依赖。
- **结论**：当前实现可以接受，不构成性能瓶颈。

#### 3. `[t - t0 for t in ts_list]` 列表推导
- **文件**：`app.py` 第 465 行
- **开销**：300 次浮点减法 + 新列表创建，约 0.005ms。
- **可替代方案**：预存相对时间，避免每次重算。但增加了逻辑复杂度。
- **结论**：当前实现可以接受。

### 🟢 可优化项（低优先级 / 可接受）

#### 4. `setStyleSheet` 每秒调用
- **文件**：`app.py` 第 480 行
- **开销**：每次 `_on_cpu_data` 都重设一次 `setStyleSheet`。Qt 的样式引擎会触发样式重算，但因为只改了一个 Label，开销 < 0.1ms。
- **可优化方案**：只在颜色实际变化时才调用（记录上次颜色状态）。
- **结论**：可做但收益极小。

#### 5. pyqtgraph `antialias=False, useOpenGL=False` 选择
- **文件**：`app.py` 第 215 行
- **评价**：正确选择。关闭抗锯齿和 OpenGL 减少了渲染开销。数据量只有 300 点，原生 QPainter 足矣。
- **结论**：已是最优配置 ✅

#### 6. `QTimer` 每秒更新运行时长
- **文件**：`app.py` 第 123-125 行
- **开销**：`time.time()` + 整数除法 + `setText`，总计 < 0.01ms。
- **结论**：完全可以接受 ✅

#### 7. `threading.Event.wait(timeout)` vs `QThread.msleep()`
- **文件**：`monitor.py` 第 85 行
- **评价**：`threading.Event.wait(timeout)` 是正确选择——它同时支持超时等待和事件唤醒（响应 `stop` 信号），比 `QThread.msleep()` 更灵活。`msleep` 无法提前唤醒。
- **结论**：当前方案已是最优 ✅

### 性能开销结论

- **总体评价**：性能设计非常优秀。分级采集策略有效地将常规路径开销控制在极低水平。
- **能否达到 < 1% CPU 目标**：**完全可以**。常规路径估算总 CPU 开销 ≈ 0.05%–0.15%，远低于 1% 目标。
- **唯一实质性问题**：`PlotDataItem` 每秒新建导致的内存泄漏，虽然不直接增加 CPU，但长时间运行后可能因 GC 压力间接影响性能。

---

## 三、综合结论

### 总体评价

| 维度 | 评分 | 说明 |
|------|------|------|
| **逻辑正确性** | **7.5 / 10** | 核心逻辑正确，但有内存泄漏和关闭时竞态条件需修复 |
| **性能达标度** | **9.0 / 10** | 完全达标（< 1% CPU），仅有一处内存泄漏需优化 |

### 问题汇总

| 类别 | 🔴 严重 | 🟡 中等 | 🟢 轻微 |
|------|---------|---------|---------|
| 逻辑正确性 | 3 | 3 | 3 |
| 性能开销 | 1 | 2 | 4 |
| **合计** | **4** | **5** | **7** |

### 优先修复建议

1. **🔴 P0 — 修复 `PlotDataItem` 内存泄漏**（逻辑 + 性能双重问题）  
   在 `_create_chart` 中预创建 `_baseline = pg.PlotDataItem()`，`_on_cpu_data` 中只调用 `_baseline.setData(x_data, zeros)` + `_fill.setCurves(self._curve, self._baseline)`。

2. **🔴 P0 — 修复 `closeEvent` 关闭竞态**  
   在 `closeEvent` 中先 `disconnect` 信号，再 `stop()` + `wait()`。加 `try/except TypeError` 防护。

3. **🔴 P0 — 防止 `_on_stop` 重复调用崩溃**  
   加 `if self._monitor_thread is None: return` 保护，`disconnect` 用 `try/except`。

4. **🟡 P1 — 修复暂停时长计算**  
   引入 `_paused_duration` 累积器，暂停时记录暂停开始时间，恢复时累加暂停时长，`_update_duration` 扣除暂停时长。

5. **🟡 P1 — 优化 `setStyleSheet` 调用**  
   缓存上一次颜色状态，只在变化时调用 `setStyleSheet`。

---

*由 CPUUsageTrack Review 系统自动生成*
