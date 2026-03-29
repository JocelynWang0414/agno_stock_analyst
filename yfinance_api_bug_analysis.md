## Bug 详细解释

### 背景：yfinance 的请求合并机制

`yfinance` 内部维护了一个**请求批处理器（request batcher）**。当它在很短的时间窗口内检测到多个并发的下载请求时，会自动把这些请求**合并成一次批量 HTTP 请求**发给 Yahoo Finance，以减少网络往返次数。

---

### 触发条件

本项目的 `Parallel` 步骤用 `ThreadPoolExecutor` 同时启动 Fundamental Analyst 和 Technical Analyst 两个 agent。Technical Analyst 对 10 个 ticker 各自调用一次 `compute_technical_signals(symbol)`，所有调用都在短时间内并发发出。

```
线程 1: yf.download("AAPL", ...)
线程 2: yf.download("MSFT", ...)
线程 3: yf.download("NVDA", ...)
...（共 10 个线程近乎同时发出请求）
```

yfinance 的批处理器发现这些请求，便将它们合并，**用一次请求同时拉取多个 ticker 的数据**。

---

### 数据结构的变化

**正常情况**（单线程，单 ticker）：

```
yf.download("AAPL", ...) → 返回 DataFrame，列为 MultiIndex:
  Level 0 (字段名): ['Close', 'High', 'Low', 'Open', 'Volume']
  Level 1 (ticker): ['AAPL', 'AAPL', 'AAPL', 'AAPL', 'AAPL']
```

调用 `get_level_values(0)` 后，列名变为 `['Close', 'High', ...]`，`raw["Close"]` 返回一个 **Series**（单列）。

**问题情况**（并发，批处理后）：

```
yf.download("AAPL", ...) → 实际返回多 ticker 的 DataFrame:
  Level 0 (字段名): ['Close', 'Close', 'High', 'High', 'Low', 'Low', ...]
  Level 1 (ticker): ['MSFT', 'NVDA', 'MSFT', 'NVDA', 'MSFT', 'NVDA', ...]
```

注意：**AAPL 线程收到的数据里根本没有 AAPL！** 它收到的是其他线程请求的 ticker 的数据。

---

### 崩溃的具体位置

```python
# get_level_values(0) 之后，列名带有重复：
# ['Close', 'Close', 'High', 'High', ...]
raw.columns = raw.columns.get_level_values(0)

# raw["Close"] 遇到重复列名时，返回的不是 Series，而是 DataFrame（两列！）
close = raw["Close"].astype(float)   # close 是 DataFrame，不是 Series

# close.iloc[-1] 取最后一行 → 返回一个有两个元素的 Series
# float(Series) → 报错！
current_price = float(close.iloc[-1])
# ❌ cannot convert the series to <class 'float'>
```

---

### 为什么只有 Technical Analyst 受影响

| | Fundamental Analyst | Technical Analyst |
|---|---|---|
| 自定义工具 | `get_free_cash_flow` — 使用 `yf.Ticker().cashflow`（非批处理路径） | `compute_technical_signals` — 使用 `yf.download()`（触发批处理） |
| 失败表现 | 正常工作，~50 秒返回 21,672 字符 | 约 1.5 秒内崩溃，返回 0 字符 |

`get_free_cash_flow` 用的是 `yf.Ticker(ticker).cashflow`，走的是单 ticker 对象接口，**不触发批处理器**。`compute_technical_signals` 用的是 `yf.download()`，**正是批处理器监听的入口**。

---

### 为什么错误被静默吞掉

`compute_technical_signals` 抛出异常后，agno 的工具执行层捕获了它，但 LLM 此时已经返回了**只有 tool call、没有文本内容**的响应（`content = None`）。后续流程：

```
tool 抛出异常
  → agno 捕获，不再继续 LLM 对话轮次
  → model_response.content 仍为 None
  → run_response.content = None
  → traced_run 记录 "Produced response (0 chars)"
  → 下游 Ranking 和 Memo 步骤收到空的 technical analysis
```

整个过程**没有任何错误日志打印到控制台**，表现为静默失败。

---

### 修复方案

将 `yf.download()` 替换为 `yf.Ticker(ticker).history()`：

```python
# 修复前：触发批处理，并发时数据错乱
raw = yf.download(ticker, period="1y", auto_adjust=True, progress=False)

# 修复后：单 ticker 对象接口，不参与批处理
raw = yf.Ticker(ticker).history(period="1y", auto_adjust=True)
```

`Ticker.history()` 每次只请求一个 ticker，返回的 DataFrame 列名始终是普通 Index（`['Close', 'High', ...]`），不会有 MultiIndex 或重复列名问题，在任意并发场景下都安全。
