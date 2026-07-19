# ppt — 永久投资组合辅助工具

命令行工具，辅助管理「中美混合版永久投资组合」。核心定位：**主动优化的计划器 + 记账本**。

实现语言：Python 3.8+，纯 CLI，无 GUI。本文档是实现规约：按它复现应得到行为等价的工具。

---

## 1. 资产配置

四桶等权目标（各 25%），走廊宽度按波动率自适应。

| 桶 | 标的 | 市场 | 最小交易单位 | 计价币种 |
|---|---|---|---|---|
| 股票 stock | SPYM, AVUV | 美股 | 1 股 | USD |
| 债券 bond | VGIT | 美股 | 1 股 | USD |
| 黄金 gold | GLDM, 518880.SS | 美股/A股 | 1 股 / 100 份 | USD / CNY |
| 现金 cash | SGOV, 511360.SS | 美股/A股 | 1 股 / 100 份 | USD / CNY |

**主标的（用于波动率/趋势计算）**：stock=SPYM、bond=VGIT、gold=GLDM、cash=SGOV。

规则：
- **股票桶双标的**：SPYM（大盘）+ AVUV（小盘价值）
  - 买入侧：选桶内**当前市值更低**的那个（仅 USD 端可买）
  - 卖出侧：选桶内**当前市值更高**的那个（含 USD 端优先）
- **桶内再均衡**：当 SPYM 或 AVUV 占比超过 60% 时触发，目标回到 50:50
- **两段式换仓**：黄金/现金桶先在美股端(GLDM/SGOV)积累，市值够买目标股数后卖出美股买入A股(518880/511360)
- **价格统一为人民币**：所有 USD 标的通过实时汇率转为 CNY 后参与权重计算

---

## 2. 数据存储

### 2.1 持仓数据

存储在 OSS（`oss://<your-bucket>/pp_holdings.json`），通过 `ossutil` CLI 读写。每次写入前自动备份到 `pp_holdings.backup.json`。

```json
{
  "holdings": {"SPYM": 30.0, "AVUV": 15.0, "VGIT": 50.0, "GLDM": 80.0, "518880.SS": 0.0, "SGOV": 100.0, "511360.SS": 0.0},
  "cash_in": 50000.0,
  "cash_out": 0.0,
  "transactions": [
    {
      "id": "uuid",
      "date": "2025-01-15",
      "type": "buy",
      "trades": [{"ticker": "SPYM", "shares": 10, "price": 72.50, "currency": "USD"}],
      "usdcny": 7.2500,
      "amount_cny": 5256.25,
      "internal": false
    }
  ],
  "created_at": "2025-01-01"
}
```

字段说明：
- `holdings[ticker]` = 股数（浮点，便于撤销/再均衡）
- `cash_in` / `cash_out` = 累计投入 / 取出（人民币）
- `transactions[i].type` ∈ `"buy" | "sell"`
- `transactions[i].trades[j].currency` ∈ `"USD" | "CNY"`
- `transactions[i].amount_cny` = 该笔交易的人民币总额（已乘汇率）
- `usdcny` 在交易时落账（撤销时仍用历史汇率）
- `internal` = 是否为内部调仓；内部调仓保留交易记录，但不计入外部投入/取出

### 2.2 本地缓存（`~/.pp/`）

| 文件 | 内容 | 结构 |
|---|---|---|
| `price_cache.json` | 价格缓存 | `{"timestamp": "YYYY-MM-DD HH:MM:SS", "prices": {ticker: 原始币种价}, "usdcny": 7.245}` |
| `price_history.json` | 桶价格历史 | `[{"date": "YYYY-MM-DD", "prices_cny": {"stock": x, "bond": y, "gold": z, "cash": w}}, ...]`（按日期升序，保留最近 120 天）|
| `pp_config.json` | 用户配置 | 见 §6 |
| `pp.log` | 日志 | 时间戳 + 级别 + 消息 |

价格历史是按**桶**聚合的（不是按 ticker）——每条记录的 `prices_cny[bucket]` = 该桶主标的的人民币单价（如黄金桶记 GLDM 的 CNY 价格）。每次 `ppt status` 触发：追加今日桶价（同日则覆盖）→ 若条目数 ≤ 30 则回填 3 个月历史。

---

## 3. 价格获取

- 使用 `yfinance` 库批量下载所有 ticker + `CNY=X`，period="5d"（取最近一个 Close 作为当前价）
- 价格缓存 TTL = 300 秒
- `--fresh` 强制绕过缓存，`--offline` 只读缓存（无缓存时报错）
- **批量下载失败的项**回退到单标的下载，单标的最多重试 3 次间隔 2 秒
- **价格校验**（不通过仅记日志，不阻塞）：
  - `5.0 ≤ usdcny ≤ 10.0`
  - 每个 `price > 0`（违反则抛错）
  - 7 个标的中唯一价格数 > 总数 / 3（防 yfinance 返回同一占位价）

环境变量 `PP_DEBUG=1` 打开调试输出。

---

## 4. 核心算法

所有数值用浮点；最终落账前按交易单位圆整。浮点比较容差 `1e-9`。

### 4.1 权重计算

```
ticker_values[t] = holdings[t] * prices_cny[t]
bucket_values[b] = sum(ticker_values[t] for t in bucket_tickers[b])
total = sum(bucket_values)
bucket_weights[b] = bucket_values[b] / total       # total=0 时返回全 0
```

`prices_cny[t] = price * usdcny` if t ∈ USD else `price`。

### 4.2 目标权重

由 `weighting_mode` 决定：

**等权** (`"equal"`)：每桶 target = 0.25。

**风险平价** (`"risk_parity"`)：

\[
w_b^* = \frac{1/\sigma_b}{\sum_j 1/\sigma_j}
\]

带 cap/floor 约束（默认 `[0.10, 0.40]`），**迭代裁剪算法**：

1. 计算原始权重
2. 重复最多 20 次：
   - 把超 cap 的桶钉到 cap、低于 floor 的桶钉到 floor
   - 累积溢出/欠额 `excess`
   - 若仍有自由桶（在区间内），按其当前权重比例重新分配 `excess`
   - 若全部被钉死，特殊处理：cap-limited 桶固定在 cap，剩余按 `1 - cap_sum` 平均分给其他桶（不低于 floor）
   - 收敛（所有桶都在 `[floor, cap]`）则停止
3. 最终归一化使 sum=1

### 4.3 波动率估计

\[
r_{b,t} = (P_{b,t} - P_{b,t-1}) / P_{b,t-1}, \quad W = 60
\]

\[
\sigma_b = \text{std}(r_{b,t-W+1}, \ldots, r_{b,t}) \times \sqrt{252}
\]

- 价格序列取自价格历史文件，按桶主标的
- 收益率少于 20 个时回退经验值：股票 15%、债券 10%、黄金 16%、现金 2%
- 下限保护：`σ_b ≥ 0.005`（防除零）

### 4.4 自适应再平衡走廊

\[
h_b = \max\left( k \cdot \frac{\sigma_b}{\sqrt{12}},\; h_{\min} \right)
\]

\[
L_b = \max(w_b^* - h_b,\; 0.10), \quad U_b = \min(w_b^* + h_b,\; 0.40)
\]

- \(k = 2.5\)，\(h_{\min} = 0.03\)
- 走廊硬顶 `corridor_upper_cap = 0.40`，硬底 `corridor_lower_floor = 0.10`
- 无价格历史时退化为固定阈值 `[0.15, 0.35]`

### 4.5 趋势信号与走廊调整

\[
\text{trend}_b = \frac{\text{MA}_{S}(P_b)}{\text{MA}_{L}(P_b)} - 1
\]

\(S = 10\)，\(L = 20\)。数据不足（少于 L 天）时 trend = 0（中性）。

调整走廊（**只移动一个边界，不收窄**）：
- 弱势桶（trend < 0）：上限上移 \(\Delta\)，延缓卖出
- 强势桶（trend > 0）：下限下移 \(\Delta\)，延缓买入

\[
\Delta = \lambda \cdot |\text{trend}_b| \cdot (U_b - L_b), \quad \lambda = 0.5
\]

调整后再次 clamp 到硬顶/硬底。

### 4.6 再平衡：联立方程求解（强制再平衡）

按动态走廊判断超标/低配桶。

**卖出标的**：桶内选当前市值最高的（USD 端优先，其次全部持仓）；**买入标的**：桶内 USD 端选当前市值最低的。

#### 单桶超标（解析解）

\[
s = \frac{V_b - w_b^* \cdot V}{p \cdot (1 - w_b^*)}
\]

股数向上取整（ceil），并 clamp 到不超过持仓。

#### 多桶同时超标（联立解）

令 \(S = \sum_j s_j p_j\)（总卖出金额），\(V_{\text{keep}} = V - \sum_{j \in \text{over}} V_j\)。对所有超标桶 i 联立：

\[
s_i \cdot p_i = V_i - w_i^* \cdot (V - S)
\]

求和得到：

\[
S = \frac{\sum_{i \in \text{over}} V_i - V \cdot \sum_{i \in \text{over}} w_i^*}{1 - \sum_{i \in \text{over}} w_i^*}
\]

特殊情况：若 \(\sum_{i \in \text{over}} w_i^* \approx 1\)（所有桶都超标，退化），每个桶独立用单桶公式求解。

#### 多桶同时低配（联立解）

对称处理，\(B = \sum_j b_j p_j\)：

\[
B = \frac{V \cdot \sum_{i \in \text{under}} w_i^* - \sum_{i \in \text{under}} V_i}{1 - \sum_{i \in \text{under}} w_i^*}
\]

每个低配桶：\(b_i p_i = w_i^*(V + B) - V_i\)。

#### 自筹资金约束

强制再平衡必须 **买入总额 ≤ 卖出总额**。违反时按比例缩放买入量，每个买入项按合法手数向下取整。

### 4.7 增量分配（定投）

输入：当前 holdings、价格、投入额 C。

1. **缺口识别**：仅保留 \(g_b = (V+C) \cdot w_b^* - V_b > (V+C) \cdot \text{tolerance}\) 的桶
2. **弹性加权**：\(\text{weight}_b = g_b^{\,\alpha}\)，\(\alpha = 1.5\)（默认）
3. **费率过滤**：迭代移除分配额 < MIN_TRADE_AMOUNT(¥500) 的桶
   - 每轮找出**分配额最小**的桶（同额时取主标的单价高的）
   - 移除后剩余桶重新按比例分配
   - 至少保留 1 个桶
4. **离散化**：最大余额法（Hamilton method）
   - 按比例计算精确浮点股数
   - 全部 floor 到合法手数（美股 1 股 / A股 100 份）
   - 剩余资金按 **(精确股数 − 已分配股数) × 单价** 最大者 +1 手，重复直到剩余不够 1 手
5. **桶内标的选择**：双标的桶选市值更低的那个
6. **退化情况**：
   - `total = 0`（首投）→ 四桶等权分配
   - 所有桶在容忍带内 → 按相对缺口比例（仍带 elasticity）分配到所有桶

### 4.8 定投 + 换仓统一规划

1. 检查 GLDM/SGOV 换仓触发条件
2. 计算原始定投分配
3. **净额合并**：检查分配方案中是否买入了换仓要卖的标的（SGOV/GLDM）
   - 若分配买入量 ≥ 换仓卖出量：抵消买入量，换仓只需直接买 A 股（标的不卖 USD 端）
   - 若分配买入量 < 换仓卖出量：分配项清零，换仓卖出量减少为净差额
   - 不论哪种情况都节省 1 笔交易
4. 模拟分配后持仓，检查 stock 桶内再均衡（避免分配已修正失衡却仍建议换仓的 whipsaw）
5. 清理 shares=0 的空分配项

### 4.9 桶内再均衡（stock: SPYM ↔ AVUV）

触发条件：\(\max(r_{\text{SPYM}}, r_{\text{AVUV}}) > 0.60\)，其中 \(r = V/V_{\text{stock}}\)。

操作：
- 高配标的为目标比例 0.50，转移金额 \(= V_{\text{over}} - 0.5 \cdot V_{\text{stock}}\)
- 卖出股数 \(= \lceil \text{转移金额} / p_{\text{sell}} \rceil\)（向上取整），clamp 到持仓
- 买入股数 \(= \lfloor \text{卖出收入} / p_{\text{buy}} \rfloor\)（向下取整）

### 4.10 两段式换仓

| 桶 | 卖 | 买 | 触发条件 |
|---|---|---|---|
| 黄金 | GLDM | 518880.SS | GLDM 市值 ≥ 1000 × \(p_{518880}\) × (1 + fx_spread) |
| 现金 | SGOV | 511360.SS | SGOV 市值 ≥ 100 × \(p_{511360}\) × (1 + fx_spread) |

`fx_spread = 0.003`（汇兑成本安全垫）。

触发时按"批"操作：
- 批数 \(= \lfloor \text{市值} / \text{threshold} \rfloor\)
- 买入整批股数 = 批数 × 换仓单位（1000 或 100）
- 卖出股数 \(= \lceil \text{买入金额} / p_{\text{sell}} \rceil\)，clamp 到持仓

### 4.11 定投达标方案（无参 `ppt plan`）

目标：使最大偏差 < tolerance（0.5%）的最小投入额。

1. **达标门控**：max_dev < tolerance → 返回 0（无需投入）
2. **理论最小投入**（仅低配桶）：

\[
C = \frac{\sum_{i \in \text{under}} w_i^* \cdot V - \sum_{i \in \text{under}} V_i}{1 - \sum_{i \in \text{under}} w_i^*}
\]

3. **退化情况**（所有桶都低配，分母 = 0）：取最大缺口 \(g\)，\(C = \max(g \cdot k/(k-1), \text{MIN\_TRADE\_AMOUNT} \cdot k)\)
4. **可行性兜底**：保证每桶至少能买 1 手，否则放大 C
5. **向上取整到百元**
6. **按缺口比例分配**（gap^1.0，不放大 elasticity，避免大缺口桶吸走全部资金）
7. **结果校验**：分配后最大偏差必须 **严格小于** 分配前，否则拦截返回 0（过冲保护）

门控语义总结：
- 已达标（max_dev < tolerance）→ 无需投入
- 走廊内但未达标 → 仍计算方案（走廊仅"可接受"，tolerance 才"达标"）
- 过冲 → 拦截，提示指定金额

### 4.12 相关性分析

**桶间相关性**：基于桶价格历史计算两两日收益率 Pearson 相关系数。

\[
\rho_{ij} = \frac{\text{Cov}(r_i, r_j)}{\sigma_i \cdot \sigma_j}
\]

- 需至少 30 天数据，方差为 0 返回 None
- 相关系数 > 0.7 时发预警

**股债相关性反转检测**：取近 61 天（前 30 + 后 30 + 1 个分界），分前后两段计算股债相关系数。

预警条件：\(\rho_{\text{前}} < 0\) 且 \(\rho_{\text{后}} > 0.3\)。

### 4.13 收益计算

**桶净成本**：累加该桶所有交易的 RMB 金额，买入 +、卖出 −。USD 交易按交易当时的汇率换算。

**总收益**：\(P = V - \sum_b \text{cost}_b\)，百分比 = \(P / \text{net\_cost}\)。

**年化 XIRR**：

现金流：买入为负、卖出为正、期末市值为正（同日可合并）。

\[
\text{NPV}(r) = \sum_i \frac{cf_i}{(1+r)^{(d_i - d_0)/365}}
\]

牛顿迭代（guess=0.1, tol=1e-6, max_iter=200）：
- 数值导数（中心差分，自适应步长）
- 边界保护：new_rate ≤ -1 → 钳到 \((rate-1)/2\)；new_rate > 10 → 钳到 \((rate+10)/2\)
- 收敛失败 → fallback 二分法（区间 `[-0.99, 10.0]`, max_iter=300）
- 流入/流出不全 → 返回 None
- XIRR 失败但持仓超 1% 净成本时回退到 CAGR：\((V/\text{cost})^{1/\text{years}} - 1\)

---

## 5. 命令接口

所有命令以 `ppt <command> [args] [global flags]` 形式调用。

### `ppt plan <金额>`
生成买入建议（含换仓优化 + 桶内再均衡）。展示四块：**分配 → 变化(before/after) → 效果 → 执行命令**。

### `ppt plan`（无参）
计算定投达标最小投入方案。已达标提示无需投入；走廊内未达标给最小方案；过冲拦截提示指定金额。

### `ppt rebalance [--full] [--dry-run]`
诊断桶偏离；`--full` 按卖超买欠生成并记录内部调仓，`--dry-run` 仅展示方案。内部调仓不改变累计投入或取出。

### `ppt buy 代码#股数@单价 [...]`
记录买入。**同标的自动加权合并均价**（如 `AVUV#2@121.63 AVUV#1@121.69` → `AVUV#3@121.65`）。单价填原始币种（USD标的填美元价）。落账后展示更新后持仓 + 最大偏差。

### `ppt sell 代码#股数@单价 [...]`
记录卖出。卖出**预校验**（持仓不足直接报错）+ 确认后**二次校验**（防并发修改）。落账后展示更新后持仓。

### `ppt status`
持仓全景：持仓 → 权重(含走廊/趋势) → 收益 → 体检(再平衡/换仓/相关性)。每次运行会追加今日桶价到历史。

### `ppt history`
交易历史，按天倒序，同标的同日合并显示**加权均价**。

### `ppt undo`
撤销最近一笔交易。展示**撤销预览卡片**（含交易明细），确认后反向操作（买入→减、卖出→加），并追加日志。撤销后持仓为负则 **clamp 到 0 + 警告日志**。

### `ppt config [show|init]`
`show` 查看当前配置；`init` 生成默认配置文件覆盖现有。

### `ppt init`
**重置所有数据**。展示确认卡片 → 确认后清空 holdings/transactions/cash_in/cash_out，保留 created_at。

### `ppt clean-history`
清理桶价格历史中的 NaN 记录。

### `ppt help`
结构化帮助，按"核心命令 / 配置工具"分组。

### 输入格式

```
代码#股数@单价
```

- 代码白名单：`SPYM`、`AVUV`、`VGIT`、`GLDM`、`518880.SS`、`SGOV`、`511360.SS`
- **USD 标的**：股数必须为正整数（按整股交易）
- **A 股标的（518880.SS / 511360.SS）**：股数必须为 100 的正整数倍（按整手交易）
- 多笔空格分隔：`ppt buy SPYM#3@72.50 VGIT#5@58.92`
- 股数 ≤ 0 或单价 ≤ 0 报错

### 全局选项

| 选项 | 作用 |
|---|---|
| `--fresh` | 忽略价格缓存，强制拉取 |
| `--offline` | 只读本地缓存 |
| `--yes` / `-y` | 跳过交互确认 |

### 退出码

| 码 | 含义 |
|---|---|
| 0 | 正常退出 |
| 1 | 业务错误（输入错误、价格获取失败、OSS 读写失败、未知命令）|
| 130 | Ctrl+C 中断 |

---

## 6. 配置参数

配置文件路径：`~/.pp/pp_config.json`。缺失字段用默认值补齐。

```json
{
  "rebalance": {
    "tolerance": 0.005
  },
  "conversion": {
    "gldm_shares": 1000,
    "sgov_shares": 100
  },
  "network": {
    "max_retry": 3,
    "retry_wait": 2,
    "cache_ttl": 300
  },
  "advanced": {
    "weighting_mode": "equal",
    "gap_elasticity": 1.5,
    "corridor_k": 2.5,
    "trend_sensitivity": 0.5,
    "rp_weight_cap": 0.40,
    "rp_weight_floor": 0.10
  }
}
```

| 参数 | 默认值 | 含义 |
|---|---|---|
| `rebalance.tolerance` | 0.005 | 缺口识别阈值（占总资产比例） |
| `conversion.gldm_shares` | 1000 | GLDM→518880 换仓目标份数 |
| `conversion.sgov_shares` | 100 | SGOV→511360 换仓目标份数 |
| `advanced.weighting_mode` | `"equal"` | `"equal"` 或 `"risk_parity"` |
| `advanced.gap_elasticity` | 1.5 | 缺口弹性指数 α |
| `advanced.corridor_k` | 2.5 | 走廊宽度系数 k |
| `advanced.trend_sensitivity` | 0.5 | 趋势调整灵敏度 λ |
| `advanced.rp_weight_cap` | 0.40 | 风险平价单桶权重上限 |
| `advanced.rp_weight_floor` | 0.10 | 风险平价单桶权重下限 |
| `network.max_retry` | 3 | 网络请求最大重试 |
| `network.retry_wait` | 2 | 重试间隔秒数 |
| `network.cache_ttl` | 300 | 价格缓存有效期（秒） |

固定常量（不在配置文件中）：

| 常量 | 值 | 含义 |
|---|---|---|
| 波动率滚动窗口 W | 60 | 天 |
| 价格历史最大保留 | 120 | 天 |
| 波动率经验默认值 | stock 15% / bond 10% / gold 16% / cash 2% | 数据不足时 fallback |
| 走廊最小半宽 \(h_{\min}\) | 0.03 | 防低波动桶走廊过窄 |
| 走廊硬顶/硬底 | 0.40 / 0.10 | 不可突破 |
| 相关性最小天数 | 30 | 低于此不计算 |
| 相关性预警阈值 | 0.7 | 超过发预警 |
| 股债反转阈值 | 0.3 | 后半段 ρ 超此值且前半段 ρ < 0 触发 |
| 桶内再均衡阈值 | 0.60 | 单标的占比超此触发 |
| 桶内再均衡目标 | 0.50 | 回到此比例 |
| 最小交易额 | 500 | 单笔 ¥，用于费率过滤 |
| 汇兑安全垫 fx_spread | 0.003 | 0.3% |
| 波动率下限 | 0.005 | 防除零 |
| 浮点比较容差 | 1e-9 | |

OSS 路径常量：

- 持仓：`oss://<your-bucket>/pp_holdings.json`
- 备份：`oss://<your-bucket>/pp_holdings.backup.json`
- 本地目录：`~/.pp/`

---

## 7. 架构

### 7.1 三层职责

建议拆为四个职责清晰的模块，调用关系**严格单向**：

```
CLI 入口与编排
  ├── 纯计算层    — 无 IO、无 print、无副作用
  ├── 数据 IO 层  — OSS / yfinance / 缓存
  └── 展示层      — 仅做 print，调用下面的原子组件
        └── 原子组件层 — 设计 token + 通用、无业务语义的渲染原语
```

约束：
- **纯计算层**不 import 任何项目内模块，不做 IO，不 print；所有可配置参数通过一个 dataclass 注入
- **原子组件层**只 import 计算层的常量（如币种集合、配置对象），暴露**通用、无业务语义**的渲染原语（Panel / KPI / Badge 等），不知道"桶"或"标的"的概念
- **展示层**把业务数据灌进组件，按命令组装页面；只 import 计算层常量 + 组件层
- **IO 层**只 import 计算层常量
- **CLI 编排层**汇总三层，负责命令分发、参数解析、异常捕获

### 7.2 终端展示设计系统

设计语言：**金融终端仪表盘**。每个命令 = 顶部标题 + 一组卡片 + 收尾；模块靠彩色左边框（accent）区分，语义靠图标 + 色板传递。

#### 设计 token

集中管理视觉常量。**改全局样式只改这一个地方**：

| 类别 | Token | 建议值 | 说明 |
|---|---|---|---|
| 内容宽度 | MAX_WIDTH | 100 列 | 所有卡片/分隔线裁剪到此宽度（终端更宽裁剪、更窄自适应），避免宽屏分散注意力 |
| 间距 | GUTTER | 2 | 列间间距 |
| 间距 | PAD_X / PAD_Y | 1 / 0 | 卡片内左右 / 上下 padding |
| 列宽 | NUM_WIDTH | 8 | 数字列最小宽度（右对齐）|
| 进度条 | BAR_WIDTH | 14 | 偏差进度条格数 |

#### 色板

语义色板分四层：

| 类别 | Token | 样式 | 用途 |
|---|---|---|---|
| 数据强度 | fg_strong | bold white | 主数字、关键 KPI |
| 数据强度 | fg_default | white | 普通数字 |
| 数据强度 | fg_muted | grey50 | 标签、辅助、备注 |
| 数据强度 | fg_dim | grey35 | 占位、空状态 |
| 语义 | accent | bold cyan | 品牌强调（边框、KPI）|
| 语义 | profit | bold green | 收益、OK |
| 语义 | loss | bold red | 亏损、严重 |
| 语义 | warn | yellow | 警告 |
| 语义 | info | bold magenta | 换仓提示 |
| 结构 | border_dim | grey23 | 空状态卡片边框 |
| 结构 | border_ok / warn / crit / info | green / yellow / red / cyan | 卡片彩色边框 |

#### 必备的原子组件类型

实现时应至少提供以下组件（命名风格自选，但语义对齐）：

| 组件类型 | 职责 |
|---|---|
| 标题分隔条 | 顶部 Rule，居中标题，限制到 MAX_WIDTH |
| 卡片 | 通用容器，标题 + 内容 + 彩色左边框 + 内 padding |
| KPI | 上行小字 label（dim）+ 下行大字 value（语义色）+ 可选副字 |
| KPI 横行 | 多个 KPI 横向并列，按比例展开 |
| KV 对 | label dim / value bright |
| KV 表 | 多行 KV 表格化 |
| 状态徽章 | ` ✓ OK ` / ` ⚠ WARN ` / ` ● CRIT ` / ` ● INFO ` 风格 |
| 货币徽章 | ` USD ` / ` CNY ` 反色，按 ticker 自动判定 |
| 进度条 | 带目标刻度（`│` 在 target 位置）+ 偏差着色 + 百分比尾巴 |
| 偏差变化 | `12.3% → 8.1% ↓` 形式，按改善/恶化着色 |
| 迷你趋势线 | `▁▂▃▄▅▆▇█` 高度块，按值线性映射 |
| 备注行 | dim 灰色小字 |
| 空状态卡片 | 统一空状态（图标 + 消息 + dim 边框）|
| 成功横幅 | `✅ ...` 单行绿色卡片 |
| 警告卡片 | `❌` / `⚠` / `ℹ` + 标题 + 可选多行 body |
| 确认卡片 | 标题 + 预览内容 + 分隔条 + prompt 文本 |
| 命令提示行 | ` ❯ ppt sell ...` 等宽小字 |

#### 业务辅助函数

实现时应提供以下工具函数（用于把数值映射到语义）：

| 函数 | 输入 → 输出 |
|---|---|
| 偏差 → 语义 tone | `dev` → `ok` / `warn` / `crit`（按 tolerance / upper_limit 阈值）|
| 偏差 → 进度条色 | 同上，对应 bar_ok / bar_warn / bar_crit |
| 偏差 → 字符串 | `↑2.5%` / `↓0.5%` / `  —  `（绝对值小于 tol 显示 `—`）|
| 偏差 → 图标 + tone | `✓` / `⚠` / `●` |
| ticker → 简写 | 去掉 `.SS` 后缀 |
| ticker → 单位 | CNY 标的 → "份"，USD 标的 → "股" |
| ticker + price → 价格串 | USD → `$72.50`，CNY → `¥5.00` |
| 字符串 → 显示宽度 | CJK 字符算 2 列（含全角符号、半全角转换区）|
| 字符串对齐 | 左对齐 / 右对齐 padding（按显示宽度而非字符数）|

#### 页面级布局规约

每个命令的输出应遵循统一模式：

- **顶部**：标题分隔条（命令名 + 关键参数 + 时间 + 汇率）
- **主体**：若干卡片（标题居中、彩色左边框、统一边框样式）
- **底部**：备注行 + 命令提示行

各命令应展示的内容：

| 命令 | 卡片内容 |
|---|---|
| status | 持仓(每行：代码 + 股数 + 单价 + 币种徽章 + 人民币金额；底部：总资产 KPI 横行) / 权重(表格：桶名 + 实际 + 目标 + 偏差 + 进度条 + 状态；桶内子项用 `╰─` 树形缩进) / 收益(大字收益 KPI + XIRR + 各桶收益) / 体检(按严重度排序的预警列表，换仓项附命令提示行) |
| plan | 分配(表格：代码 + 股数 + 单价 + 金额；底部合计 KPI) / 变化(当前 + 买入后 + 偏差 + 进度条；严重超标时附预警) / 效果(最大偏差变化 + 剩余超/低配徽章 + 换仓明细 + 节省笔数) / 执行(`❯ ppt sell ...` 和 `❯ ppt buy ...` 命令提示行) |
| buy/sell 确认 | 交易明细表 + 汇率 + 分隔条 + 本次投入/取出 KPI |
| 交易落账 | `✅` 标题 + 持仓变化表 + 分隔条 + 落账后 KPI（投入 + 总资产 + 最大偏差）|
| history | 按天 panel：日期 + buy/sell 徽章 + 金额；桶内同标的合并均价 |
| config show | 每 section 一张卡片（按 section 类型用不同 accent）|
| undo | 撤销预览卡片（KV 表 + 交易明细 + prompt）|
| init | 重置确认卡片（红色 accent + 警告 + prompt）|
| 错误 | `❌` 警告卡片 |
| 空状态 | 居中（或左对齐）的空状态卡片 |

#### 扩展硬约束

**所有终端输出必须经过原子组件层**，禁止任何业务代码（CLI 入口、展示层）直接 `print(Text(...))` 散写。这是"改样式不会遗漏"的根本机制——新增命令或展示分支时，没有"裸 print"这个选项，必须组合使用组件，全局色板、间距、边框自动一致。

---

## 8. 依赖

```
yfinance
pandas
rich
ossutil (系统级，通过 subprocess 调用)
```

安装：`pip install -e .`，入口命令 `ppt`。

OSS 访问依赖 `ossutil` 可执行文件（通过 `OSSUTIL_PATH` 环境变量覆盖路径）。

---

## 9. 符号表

| 符号 | 含义 |
|---|---|
| \(w_b^*\) | 桶 b 目标权重 |
| \(\sigma_b\) | 桶 b 年化波动率 |
| \(V\)、\(V_b\) | 总市值、桶市值 |
| \(C\) | 增量投入资金 |
| \(h_b\) | 走廊半宽 |
| \(L_b\)、\(U_b\) | 走廊下限、上限 |
| \(k\) | 走廊宽度系数 (2.5) |
| \(\alpha\) | 缺口弹性指数 (1.5) |
| \(\lambda\) | 趋势调整灵敏度 (0.5) |
| \(\rho\) | Pearson 相关系数 |
| \(s_i\) | 桶 i 卖出股数 |
| \(b_i\) | 桶 i 买入股数 |
| \(g_b\) | 桶 b 缺口金额 |
| \(S\) | 联立方程中总卖出金额 |
| \(B\) | 联立方程中总买入金额 |
