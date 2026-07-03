# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

微博搜索爬虫服务，核心是一个 FastAPI API（`api.py`）加 Playwright 浏览器自动化，用于采集微博搜索、用户微博列表和用户资料。项目重点是降低微博风控触发概率：代理轮询、Cookie 池、浏览器指纹轮换、请求频率限制和风控状态监控都集中在 API 服务中。

## 开发环境

- Python 3.10+
- 包管理器：`uv`
- 主要依赖：`fastapi`、`playwright`、`playwright-stealth`、`httpx`、`curl-cffi`、`aiomysql`、`redis`、`rich`

首次运行或浏览器依赖缺失时，先安装依赖和 Playwright 浏览器：

```bash
uv sync
uv run playwright install chromium
```

## 常用命令

### 启动 API 服务

```bash
# 服务器环境推荐：虚拟显示器 + 多 worker
xvfb-run -a uv run python api.py --workers 4

# 本地开发：直接启动，worker 数量按需降低便于调试
uv run python api.py --workers 1
uv run python api.py --workers 2

# 后台启动示例
nohup xvfb-run -a uv run python api.py --workers 4 > output.log 2>&1 &
```

### 服务管理脚本

```bash
./restart_api.sh          # 默认重启
./restart_api.sh start    # 启动服务
./restart_api.sh stop     # 停止服务
./restart_api.sh restart  # 重启服务
./restart_api.sh status   # 查看状态
./restart_api.sh logs     # 查看实时日志
./restart_api.sh help     # 查看帮助
```

### 脚本式测试

当前仓库没有标准 pytest 配置，根目录测试文件是可直接运行的脚本：

```bash
uv run python test_proxy.py           # 测试 HTTP 代理池加载、轮询、失效标记和重载
uv run python test_http_proxy.py      # 测试代理连接与请求重试逻辑
uv run python test_enhanced_fetch.py  # 测试已启动 API 的 /fingerprint、/user/weibo、/user/profile
```

`test_enhanced_fetch.py` 依赖本地 API 已启动，默认访问 `http://localhost:8000`。

### 运行和调试接口

```bash
curl http://localhost:8000/fingerprint
curl http://localhost:8000/proxy/status
curl http://localhost:8000/cookies/status
```

`/fingerprint` 是运行状态的主要观测入口，包含当前指纹、请求统计、增强监控和限速器状态。

## 架构概览

### API 服务主线（`api.py`）

`api.py` 是核心文件：定义 FastAPI 应用、生命周期管理、全局浏览器实例、Cookie 池、风控监控、代理接入和所有 HTTP 端点。服务启动时通过 `lifespan()` 初始化数据库连接池、加载 Cookie、加载代理、启动 Playwright，并为每个 worker 创建独立浏览器上下文。

主要端点：

- `/search`、`/search/multi`：搜索采集入口。
- `/user/weibo`、`/user/weibo/multi`：用户微博列表采集。
- `/user/profile`：用户资料采集。
- `/fingerprint`：浏览器指纹、请求统计、风控监控和限速器状态。
- `/proxy/status`、`/proxy/reload`：代理池状态和手动重载。
- `/cookies/status`、`/cookies/reload`：Cookie 池状态和手动重载。
- `/test/network`、`/test/proxies`：运行时网络和代理诊断。

### 双请求模式

项目内有两条外部请求路径：

- `fetch_with_browser()`：通过 Playwright 页面里的 `fetch` 发请求，共享真实浏览器上下文、Cookie、指纹和 TLS 行为，优先用于用户资料、用户微博等更容易触发风控的接口。
- `fetch_with_proxy_retry()`：通过 HTTP 客户端和代理池直接请求，速度更快，适合风控压力较低或诊断类场景。

选择请求方式时优先考虑风控强度，而不是单纯性能。用户相关接口通常走浏览器方式。

### 风控与限速层

`api.py` 中的反风控逻辑包括：

- 随机浏览器指纹：User-Agent、viewport、locale、timezone、platform、WebGL、硬件参数。
- `RateLimiter`：令牌桶限速，配置来自 `config.RATE_LIMITER_*`。
- `RequestMonitor`：统计最近请求成功率、被封率、平均响应时间，并决定是否降速。
- `smart_delay()`：根据连续请求次数、验证码冷却和监控状态动态 sleep。
- 浏览器重置：达到配置阈值或检测到页面关闭/风控后刷新或重建浏览器上下文。

这些参数集中在 `config.py`，修改行为优先改配置，不要在调用处硬编码新阈值。

### Cookie 管理

Cookie 池在 API 启动时通过 `aiomysql` 从 MySQL 加载 `weibo_cookie where status=1`。`get_next_cookie()` 使用 `asyncio.Lock` 做进程内轮询保护；浏览器请求可通过 `use_cookie=True` 自动取下一个 Cookie。Cookie 失效时调用 `mark_cookie_invalid()` 从内存池移除，必要时重新加载。

### 代理管理（`proxy_manager.py`）

`ProxyManager` 从 `config.PROXY_HTTP_URL` 指定的 HTTP 接口加载代理，内存中轮询并跳过失效代理。多 worker 共享代理列表通过 `config.PROXY_CACHE_FILE` 指向的临时 JSON 文件实现：一个 worker 成功拉取后写入缓存，其他 worker 在接口失败或限流时可读取未过期缓存。

关键方法：

- `load_proxies_from_http()`：拉取代理，处理 429/超时重试，并写共享缓存。
- `get_next_proxy()`：轮询返回 `http://ip:port`。
- `mark_proxy_failed()`：标记代理失效。
- `start_auto_reload()` / `close()`：管理后台自动重载任务。

### 配置（`config.py`）

所有可调参数都在 `config.py`：关键词、页数、风控延迟、浏览器模式、输出目录、代理接口、代理缓存、数据库连接等。调试有头浏览器时改 `HEADLESS = False`；服务器通常使用 `xvfb-run`。

### 其他模块

- `weibosearch.py`：独立的移动端微博搜索自动化实验/脚本，包含滑块验证码处理、模拟输入、截图和 API 请求捕获。
- `main.py`：独立爬虫脚本入口，与 API 服务分离。
- `requestDemo.py`：代理请求示例脚本。

## 代码约定

- 使用 Python 3.10+ 类型语法：`dict | None`、`list[dict]`，避免新增 `typing.Optional/List/Dict` 风格。
- I/O 使用 `async/await`；异步路径里用 `asyncio.sleep()`，不要用 `time.sleep()` 阻塞事件循环。
- 共享状态（Cookie 索引、浏览器重置、代理轮询）需要 `asyncio.Lock` 或现有管理器保护。
- 日志沿用 Rich/彩色中文输出，并在多 worker 相关日志中包含 `PID:{os.getpid()}`。
- 公共函数/类保持中文文档字符串；不要把风控阈值、代理地址、数据库参数散落到业务代码中。
- 修改浏览器生命周期时，确保关闭路径清理 browser context、Playwright、数据库连接池和代理管理器后台任务。

## 运行注意事项

- `config.PROXY_ENABLED` 控制是否使用代理；关闭时 `get_proxy()` 返回 `None` 并直连。
- `USE_PERSISTENT_CONTEXT=False` 是为了避免多 worker 下浏览器 profile 的 SingletonLock 冲突；如启用持久化上下文，必须保证每个 worker 使用独立目录。
- 多 worker 下每个进程都有独立全局浏览器上下文和内存状态；跨 worker 共享目前主要依赖数据库和代理缓存文件。
- `config.py` 当前包含实际环境连接信息。编辑时避免扩大敏感信息暴露范围，提交前按项目要求处理配置。