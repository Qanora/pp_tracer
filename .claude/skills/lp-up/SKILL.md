---
name: lp-up
description: 第零层·A——执行引擎+分析运行时数据，持续发现架构/实现/算法缺陷，通过 subagent 启动 lp-ms 驱动迭代改进
---

# LP-UP（第零层 · 持续改进引擎）

主动执行引擎、观察运行过程、发现缺陷、提出 milestone，通过 **subagent** 启动 lp-ms 驱动整个飞轮迭代。

```
┌─────────────────────────────────────────────────────┐
│                    lp-up (第零层·A)                    │
│  执行 → 观察 → 分析 → 报告 → subagent:lp-ms（自动）   │
└────────┬────────────────────────────────────────────┘
         │ milestone (via subagent)
         ▼
    lp-ms (需求拆解 → issue)
         │ issue
         ▼
    lp-mr (MR 生命周期)
         │ branch
         ▼
    lp-dev (写代码)
         │ merge
         ▼
    lp-up (再执行 → 验证修复 → 发现新问题 → ...)
```

## 调用方式

```text
/lp-up                           # 纯分析模式（不执行，只分析已有数据）
/lp-up --run quick               # 快速轮：执行 + 分析
/lp-up --run full                # 完整轮：全量执行 + 深度分析
/lp-up --focus <area>            # 聚焦：performance | cost | accuracy | reliability
/lp-up --since <date>            # 只分析指定日期之后的数据
/lp-up --resume                  # 从中断恢复，继续上一轮未完成的改进循环
```

## 核心概念：持续改进循环

lp-up 不是一次性工具，而是一个**闭环迭代引擎**：

```text
Round N:   执行 → 观察 → 发现 F₁, F₂, F₃ → subagent:lp-ms → 飞轮实现
Round N+1: 执行 → 观察 → 验证 F₁ 已修复 ✓, F₂ 部分改善 ~, F₃ 未改善 ✗
                    → 发现新问题 F₄ → subagent:lp-ms → 飞轮实现
Round N+2: ...
```

每一轮都在上一轮的基础上推进，既验证历史修复效果，又发现新的改进空间。

## 流程

### 阶段 A：执行引擎（--run 模式）

若用户指定了 `--run`，先主动运行引擎管道，在运行过程中实时观察。

#### A.1 Quick Round

```bash
# 1. 执行 tracer 主流程
pptracer run --quick

# 2. 查看最近报告
pptracer report --last
```

#### A.2 Full Round

```bash
# 1. 全量数据采集
pptracer collect --full

# 2. 延迟分析
pptracer analyze --latency

# 3. 健康检查
pptracer health-check

# 4. 生成报告
pptracer report --full
```

#### A.3 执行期间实时观察

每个命令执行时同步采集：

| 观察维度     | 采集方式                                                    |
| ------------ | ----------------------------------------------------------- |
| 退出码       | `$?`                                                        |
| 耗时         | `time` 包裹                                                 |
| stdout/stderr | 完整捕获                                                    |
| 资源峰值     | 执行前后各采样一次 `psutil`：RSS、CPU%、open FDs            |
| 错误计数     | stderr 行数 + 日志中 ERROR 级别行数                         |

```bash
# 示例：包裹执行并采集运行时指标
python -c "
import time, psutil, os, sys, subprocess, json

pid = os.getpid()
before = {'rss_mb': psutil.Process(pid).memory_info().rss / 1024**2}

t0 = time.monotonic()
result = subprocess.run(sys.argv[1:], capture_output=True, text=True)
elapsed = time.monotonic() - t0

after = {'rss_mb': psutil.Process(pid).memory_info().rss / 1024**2}

print(json.dumps({
    'exit_code': result.returncode,
    'elapsed_s': round(elapsed, 1),
    'rss_before_mb': round(before['rss_mb'], 1),
    'rss_after_mb': round(after['rss_mb'], 1),
    'rss_delta_mb': round(after['rss_mb'] - before['rss_mb'], 1),
    'stdout_lines': len(result.stdout.splitlines()),
    'stderr_lines': len(result.stderr.splitlines()),
}))
" -- pptracer run --quick
```

### 阶段 B：数据采集

无论 `--run` 还是纯分析模式，都执行数据采集。采集来源分两类：

#### B.1 本轮执行数据（仅 --run 模式）

- 各命令的退出码、耗时、stdout/stderr
- 资源采样（RSS delta、CPU 峰值）
- 执行期间新产生的日志行

#### B.2 历史运行时数据（所有模式）

**结构化日志**（`~/.pptracer/logs/*.log`）：
```bash
# 按级别和模块统计
cat ~/.pptracer/logs/*.log | jq -r '[.level, .module] | @tsv' | sort | uniq -c | sort -rn

# 提取 ERROR（最近 30 天）
find ~/.pptracer/logs/ -name "*.log" -mtime -30 | xargs cat | jq 'select(.level == "ERROR")'

# 提取各阶段耗时
cat ~/.pptracer/logs/tracer.log | jq 'select(.data.elapsed_s != null) | {event, elapsed_s: .data.elapsed_s}'
```

**SQLite 运行时指标**（`~/.pptracer/data/pptracer.db`）：

| 表名                        | 分析目标                     |
| --------------------------- | ---------------------------- |
| `trace_samples`             | 延迟分布、P50/P95/P99       |
| `hop_metrics`               | 逐跳延迟趋势                 |
| `health_checks`             | 健康检查历史                 |
| `alerts`                    | 告警频率、类型分布、解决率   |

**Parquet 批量数据**（`~/.pptracer/data/`）：
- `traces/dt=*/` — 全量 trace 数据覆盖度和完整性

### 阶段 C：多维度分析

#### C.1 架构缺陷

**A1. 内存泄漏**
```sql
SELECT date(ts) as day, max(rss_mb) as peak_rss
FROM monitoring_samples
WHERE ts >= date('now', '-30 days')
GROUP BY date(ts)
ORDER BY day
```
判定：7 日 RSS 线性回归斜率 > 50MB/天 且 工作负载持平 → 疑似泄漏。

**A2. FD 泄漏**
```sql
SELECT date(ts) as day, max(open_fds) as peak_fds
FROM monitoring_samples
WHERE ts >= date('now', '-30 days')
GROUP BY date(ts)
ORDER BY day
```
判定：7 日 FD 计数斜率 > 10/天 → 疑似泄漏。

**A3. 管道瓶颈**
从日志提取各阶段 P50/P95/P99 耗时，P95 > 2× 历史中位数 → 瓶颈。

**A4. 调度可靠性**
检查 cron 任务是否按预期执行——交易日无 monitoring_samples 记录 → 调度可能未运行。

#### C.2 实现缺陷

**I1. 错误聚类**
```bash
cat ~/.pptracer/logs/*.log | jq -r 'select(.level == "ERROR") | "\(.module) | \(.event)"' | sort | uniq -c | sort -rn | head -10
```
判定：单一 error > 10 次/天 → 系统性 bug。

**I2. 数据缺口**
检查 traces Parquet 分区覆盖——某时间窗口记录数 < 中位数 50% → 采集不完整。

**I3. 延迟异常**
P99 延迟 > 历史 P99 × 3 → 异常延迟 spike。

**I4. 本轮执行异常（仅 --run 模式）**
- 任一命令退出码 ≠ 0 → 立即标记为 CRITICAL
- stderr 非空 → 提取关键错误信息
- RSS delta > 200MB（单次执行）→ 内存异常

#### C.3 算法缺陷

**G1. 延迟漂移**
```sql
SELECT date(ts) as day, p50_ms, p95_ms, p99_ms
FROM trace_samples
WHERE ts >= date('now', '-30 days')
ORDER BY day
```
判定：20 日滚动 P50 斜率 > 1ms/天 → 系统性延迟退化。

**G2. 逐跳延迟分布异常**
特定 hop 延迟占比超过总量 50% → 单点瓶颈。

**G3. 健康检查通过率下降**
```sql
SELECT date(ts) as day, 
       sum(case when passed then 1 else 0 end) * 1.0 / count(*) as pass_rate
FROM health_checks
WHERE ts >= date('now', '-30 days')
GROUP BY date(ts)
ORDER BY day
```
判定：pass_rate < 0.8 → 系统健康度下降。

### 阶段 D：发现分类

每个发现包含：

| 字段       | 说明                                                     |
| ---------- | -------------------------------------------------------- |
| 严重度     | `CRITICAL`（影响正确性/可用性）、`WARNING`（影响效率）、`INFO`（优化建议） |
| 类别       | `ARCHITECTURE`、`IMPLEMENTATION`、`ALGORITHM`             |
| 证据       | 具体数据点 + 时间戳 + 统计量                              |
| 根因假设   | 基于证据的推断                                           |
| 建议范围   | 涉及模块/文件                                            |

### 阶段 E：报告生成

```text
## LP-UP 分析报告 — Round <N>

**分析时间**: <ISO timestamp>
**执行模式**: quick | full | passive
**数据范围**: <start> → <end>
**上一轮**: Round <N-1> 于 <date>，共 <M> 个发现，<X> 个已修复

---

### 上一轮修复验证（仅 Round ≥ 2）

| 发现 | 状态 | 证据 |
|------|------|------|
| #1 内存泄漏 | ✓ 已修复 | RSS 7 日斜率从 +45MB/天 降至 +3MB/天 |
| #2 P99 延迟 | ~ 部分改善 | P99 从 500ms 降至 200ms |

---

### 本轮发现汇总

| # | 严重度 | 类别 | 简述 | 建议 milestone |
|---|--------|------|------|----------------|
| 1 | CRITICAL | IMPLEMENTATION | run 命令 exit code=1 | 修复 run 命令异常 |
| 2 | WARNING | ALGORITHM | P99 延迟上升趋势 | 排查延迟瓶颈 |

---
```

### 阶段 F：自动派发 lp-ms

报告生成后**自动推进，不询问用户**。派发规则：

| 严重度 | 动作 |
|--------|------|
| CRITICAL | **自动推进** — 立即通过 subagent 启动 lp-ms |
| WARNING | **自动推进** — 立即通过 subagent 启动 lp-ms |
| INFO | **自动跳过** — 记录到 findings.json，下一轮若升级则推进 |

对每个自动推进的发现，通过 `Agent` 工具以 subagent 模式启动 lp-ms：

```text
Agent(
  description: "lp-ms: <简述>",
  subagent_type: "lp-ms",
  prompt: "<milestone 描述>"
)
```

每个 milestone 描述格式：

```text
[lp-up][<类别>] <简述>

**来源**: lp-up Round <N> 分析报告
**严重度**: CRITICAL | WARNING | INFO
**类别**: ARCHITECTURE | IMPLEMENTATION | ALGORITHM
**证据摘要**: <关键数据点>
**根因假设**: <分析判断>
**预期收益**: <修复后的改善>
**建议范围**: <涉及模块/文件>
```

**串行派发**：按严重度排序（CRITICAL → WARNING），依次启动 subagent。

### 阶段 G：状态持久化

每轮结束后保存状态：

```text
.claude/state/lp-up/
  round.md           # 当前 round 编号、最后分析日期、执行模式
  findings.json      # 历史发现追踪
```

**findings.json 结构**：
```json
{
  "round": 3,
  "last_run": "2026-05-23T08:00:00Z",
  "findings": [
    {
      "id": "F-001",
      "title": "RSS 7 日增长 +320MB，疑似内存泄漏",
      "severity": "CRITICAL",
      "category": "ARCHITECTURE",
      "status": "resolved",
      "milestone_url": "https://github.com/Qanora/pp_tracer/milestone/5",
      "round_discovered": 1,
      "round_resolved": 2
    }
  ]
}
```

### 阶段 H：下一轮预告

```text
## 本轮总结

- 发现总数: 3
- 已推进: 2 个 milestone（通过 lp-ms subagent）
- 跳过: 1 个

## 下一轮建议

建议在 milestone 实现合并后（预计 1-3 天），运行：
  /lp-up --run quick

重点验证:
  - F-001 修复效果（内存泄漏）
  - F-002 修复效果（run 命令异常）
```

## 约束

- **执行权限**：`--run` 模式需要 Bash 权限运行 CLI 命令；纯分析模式只读
- **只读分析**：除 `--run` 中的引擎执行外，不修改任何代码、配置或数据
- **自动推进**：CRITICAL 和 WARNING 发现自动通过 subagent 派发 lp-ms，不询问用户；INFO 记录到 findings.json 供后续跟踪
- **串行派发**：多个 milestone 按严重度排序依次派发，不并行
- **数据采样上限**：单次分析不超过 30 天数据或 10 万行日志
- **证据驱动**：每个发现必须有可追溯的数据证据
- **增量分析**：若存在上一轮状态文件，默认只分析上次报告之后的新数据

## 数据不足处理

若某维度数据不足，标注跳过：

```text
⏭ [ARCHITECTURE] 内存泄漏检测 — 跳过：monitoring_samples 仅有 3 天数据，需至少 7 天
⏭ [ALGORITHM] 延迟漂移检测 — 跳过：trace_samples 无数据
```

不做强行推断。
