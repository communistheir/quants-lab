# Docker 中运行 PMM Dynamic 策略参数优化指南

## 概述

本文档详细介绍如何通过 Docker 运行 `pmm_dynamic` 策略的参数优化任务。参数优化基于历史回测数据，使用贝叶斯优化算法搜索最佳参数组合。

---

## 快速开始

### 方式一：使用 Makefile（推荐）

最简单的方式：

```bash
# 构建 Docker 镜像（首次）
make build

# 启动 MongoDB（必需，存储优化结果）
make run-db

# 运行 PMM Dynamic 优化任务
make trigger-task task=pmm_dynamic_optimization config=template_pmm_dynamic_optimization.yml
```

---

## 详细部署方式

### 方式二：直接使用 Docker 命令

如果你想更细致地控制，可以直接运行：

#### 1. 构建镜像

```bash
docker build -t hummingbot/quants-lab -f Dockerfile .
```

#### 2. 启动 MongoDB（如果还没启动）

```bash
docker compose -f docker-compose-db.yml up -d
```

#### 3. 运行优化任务（前台运行，可看日志）

```bash
docker run --rm \
  -v $(pwd)/app/outputs:/quants-lab/app/outputs \
  -v $(pwd)/config:/quants-lab/config \
  -v $(pwd)/app:/quants-lab/app \
  -v $(pwd)/research_notebooks:/quants-lab/research_notebooks \
  --env-file .env \
  --network host \
  hummingbot/quants-lab \
  conda run --no-capture-output -n quants-lab python3 cli.py trigger-task \
    --task pmm_dynamic_optimization \
    --config config/template_pmm_dynamic_optimization.yml
```

---

### 方式三：后台运行（长期运行）

如果你想让优化任务在后台持续运行（每12小时自动触发一次）：

```bash
# 1. 启动后台任务
make run-tasks config=template_pmm_dynamic_optimization.yml

# 2. 查看实时日志
make logs-tasks
```

**工作流程：**
- 每12小时自动下载最新 K 线数据（`candles_downloader` 任务）
- 数据下载完后自动触发 PMM Dynamic 优化（`pmm_dynamic_optimization` 任务）
- 优化结果自动存储到 MongoDB

---

## 完整部署流程

### 第一次完整部署

```bash
# 第1步：构建镜像
make build

# 第2步：启动数据库
make run-db

# 第3步：查看 MongoDB 日志确保启动成功
make logs-db

# 第4步：运行优化任务（后台模式）
make run-tasks config=template_pmm_dynamic_optimization.yml

# 第5步：查看任务日志
make logs-tasks
```

### 停止任务

```bash
# 停止所有后台任务
make stop-tasks
```

---

## 配置调整

在 `config/template_pmm_dynamic_optimization.yml` 中，根据需求调整优化参数：

```yaml
tasks:
  pmm_dynamic_optimization:
    config:
      # 策略配置
      connector_name: "binance"      # 交易所
      resolution: "3m"               # K线周期
      
      # 交易配置
      selected_pairs:
        - "BTC-USDT"                # 交易对（可添加多个）
      
      # 优化配置
      n_trials: 50                  # 尝试50个参数组合（更多=优化更久）
      lookback_days: 30             # 用过去30天数据优化（更多=计算更久）
      end_time_buffer_hours: 6      # 跳过最后6小时（避免不完整数据）
      study_name: "pmm_dynamic_v1"  # 优化研究名称
```

### 参数说明

| 参数 | 说明 | 建议值 |
|------|------|--------|
| `n_trials` | 参数组合尝试次数 | 10-100（越多越精确，越耗时） |
| `lookback_days` | 回测历史数据天数 | 7-30（需包含不同行情）|
| `connector_name` | 交易所名称 | binance, okx, 等 |
| `resolution` | K线周期 | 3m, 5m, 1h, 1d |

---

## 查看优化结果

### 1. 查看容器日志

```bash
# 查看特定容器日志
docker logs quants-lab-template_pmm_dynamic_optimization -f

# 查看最近100行日志
docker logs quants-lab-template_pmm_dynamic_optimization -n 100

# 查看过去10分钟的日志
docker logs quants-lab-template_pmm_dynamic_optimization --since 10m
```

### 2. 访问 MongoDB UI

- **URL**: http://localhost:28081
- **用户名**: admin
- **密码**: changeme

**查看优化结果步骤：**
1. 连接到 MongoDB
2. 选择数据库：`quants_lab`
3. 找到集合：`optimization_studies` 或 `optimization_trials`
4. 查看最佳参数和试验结果

### 3. 最佳参数输出

代码中会自动输出最佳参数到日志（见 [pmm_dynamic_backtesting_task.py](../app/tasks/backtesting/pmm_dynamic_backtesting_task.py#L210)）：

```python
best_params = self.optimizer.get_study_best_params(study_name)
logging.info(f"Best params for {trading_pair}: {best_params}")
```

**日志示例：**
```
Best params for BTC-USDT: {
  'buy_spread_1': 0.75,
  'buy_spread_2': 1.5,
  'buy_spread_3': 3.0,
  'sell_spread_1': 0.75,
  'sell_spread_2': 1.5,
  'sell_spread_3': 3.0,
  'macd_fast': 12,
  'macd_slow': 26,
  'macd_signal': 9,
  'natr_length': 14,
  'take_profit': 0.03,
  'stop_loss': 0.02
}
```

---

## 优化指标说明

### Sharpe Ratio（夏普比率）

代码默认使用 **Sharpe Ratio** 作为优化目标，这是衡量风险调整收益的常用指标。

**含义：**
- 衡量单位风险下的超额收益
- 值越高，表示收益相对于风险越稳定
- 公式: $(E[R_p] - R_f) / \sigma_p$

### 其他关键指标

| 指标 | 说明 | 理想值 |
|------|------|--------|
| **Sharpe Ratio** | 风险调整收益 | > 1.0 |
| **Max Drawdown** | 最大回撤 | < 20% |
| **Win Rate** | 盈利交易比例 | > 50% |
| **Total Return** | 总收益率 | > 5% |

---

## 故障排查

### 问题 1: MongoDB 连接失败

```bash
# 检查 MongoDB 容器状态
docker compose -f docker-compose-db.yml ps

# 查看 MongoDB 日志
make logs-db

# 重新启动数据库
make stop-db && make run-db
```

### 问题 2: 镜像构建失败

```bash
# 清理旧镜像
docker rmi hummingbot/quants-lab

# 重新构建（加上 --no-cache 忽略缓存）
docker build --no-cache -t hummingbot/quants-lab -f Dockerfile .
```

### 问题 3: 任务超时

如果 K 线数据很多或参数搜索空间很大，任务可能超时。

**解决方案：**
```bash
# 减少优化参数
n_trials: 10          # 从 50 改为 10

# 减少回测周期
lookback_days: 7      # 从 30 改为 7

# 增加超时时间（在 YAML 中）
timeout_seconds: 57600  # 16 小时
```

### 问题 4: 无法找到 K 线数据

确保数据已下载。运行数据下载任务：

```bash
make trigger-task task=candles_downloader config=template_pmm_dynamic_optimization.yml
```

或等待自动任务在 12 小时后执行。

### 问题 5: Docker 权限错误

如果遇到权限问题：

```bash
# macOS 用户通常无需 sudo
# 如果遇到问题，可以：
sudo chmod 666 /var/run/docker.sock

# 或添加用户到 docker 组
sudo usermod -aG docker $USER
```

---

## 进阶用法

### 1. 同时优化多个交易对

```yaml
selected_pairs:
  - "BTC-USDT"
  - "ETH-USDT"
  - "SOL-USDT"
```

每个交易对会独立优化，生成独立的参数集。

### 2. 自定义优化目标

编辑 [pmm_dynamic_backtesting_task.py](../app/tasks/backtesting/pmm_dynamic_backtesting_task.py) 中的 `generate_config()` 方法，修改参数搜索空间。

**示例：扩大 spread 范围**

```python
buy_spread_1 = trial.suggest_float("buy_spread_1", 0.1, 3.0, step=0.1)  # 更宽的范围
```

### 3. 本地运行（不使用 Docker）

```bash
# 启用源代码模式（使用本地 Python 环境）
make trigger-task task=pmm_dynamic_optimization config=template_pmm_dynamic_optimization.yml source=1
```

**前提条件：**
- 本地已安装 conda 环境（运行 `make install`）
- MongoDB 已启动（运行 `make run-db`）

---

## 部署前检查清单

- [ ] Docker 已安装且运行正常：`docker --version`
- [ ] Docker Compose 已安装：`docker-compose --version`
- [ ] `.env` 文件已配置（包含 API 密钥等）
- [ ] 交易对数据已下载到 `data/candles/` 目录
- [ ] MongoDB 容器能成功启动
- [ ] 配置文件 `template_pmm_dynamic_optimization.yml` 已正确设置

---

## 性能优化建议

### 1. 并行优化多交易对

目前任务串行处理。若想并行优化，可创建多个任务容器：

```bash
# 在不同终端运行
make trigger-task task=pmm_dynamic_optimization config=template_pmm_dynamic_optimization.yml &
# （修改配置为另一个交易对后再运行）
make trigger-task task=pmm_dynamic_optimization config=template_pmm_dynamic_optimization.yml &
```

### 2. 缓存优化

Dockerfile 已优化缓存策略，条件编译层以加快重建速度。

### 3. 资源限制

若需限制 Docker 资源占用：

```bash
docker run --rm \
  --memory 4g \
  --cpus 2 \
  ... (其他参数)
```

---

## 相关文件

| 文件 | 说明 |
|------|------|
| [pmm_dynamic_backtesting_task.py](../app/tasks/backtesting/pmm_dynamic_backtesting_task.py) | 优化任务主文件 |
| [template_pmm_dynamic_optimization.yml](../config/template_pmm_dynamic_optimization.yml) | 任务配置文件 |
| [Dockerfile](../Dockerfile) | Docker 镜像定义 |
| [docker-compose-db.yml](../docker-compose-db.yml) | MongoDB 容器编排 |
| [Makefile](../Makefile) | 便捷命令集 |

---

## FAQ

### Q: 优化需要多长时间？

**A:** 取决于参数：
- `n_trials=10, lookback_days=7`: 约 10-20 分钟
- `n_trials=50, lookback_days=30`: 约 1-3 小时
- `n_trials=100, lookback_days=60`: 约 4-8 小时

### Q: 优化的参数可以直接用于实盘吗？

**A:** 不建议。建议：
1. 先用 out-of-sample 数据验证（后向测试）
2. 用小资金在实盘纸交易 1-2 周观察表现
3. 根据实盘反馈微调参数

### Q: 如何导出优化结果？

**A:** 通过 MongoDB UI 导出，或编写脚本访问 MongoDB：

```python
from pymongo import MongoClient

client = MongoClient("mongodb://admin:admin@localhost:27017/quants_lab")
db = client.quants_lab
studies = db.optimization_studies.find_one()
print(studies)
```

### Q: 能否修改已优化的参数范围？

**A:** 可以。编辑 [pmm_dynamic_backtesting_task.py](../app/tasks/backtesting/pmm_dynamic_backtesting_task.py) 中 `generate_config()` 方法的 `trial.suggest_*()` 调用。修改后需重新构建 Docker 镜像。

---

## 支持

遇到问题？

1. 查看日志：`docker logs <container_id>`
2. 检查 MongoDB 状态：`make logs-db`
3. 验证配置：`python cli.py validate-config --config config/template_pmm_dynamic_optimization.yml`

---

**最后更新**: 2025年12月24日
