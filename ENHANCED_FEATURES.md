# 增强型反风控功能说明

## 📋 概述

`fetch_with_proxy_retry` 方法已升级，新增以下反风控能力：

1. **令牌桶算法频率限制**
2. **多样化浏览器指纹轮换**
3. **请求行为监控和统计**
4. **自适应速率调整**
5. **风控检测和自动降速**

## 🚀 核心特性

### 1. 令牌桶频率限制（RateLimiter）

基于令牌桶算法的精确频率控制，避免请求过于集中：

```python
class RateLimiter:
    capacity: int = 10          # 令牌桶容量（最大并发数）
    refill_rate: float = 1.0    # 令牌恢复速率（个/秒）
```

**工作原理**：
- 初始有 10 个令牌
- 每秒恢复 1 个令牌
- 每次请求消耗 1 个令牌
- 令牌不足时等待，超时返回 429 错误

**自适应调整**：
- 成功率 < 50%：降低速率（`refill_rate * 0.5`，`capacity - 2`）
- 成功率 > 80%：适当提升（`refill_rate * 1.2`，`capacity + 1`）

### 2. 请求行为监控（RequestMonitor）

统计最近 100 个请求的行为特征：

```python
监控指标：
- total_requests: 总请求数
- success_count: 成功次数
- blocked_count: 被封次数（432/418/403/401）
- error_count: 错误次数
- success_rate: 成功率
- blocked_rate: 被封率
- average_duration: 平均响应时间
```

**降速触发条件**：
- 被封率 > 30%
- 成功率 < 50%

触发后自动进入 3-8 秒随机延迟。

### 3. 多样化 HTTP 指纹

每次请求随机生成新的 HTTP 指纹：

```python
随机元素：
- User-Agent（10+ 种）
- Accept-Language（4 种）
- Accept-Encoding（3 种）
- Sec-CH-UA（3 种）
- Sec-CH-UA-Platform（3 种）
```

**指纹更换策略**：
- 每次请求自动更换
- 代理重试时强制更换
- 风控拦截后立即更换

### 4. 风控检测和自动冷却

检测到风控状态码后自动处理：

```python
风控状态码：432, 418, 403, 401

处理流程：
1. 记录为 blocked 请求
2. 调整速率限制器
3. 进入冷却期（config.CAPTCHA_COOLDOWN + 随机 2-5s）
4. 切换代理和指纹重试
```

### 5. 行为抖动

模拟人类操作的不确定性：

```python
- 请求前：随机延迟 0.1-0.5 秒
- 重试间隔：随机延迟 0.5-1.5 秒
- 降速期：随机延迟 3-8 秒
```

## ⚙️ 配置选项

在 `config.py` 中新增配置项：

```python
# 令牌桶容量（最大并发请求数）
RATE_LIMITER_CAPACITY = 10

# 令牌恢复速率（每秒恢复的令牌数）
RATE_LIMITER_REFILL_RATE = 1.0

# 请求监控窗口大小（统计最近 N 个请求）
REQUEST_MONITOR_WINDOW_SIZE = 100

# 风控检测阈值
BLOCKED_RATE_THRESHOLD = 0.3  # 被封率超过 30% 触发降速
SUCCESS_RATE_THRESHOLD = 0.5  # 成功率低于 50% 触发降速
```

**调优建议**：
- **保守策略**：`CAPACITY=5`, `REFILL_RATE=0.5`（更慢但更安全）
- **激进策略**：`CAPACITY=15`, `REFILL_RATE=2.0`（更快但风控风险高）
- **平衡策略**（默认）：`CAPACITY=10`, `REFILL_RATE=1.0`

## 📊 监控端点

### GET `/fingerprint`

返回增强的监控数据：

```json
{
  "enhanced_monitor": {
    "total_http_requests": 152,
    "success_count": 138,
    "blocked_count": 8,
    "error_count": 6,
    "success_rate": "90.8%",
    "blocked_rate": "5.3%",
    "average_response_time": "1.23s",
    "should_slow_down": false
  },
  "rate_limiter": {
    "current_tokens": 7.82,
    "capacity": 10,
    "refill_rate": 1.0,
    "status": "healthy"
  }
}
```

**状态指标说明**：
- `status: "healthy"` - 令牌充足（> 30% capacity）
- `status: "limited"` - 令牌不足（≤ 30% capacity）
- `should_slow_down: true` - 触发降速条件

## 🧪 测试

运行测试脚本：

```bash
# 1. 启动 API 服务
uv run python api.py --workers 1

# 2. 运行测试（新终端）
uv run python test_enhanced_fetch.py
```

测试内容：
- 获取初始状态
- 发送 5 个测试请求（观察频率限制）
- 查看更新后的监控数据

## 🔍 日志示例

```
[PID:12345][用户微博] 尝试代理 1/5: http://proxy-server:8080
[PID:12345][用户微博] 代理请求成功 (耗时 1.23s)

[PID:12345][用户微博] 触发风控 (HTTP 432)
[PID:12345][用户微博] 进入风控冷却期 6.8s

[PID:12345][用户微博] 检测到高风控风险，自动降速 5.2s
```

## 📈 性能影响

**内存占用**：
- RateLimiter: ~1KB
- RequestMonitor: ~50KB（100 条记录）

**延迟影响**：
- 正常请求：+0.1-0.5s（行为抖动）
- 频率限制：等待令牌（最多 30s 超时）
- 风控降速：+3-8s

**优点**：
- ✅ 显著降低被封概率
- ✅ 自动适应风控强度
- ✅ 实时监控请求健康度
- ✅ 无需手动调参

**缺点**：
- ⚠️ 请求速度略有下降
- ⚠️ 内存占用小幅增加

## 🛠️ 故障排查

### 问题 1：频繁触发 429 错误

**原因**：令牌桶容量或恢复速率过低

**解决方案**：
```python
# config.py
RATE_LIMITER_CAPACITY = 15       # 增加容量
RATE_LIMITER_REFILL_RATE = 1.5   # 提高恢复速率
```

### 问题 2：仍然频繁被封（432）

**原因**：风控检测阈值过于宽松

**解决方案**：
```python
# config.py
BLOCKED_RATE_THRESHOLD = 0.2  # 降低到 20%
SUCCESS_RATE_THRESHOLD = 0.7  # 提高到 70%
```

### 问题 3：请求速度过慢

**原因**：频繁触发降速机制

**解决方案**：
1. 检查 `/fingerprint` 端点查看 `should_slow_down` 状态
2. 如果 `blocked_rate` 高，说明代理质量差，需更换代理池
3. 如果 `success_rate` 低，检查网络连接和目标站点状态

## 📝 最佳实践

1. **启动服务后**：
   - 访问 `/fingerprint` 查看初始状态
   - 观察 `rate_limiter.status` 是否健康

2. **运行期间**：
   - 定期监控 `enhanced_monitor.blocked_rate`
   - 被封率 > 10% 时考虑降低请求速率

3. **调优参数**：
   - 从保守策略开始
   - 观察 1-2 小时后的 `success_rate`
   - 成功率 > 90% 时可适当提升速率

4. **代理管理**：
   - 使用高质量代理池
   - 定期轮换代理
   - 及时剔除被封的代理

## 🔗 相关文件

- `api.py`: 核心实现（RateLimiter、RequestMonitor、fetch_with_proxy_retry）
- `config.py`: 配置选项
- `test_enhanced_fetch.py`: 测试脚本
- `AGENTS.md`: 项目开发指南
