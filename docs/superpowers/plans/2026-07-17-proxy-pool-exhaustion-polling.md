# 代理池耗尽后轮询 PROXY_HTTP_URL Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修改 `proxy_manager.py` 的 `ProxyManager.get_next_proxy()`，当所有代理失效时每 1 秒轮询 `PROXY_HTTP_URL`，直到拿到新代理并返回。

**Architecture:** 在 `get_next_proxy()` 内部检测到全部代理失效后，进入 `while True` 循环：sleep 1 秒，调用 `load_proxies_from_http()`，成功后返回下一个可用代理。复用现有解析、缓存和退避逻辑。同步阻塞调用方。

**Tech Stack:** Python 3.10+, asyncio, httpx, rich

## Global Constraints

- 1 秒轮询间隔写死，不新增配置项。
- 不复用/精简 `load_proxies_from_http()`，直接调用完整版。
- 无最大轮询次数或超时。
- 轮询在 `get_next_proxy()` 内部并持有 `self._lock`。
- 不修改 `config.py`、`api.py` 或现有测试脚本。
- 遵循现有代码风格：中文文档字符串、Rich 彩色日志、Python 3.10+ 类型语法。

---

## File Structure

- **`proxy_manager.py`**（修改）
  - 修改 `ProxyManager.get_next_proxy()`：代理池全部失效时进入 1 秒轮询循环。
- **`test_proxy_exhaustion.py`**（新建）
  - 根目录可执行脚本，验证代理耗尽后 `get_next_proxy()` 会轮询直到拿到新代理。

---

### Task 1: 编写会失败的单元测试

**Files:**
- Create: `test_proxy_exhaustion.py`

**Interfaces:**
- Consumes: `ProxyManager.get_next_proxy()`, `ProxyManager.mark_proxy_failed()`
- Produces: N/A

- [ ] **Step 1: 创建测试脚本**

在仓库根目录创建 `test_proxy_exhaustion.py`，内容如下：

```python
"""
测试代理池全部失效后的轮询行为
"""

import asyncio
import time
from proxy_manager import ProxyManager


async def test_exhaustion_polling():
    """全部代理失效后，get_next_proxy 应每 1 秒轮询，直到拿到新代理"""
    pm = ProxyManager()

    # 注入两个代理并全部标记失效
    pm._proxies = [
        {"ip": "1.1.1.1", "port": "8080"},
        {"ip": "2.2.2.2", "port": "8080"},
    ]
    pm._failed_proxies = {0, 1}

    call_count = 0

    async def fake_load_proxies():
        nonlocal call_count
        call_count += 1
        # 前两次返回失败，第三次注入新代理
        if call_count >= 3:
            pm._proxies = [{"ip": "9.9.9.9", "port": "9999"}]
            pm._failed_proxies.clear()
            pm._current_index = 0
            return True
        return False

    pm.load_proxies_from_http = fake_load_proxies

    start = time.time()
    proxy = await pm.get_next_proxy()
    elapsed = time.time() - start

    assert proxy is not None, "应返回新代理"
    assert "9.9.9.9:9999" in proxy, f"返回的代理 URL 不正确: {proxy}"
    assert call_count >= 3, f"轮询次数不足: {call_count}"
    assert elapsed >= 2.0, f"等待时间不足 2 秒: {elapsed:.2f}s"
    print(f"✅ 测试通过：第 {call_count} 次轮询拿到代理，共等待 {elapsed:.2f}s")


if __name__ == "__main__":
    asyncio.run(test_exhaustion_polling())
```

- [ ] **Step 2: 运行测试，确认当前失败**

```bash
uv run python test_proxy_exhaustion.py
```

Expected: 测试失败（`AssertionError: 应返回新代理` 或类似），因为当前 `get_next_proxy()` 在代理耗尽时直接返回 `None`。

---

### Task 2: 实现轮询逻辑

**Files:**
- Modify: `proxy_manager.py:307-344`

**Interfaces:**
- Consumes: `ProxyManager.load_proxies_from_http()`
- Produces: `ProxyManager.get_next_proxy()` 行为变更

- [ ] **Step 1: 修改 `get_next_proxy()`**

将 `proxy_manager.py` 中 `get_next_proxy()` 方法替换为：

```python
    async def get_next_proxy(self) -> str | None:
        """获取下一个可用代理

        自动跳过失效代理，循环轮询。当所有代理失效时，每 1 秒重新请求
        PROXY_HTTP_URL，直到拿到新代理。

        Returns:
            代理URL (http://ip:port)，无可用代理时阻塞轮询直到拿到为止
        """
        async with self._lock:
            if not self._proxies:
                # 尝试加载代理
                try:
                    success = await self.load_proxies_from_http()
                    if not success or not self._proxies:
                        console.print(f"[red][PID:{pid}] 无法加载代理，返回 None[/red]")
                        return None
                except Exception as e:
                    console.print(f"[red][PID:{pid}] 加载代理异常: {e}[/red]")
                    return None

            while True:
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
                    return self._proxy_to_url(proxy)

                # 所有代理都失效，1 秒后重新请求接口
                failed_rate = len(self._failed_proxies) / len(self._proxies) * 100
                console.print(
                    f"[red][PID:{pid}] 代理池耗尽：所有 {len(self._proxies)} 个IP都已失效 "
                    f"({failed_rate:.1f}%)，1s 后重新请求 PROXY_HTTP_URL...[/red]"
                )
                await asyncio.sleep(1)

                try:
                    await self.load_proxies_from_http()
                except Exception as e:
                    console.print(f"[yellow][PID:{pid}] 轮询加载代理失败: {e}[/yellow]")
```

- [ ] **Step 2: 运行测试，确认通过**

```bash
uv run python test_proxy_exhaustion.py
```

Expected:
```
✅ 测试通过：第 3 次轮询拿到代理，共等待 2.0Xs
```

- [ ] **Step 3: 运行现有测试，确保未破坏原有行为**

```bash
uv run python test_proxy.py
```

Expected: 现有代理池测试通过，无回归。

- [ ] **Step 4: 提交代码**

```bash
git add proxy_manager.py test_proxy_exhaustion.py
git commit -m "feat: poll PROXY_HTTP_URL every 1s when proxy pool exhausted"
```

---

## Self-Review

### Spec Coverage

| Spec 要求 | 对应 Task |
|---|---|
| 代理全部失效后每 1 秒轮询 `PROXY_HTTP_URL` | Task 2 Step 1 |
| 拿到新代理后返回 | Task 2 Step 1 |
| 复用 `load_proxies_from_http()` | Task 2 Step 1 |
| 1 秒间隔写死，不新增配置 | Task 2 Step 1 |
| 无最大轮询次数/超时 | Task 2 Step 1 |
| 单元测试验证轮询行为 | Task 1 |

### Placeholder Scan

- 无 "TBD"、"TODO"、"implement later"。
- 无 "add appropriate error handling" 等模糊描述。
- 所有代码块包含完整可运行代码。

### Type Consistency

- `get_next_proxy()` 签名保持 `async def get_next_proxy(self) -> str | None`。
- `load_proxies_from_http()` 仍返回 `bool`，未变更。
- 测试中断言使用 `proxy is not None` 和字符串包含检查，避免暴露认证信息。
