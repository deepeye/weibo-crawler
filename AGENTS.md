# AGENTS.md - Agent 指南

本文件为在此代码库中工作的 agentic coding agents 提供指南。

## 项目概述

微博搜索爬虫 - 基于 Playwright 和 FastAPI 的异步采集服务，支持代理轮询、浏览器指纹管理、验证码自动处理。

## 开发环境

- Python 版本：3.10+
- 包管理器：uv
- 主要依赖：playwright, fastapi, playwright-stealth, rich, httpx, aiomysql, redis

## 构建和运行命令

### 启动服务

```bash
# 推荐：使用虚拟显示器（服务器环境）
xvfb-run -a uv run python api.py --workers 4

# 本地开发（无头模式）
uv run python api.py --workers 2

# 如果要用有头模式，修改 config.py: HEADLESS = False
```

### 重启服务

```bash
./restart_api.sh        # 重启服务（默认）
./restart_api.sh start  # 启动服务
./restart_api.sh stop   # 停止服务
./restart_api.sh status # 查看状态
./restart_api.sh logs   # 查看实时日志
```

### 测试命令

```bash
# 测试代理管理器
uv run python test_proxy.py

# 测试 HTTP 代理连接
uv run python test_http_proxy.py

# 运行单个测试（如果使用 pytest）
# uv run pytest tests/test_xxx.py::test_name -v

# 代码格式化
black .
```

## 代码风格指南

### 导入顺序

按照以下顺序组织导入（用空行分隔）：
1. 标准库
2. 第三方库
3. 本地模块

```python
import asyncio
import json
from pathlib import Path

from fastapi import FastAPI, HTTPException
from playwright.async_api import async_playwright

import config
from proxy_manager import proxy_manager
```

### 类型提示

使用现代 Python 3.10+ 类型注解语法，避免 `Optional`：

```python
# ✅ 推荐
def get_fingerprint() -> dict | None:
    pass

async def search(keyword: str, max_pages: int = 5) -> list[dict]:
    pass

# ❌ 避免
from typing import Optional, List, Dict
def get_fingerprint() -> Optional[Dict]:
    pass
```

### 命名约定

- 类名：`PascalCase`（如 `WeiboCrawler`, `ProxyManager`）
- 函数和变量：`snake_case`（如 `load_proxies`, `max_pages`）
- 常量：`UPPER_SNAKE_CASE`（如 `REQUEST_DELAY_BASE`, `HEADLESS`）
- 私有方法：`_snake_case`（如 `_get_redis`, `_reload_loop`）

### 文档字符串

所有公共函数和类必须包含中文文档字符串：

```python
async def get_next_proxy(self, max_retries: int = 3) -> Optional[str]:
    """轮询获取下一个代理

    Args:
        max_retries: 最大重试次数，用于跳过可能失效的代理

    Returns:
        代理 URL 字符串，失败返回 None
    """
    pass
```

### 错误处理

- 使用 try-except 捕获预期异常
- 使用 Rich 库的彩色输出记录错误（`[red]`、`[yellow]`）
- 日志输出中包含进程 PID 便于追踪

```python
try:
    await load_data()
except Exception as e:
    pid = os.getpid()
    console.print(f"[red][PID:{pid}] 加载失败: {e}[/red]")
    return None
```

### 异步编程

- 所有 I/O 操作必须使用 `async/await`
- 使用 `asyncio.Lock()` 保护共享状态
- 使用 `asyncio.sleep()` 而不是 `time.sleep()`

```python
# ✅ 正确
async with self._lock:
    result = await fetch_data()
    await asyncio.sleep(1)

# ❌ 错误
with self._lock:  # 缺少 async
    result = fetch_data()
    time.sleep(1)  # 阻塞事件循环
```

### 配置管理

所有配置项统一放在 `config.py`，避免硬编码：

```python
# ✅ 推荐
delay = config.REQUEST_DELAY_BASE + random.uniform(0, config.REQUEST_DELAY_RANDOM)

# ❌ 避免
delay = 1.0 + random.uniform(0, 2.0)
```

### 日志输出

使用 Rich Console 输出彩色日志：
- `[cyan]` - 信息提示
- `[green]` - 成功消息
- `[yellow]` - 警告
- `[red]` - 错误
- `[dim]` - 次要信息

```python
console.print(f"[cyan]正在初始化浏览器...[/cyan]")
console.print(f"[green]成功加载 {len(proxies)} 个代理[/green]")
console.print(f"[yellow]第 {page_num} 页无数据，停止翻页[/yellow]")
console.print(f"[red]请求失败: {e}[/red]")
```

### 文件操作

使用 `pathlib.Path` 而不是 `os.path`：

```python
from pathlib import Path

output_dir = Path(config.OUTPUT_DIR)
output_dir.mkdir(exist_ok=True)

filename = output_dir / f"{keyword}_{timestamp}.json"
with open(filename, "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False, indent=2)
```

### 浏览器自动化

- 始终注入脚本绕过 webdriver 检测
- 使用随机延迟模拟人类操作
- 根据环境选择 `headless` 模式

```python
context.add_init_script("""
    Object.defineProperty(navigator, 'webdriver', {
        get: () => undefined
    });
""")

await asyncio.sleep(random.uniform(1, 3))
```

## 项目结构

```
weibo-crawler/
├── api.py           # FastAPI 服务主入口
├── main.py          # 独立爬虫脚本
├── config.py        # 配置文件
├── proxy_manager.py # 代理管理器
├── weibosearch.py   # 搜索自动化模块
├── test_proxy.py    # 代理测试
├── restart_api.sh   # 服务重启脚本
├── output/          # 输出目录
└── pyproject.toml   # 项目配置
```

## 注意事项

1. **不要在代码中硬编码密码**，所有敏感信息放在 `config.py`
2. **不要提交 `.env` 或配置文件中的真实密码** 到版本控制
3. **多 worker 环境** 必须使用 `asyncio.Lock()` 保护共享状态
4. **浏览器上下文** 使用完毕必须调用 `close()` 释放资源
5. **使用中文注释和日志**，保持与代码库一致
