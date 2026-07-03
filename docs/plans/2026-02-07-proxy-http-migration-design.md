# 代理IP获取方式迁移设计文档

**日期**: 2026-02-07
**状态**: 待实施
**作者**: Claude + 用户协作

---

## 1. 概述

### 1.1 背景
当前代理管理器从 Redis 获取代理列表，需要迁移到 HTTP 接口方式，并实现新的轮询和失效处理机制。

### 1.2 目标
- 从 HTTP 接口获取代理 IP 列表（200个）
- 实现轮询机制和智能失效处理
- 每60秒自动更新代理池（全部覆盖）
- 支持业务级失败检测和重试

---

## 2. 核心架构

### 2.1 整体变更

**移除依赖：**
- 删除 Redis 相关代码（`aioredis`、`_redis_client`）

**新增依赖：**
- `httpx.AsyncClient` - 用于HTTP接口调用

**数据结构调整：**
```python
class ProxyManager:
    _proxies: list[dict]           # [{"ip": "x.x.x.x", "port": "1234"}, ...]
    _available_proxies: set[int]   # 可用代理索引集合
    _current_index: int            # 轮询位置
    _failed_proxies: set[int]      # 本周期失效的代理索引
    _lock: asyncio.Lock            # 并发保护
```

### 2.2 生命周期

1. **启动阶段**：立即调用 HTTP 接口获取代理列表
2. **运行阶段**：每60秒自动更新，全部覆盖旧数据
3. **更新时**：重置 `_failed_proxies` 和 `_available_proxies`

---

## 3. 详细设计

### 3.1 HTTP 接口调用

**端点信息：**
- URL: `http://ip.16yun.cn:817/myip/pl/2ba2af16-4019-4cf1-93e0-7e39dc5037e4/`
- 参数: `s=roeaszjrmy&u=pdmi&format=json`
- 返回格式: `[{"ip": "x.x.x.x", "port": "1234"}, ...]`

**实现方法：**
```python
async def load_proxies_from_http(self) -> bool:
    """从HTTP接口获取代理列表

    Returns:
        成功返回 True，失败返回 False（保留旧代理池）
    """
    url = config.PROXY_HTTP_URL
    params = config.PROXY_HTTP_PARAMS

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(url, params=params)
            response.raise_for_status()
            data = response.json()

            if not isinstance(data, list):
                console.print("[yellow]HTTP接口返回格式错误[/yellow]")
                return False

            # 全部覆盖
            self._proxies = data
            self._failed_proxies.clear()
            self._available_proxies = set(range(len(data)))
            self._current_index = 0

            console.print(f"[green]成功加载 {len(data)} 个代理[/green]")
            return True

    except Exception as e:
        console.print(f"[yellow]HTTP接口调用失败: {e}，保留旧代理池[/yellow]")
        return False
```

### 3.2 轮询机制

**规则：**
1. 按顺序从索引0开始获取代理
2. 自动跳过已失效的代理
3. 到达末尾后从头循环
4. 如果所有代理都失效，返回 None

**实现方法：**
```python
async def get_next_proxy(self) -> str | None:
    """获取下一个可用代理

    Returns:
        代理URL (http://ip:port)，无可用代理返回 None
    """
    async with self._lock:
        if not self._proxies:
            return None

        # 最多检查整个代理池
        checked = 0
        while checked < len(self._proxies):
            idx = self._current_index % len(self._proxies)
            self._current_index += 1

            # 跳过失效代理
            if idx in self._failed_proxies:
                checked += 1
                continue

            proxy = self._proxies[idx]
            return f"http://{proxy['ip']}:{proxy['port']}"

        # 所有代理都失效
        console.print("[red]代理池耗尽：所有IP都已失效[/red]")
        return None
```

### 3.3 失效处理

**失效条件（业务级）：**
- 连接超时、连接被拒绝
- HTTP 4xx/5xx 错误
- 微博风控响应（432, 418, 403, 414）
- 响应超时、网络异常

**失效标记：**
```python
async def mark_proxy_failed(self, proxy_url: str):
    """标记代理失效

    Args:
        proxy_url: 失效的代理URL
    """
    async with self._lock:
        # 从URL反查索引
        for idx, proxy in enumerate(self._proxies):
            url = f"http://{proxy['ip']}:{proxy['port']}"
            if url == proxy_url:
                self._failed_proxies.add(idx)
                console.print(
                    f"[yellow]代理失效 [{idx+1}/{len(self._proxies)}]: "
                    f"{proxy['ip']}:{proxy['port']}[/yellow]"
                )
                break
```

**失效策略：**
- 失效的IP在本周期（60秒）内永久移除
- 下次HTTP接口更新时，所有失效标记清除
- 不会在同一周期内重复使用失败的IP

### 3.4 重试逻辑

**在 api.py 的调用方实现：**
```python
async def fetch_with_proxy_retry(
    url: str,
    max_proxy_retries: int = 10,
    **kwargs
) -> httpx.Response | None:
    """使用代理重试请求，最多尝试10个不同IP"""

    for attempt in range(max_proxy_retries):
        proxy_url = await proxy_manager.get_next_proxy()

        if proxy_url is None:
            console.print("[red]无可用代理，放弃请求[/red]")
            break

        try:
            async with httpx.AsyncClient(proxy=proxy_url, timeout=30.0) as client:
                response = await client.get(url, **kwargs)

                # 业务级失败检测
                if response.status_code in [432, 418, 403, 414]:
                    await proxy_manager.mark_proxy_failed(proxy_url)
                    continue

                if response.status_code >= 400:
                    await proxy_manager.mark_proxy_failed(proxy_url)
                    continue

                # 成功
                return response

        except (httpx.ConnectError, httpx.TimeoutException, Exception) as e:
            await proxy_manager.mark_proxy_failed(proxy_url)
            console.print(f"[yellow]代理连接失败: {e}[/yellow]")
            continue

    return None
```

### 3.5 自动更新

**后台任务：**
```python
async def _auto_reload_loop(self):
    """每60秒自动更新代理池"""
    while not self._should_stop:
        try:
            await asyncio.sleep(self._reload_interval)

            if not self._should_stop:
                old_count = len(self._proxies)
                old_failed = len(self._failed_proxies)

                success = await self.load_proxies_from_http()

                if success:
                    new_count = len(self._proxies)
                    console.print(
                        f"[cyan]代理池更新: {old_count}个 ({old_failed}失效) -> "
                        f"{new_count}个 (全部可用)[/cyan]"
                    )

        except asyncio.CancelledError:
            break
        except Exception as e:
            console.print(f"[red]自动更新异常: {e}[/red]")
```

---

## 4. 配置调整

### 4.1 config.py 新增配置

```python
# ==================== HTTP代理接口配置 ====================
PROXY_HTTP_URL = "http://ip.16yun.cn:817/myip/pl/2ba2af16-4019-4cf1-93e0-7e39dc5037e4/"
PROXY_HTTP_PARAMS = {
    "s": "roeaszjrmy",
    "u": "pdmi",
    "format": "json"
}
PROXY_RELOAD_INTERVAL = 60  # 秒
```

### 4.2 废弃配置（标记但保留）

```python
# ==================== 废弃：Redis 代理池配置 ====================
# 已迁移到 HTTP 接口，以下配置不再使用
# PROXY_REDIS_HOST = "..."
# PROXY_REDIS_PORT = 6379
# ...
```

---

## 5. 监控和调试

### 5.1 新增API端点

```python
@app.get("/proxy/status")
async def get_proxy_status():
    """获取代理池实时状态"""
    total = len(proxy_manager._proxies)
    failed = len(proxy_manager._failed_proxies)

    return {
        "total_proxies": total,
        "available_count": total - failed,
        "failed_count": failed,
        "failed_rate": failed / total if total > 0 else 0,
        "current_index": proxy_manager._current_index,
        "sample_proxies": proxy_manager._proxies[:5]
    }
```

### 5.2 关键指标

**健康指标：**
- `failed_rate < 20%` - 正常
- `failed_rate 20-50%` - 警告
- `failed_rate > 50%` - 严重

**告警触发：**
- 失效率超过80% → 红色警告
- HTTP接口连续3次失败 → 黄色警告
- 所有代理耗尽 → 红色警告

### 5.3 日志输出规范

使用 Rich Console 带颜色输出：
```python
# 成功事件
console.print(f"[green][PID:{pid}] 成功加载 200 个代理[/green]")

# 警告事件
console.print(f"[yellow][PID:{pid}] HTTP接口失败，保留旧代理池[/yellow]")

# 错误事件
console.print(f"[red][PID:{pid}] 代理池耗尽[/red]")

# 信息事件
console.print(f"[cyan][PID:{pid}] 代理池更新: 180个可用[/cyan]")
```

---

## 6. 测试计划

### 6.1 test_proxy.py 更新

**测试用例：**
1. HTTP接口连通性测试
2. JSON格式解析测试
3. 轮询顺序验证（0→1→2→...→199→0）
4. 失效标记和跳过逻辑
5. 60秒自动更新验证
6. 全部失效场景处理

### 6.2 test_http_proxy.py 更新

**测试用例：**
1. 单个代理连接测试
2. 10次重试逻辑验证
3. 失效标记是否生效
4. 并发请求安全性（多worker）

### 6.3 集成测试

**验证流程：**
1. 启动服务，检查是否成功加载200个代理
2. 发起搜索请求，观察代理轮询
3. 模拟代理失效，验证自动切换
4. 等待60秒，确认代理池更新
5. 检查 `/proxy/status` 端点数据

---

## 7. 实施步骤

### 7.1 准备阶段
1. 备份当前 `proxy_manager.py`
2. 创建测试分支
3. 更新 `config.py` 添加新配置

### 7.2 核心开发
1. 修改 `ProxyManager` 类：
   - 删除 Redis 相关代码
   - 实现 `load_proxies_from_http()`
   - 重构 `get_next_proxy()`
   - 新增 `mark_proxy_failed()`
2. 修改 `_auto_reload_loop()`
3. 更新启动/关闭逻辑

### 7.3 调用方调整
1. 修改 `api.py` 的 `fetch_with_proxy_retry()`
2. 添加 `/proxy/status` 端点
3. 更新启动事件处理

### 7.4 测试验证
1. 更新测试脚本
2. 运行单元测试
3. 运行集成测试
4. 压力测试（模拟高并发）

### 7.5 文档更新
1. 更新 `CLAUDE.md`
2. 更新 `README.md`
3. 添加迁移说明

---

## 8. 风险和缓解

### 8.1 风险点

**R1: HTTP接口不稳定**
- 影响：无法获取代理，服务中断
- 缓解：保留旧代理池，记录警告日志

**R2: 代理质量差导致大量失效**
- 影响：可用代理快速减少
- 缓解：监控失效率，超过80%告警

**R3: 60秒更新延迟导致服务间隙**
- 影响：旧代理全部失效后需等待更新
- 缓解：定期检查可用数量，提前告警

**R4: 并发请求导致锁竞争**
- 影响：性能下降
- 缓解：使用 `asyncio.Lock` 保护关键区域，锁粒度最小化

### 8.2 回滚计划

如果迁移失败，可快速回滚到 Redis 方案：
1. 恢复备份的 `proxy_manager.py`
2. 恢复 `config.py` 中的 Redis 配置
3. 重启服务

---

## 9. 兼容性

### 9.1 保留方法（向后兼容）

```python
# 继续支持，但实现改变
async def get_all_proxies(self) -> list[dict]:
    """返回所有代理列表"""
    return self._proxies

async def reload(self) -> bool:
    """手动重载，调用HTTP接口"""
    return await self.load_proxies_from_http()
```

### 9.2 废弃方法（需删除）

```python
# 完全删除
async def _get_redis(self) -> aioredis.Redis
async def load_proxies(self) -> bool  # 替换为 load_proxies_from_http
```

---

## 10. 总结

### 10.1 关键改进
- ✅ 简化架构，移除 Redis 依赖
- ✅ 实现智能轮询和失效处理
- ✅ 支持业务级失败检测
- ✅ 每60秒自动更新，全部覆盖
- ✅ 完善的监控和告警机制

### 10.2 预期效果
- 代理管理更简单，减少外部依赖
- 失效处理更精准，避免重复使用坏IP
- 自动更新确保代理池新鲜度
- 最多10次重试提高请求成功率

### 10.3 后续优化方向
- 代理质量评分（根据成功率）
- 动态调整更新频率
- 代理池预热机制
- 分布式代理池共享
