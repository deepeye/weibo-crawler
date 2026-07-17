# 代理池耗尽后轮询 PROXY_HTTP_URL 设计文档

## 背景

`ProxyManager.get_next_proxy()` 在代理池中的所有 IP 都被标记为失效时，会立即返回 `None`，导致调用方放弃当前请求。实际运行中，上游代理接口 `PROXY_HTTP_URL` 可能会在短暂时间后重新下发可用 IP，因此需要在代理耗尽时主动、高频地重新请求接口，直到拿到新代理。

## 目标

当 `ProxyManager` 内所有代理均失效时，`get_next_proxy()` 应每间隔 1 秒请求一次 `PROXY_HTTP_URL`，直到成功获取新代理并返回给调用方。

## 非目标

- 不修改代理加载、解析、缓存的核心逻辑。
- 不新增配置项（1 秒间隔写死）。
- 不限定最大轮询次数或超时时间。
- 不改变多 worker 之间通过共享缓存文件同步代理的现状。

## 设计决策

### 决策 1：阻塞等待 vs 后台补充

选择 **阻塞等待**：`get_next_proxy()` 在代理耗尽时原地轮询，拿到新代理后才返回。

理由：
- 与需求描述完全一致。
- 调用方当前拿不到可用代理，等待比直接返回 `None` 更符合预期。
- 实现简单，无需维护后台任务和事件通知。

### 决策 2：轮询间隔与超时

- 固定间隔 **1 秒**。
- **无最大等待时间**，轮询到拿到代理为止。

理由：用户明确要求“每间隔 1s 请求一次 … 直到拿到 IP 为止”。

### 决策 3：复用 `load_proxies_from_http()`

轮询时直接调用现有的 `load_proxies_from_http()`，而不是新写一个精简版请求函数。

理由：
- 复用解析、缓存、429 退避、超时重试逻辑。
- 共享缓存文件机制仍然生效：HTTP 接口失败时可能从缓存文件加载到可用代理。

### 决策 4：在 `get_next_proxy()` 内部轮询并持有锁

轮询循环位于 `get_next_proxy()` 的 `async with self._lock` 块内。

理由：
- 实现最直接，状态检查与更新不需要跨锁协调。
- 代理耗尽是异常状态，锁被长期持有的副作用可接受。
- 项目整体风格偏向简单直接。

副作用：
- 轮询期间，同进程内其他需要 `self._lock` 的操作（`mark_proxy_failed`、`get_proxy_status`、自动重载）会被阻塞。

## 架构与数据流

```
调用方 (api.py fetch_with_proxy_retry)
        ↓
get_next_proxy()
        ↓
持有 self._lock
        ↓
检查 self._proxies 是否为空
        ↓
尝试 load_proxies_from_http() （现有逻辑）
        ↓
遍历代理池，跳过 _failed_proxies 中的索引
        ↓
全部失效？
   ├─ 否 → 返回 http://user:pass@ip:port
   └─ 是 → 进入轮询循环
            ↓
      打印日志：代理池耗尽，1s 后重试...
            ↓
      await asyncio.sleep(1)
            ↓
      await load_proxies_from_http()
            ↓
      成功且 self._proxies 非空？
         ├─ 是 → _failed_proxies 已清空，返回下一个可用代理
         └─ 否 → 继续循环
```

## 修改范围

仅修改 `proxy_manager.py` 中的 `ProxyManager.get_next_proxy()` 方法。

### 伪代码

```python
async def get_next_proxy(self) -> str | None:
    async with self._lock:
        if not self._proxies:
            try:
                await self.load_proxies_from_http()
            except Exception as e:
                console.print(f"[red][PID:{pid}] 加载代理异常: {e}[/red]")
                return None

        while True:
            checked = 0
            while checked < len(self._proxies):
                idx = self._current_index % len(self._proxies)
                self._current_index += 1

                if idx in self._failed_proxies:
                    checked += 1
                    continue

                proxy = self._proxies[idx]
                return self._proxy_to_url(proxy)

            # 所有代理都失效，开始 1 秒轮询
            console.print(
                f"[red][PID:{pid}] 代理池耗尽：所有 {len(self._proxies)} 个IP都已失效，"
                f"1s 后重新请求 PROXY_HTTP_URL...[/red]"
            )
            await asyncio.sleep(1)

            try:
                await self.load_proxies_from_http()
            except Exception as e:
                console.print(f"[yellow][PID:{pid}] 轮询加载代理失败: {e}[/yellow]")
```

## 异常处理

| 场景 | 行为 |
|---|---|
| `PROXY_HTTP_URL` 返回空代理列表 | `load_proxies_from_http()` 返回 `False`，sleep 1s 后继续轮询 |
| `PROXY_HTTP_URL` 返回 429 | `load_proxies_from_http()` 内部已做指数退避重试；最终失败后 sleep 1s 继续 |
| `PROXY_HTTP_URL` 超时 | 同 429：内部重试后退避，最终失败则 1s 后继续 |
| 共享缓存文件里还有未过期代理 | `load_proxies_from_http()` 第二步会从文件加载成功，轮询结束 |
| 新代理拿到后立刻又全部失效 | 下次 `get_next_proxy()` 进来会再次检测到全部失效，重新进入 1s 轮询 |

## 测试计划

1. **单元测试脚本 `test_proxy_exhaustion.py`**
   - 初始化 `ProxyManager`，手动注入若干代理；
   - 全部标记为失效；
   - mock `load_proxies_from_http()`：前 2 次返回 `False`，第 3 次注入新代理并返回 `True`；
   - 调用 `get_next_proxy()`，断言最终返回新代理，且至少经历了 2 次 1 秒等待。

2. **集成验证**
   - 启动 API：`uv run python api.py --workers 1`；
   - 通过连续触发风控把代理全部标记失效；
   - 观察日志出现“代理池耗尽，1s 后重新请求 PROXY_HTTP_URL...”并持续每秒打印，直到接口返回新代理；
   - 请求最终成功。

3. **边界：无代理可用时仍不放弃**
   - 若 `PROXY_HTTP_URL` 长期不可用，验证 `get_next_proxy()` 持续轮询，不返回 `None`。

## 风险与注意事项

- 轮询期间 `self._lock` 被长期持有，同进程内 `mark_proxy_failed`、`get_proxy_status`、自动重载会阻塞。
- 多 worker 场景下，每个 worker 的 `ProxyManager` 会独立轮询，这是现有架构决定的。
- 若 `PROXY_HTTP_URL` 持续不可用且缓存文件也已过期，调用方将无限期挂起。
