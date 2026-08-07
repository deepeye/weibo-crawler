"""
微博采集 API 服务 - 基于 FastAPI
"""

import re
import time
import random
import os
import json
import asyncio
from enum import Enum
from urllib.parse import quote, unquote
from contextlib import asynccontextmanager
from typing import Literal
from functools import lru_cache

from fastapi import FastAPI, Query, HTTPException
from fastapi.responses import JSONResponse
from playwright.async_api import async_playwright, Page, BrowserContext
from playwright_stealth import Stealth
import httpx
import aiomysql
from curl_cffi.requests import AsyncSession  # 🔥 新增：curl_cffi
from rich.console import Console

import config
from proxy_manager import proxy_manager

console = Console()


# 全局浏览器上下文
browser_context: BrowserContext | None = None
browser_page: Page | None = None
browser_instance = None
playwright_instance = None

# 记录上次访问的关键词，避免重复访问搜索页
last_keyword: str | None = None

# 当前进程的指纹信息
current_fingerprint: dict | None = None

# 指纹重置计数器
fingerprint_reset_count: int = 0

# 请求统计
request_count: int = 0  # 总请求数
requests_since_rest: int = 0  # 上次休息后的请求数
last_captcha_time: float = 0  # 上次触发验证码的时间
process_start_time: float = 0  # 进程启动时间
last_full_reset_time: float = 0  # 上次完全重置的时间

# Cookie 轮询管理
weibo_cookies: list[str] = []  # 从数据库加载的 cookie 列表
cookie_index: int = 0  # 当前使用的 cookie 索引
cookie_lock = asyncio.Lock()  # Cookie 轮询锁（多 worker 安全）
browser_lock = asyncio.Lock()  # 浏览器操作锁（防止并发重置冲突）
db_pool = None  # 数据库连接池

# 预定义的 User-Agent 列表，避免网络请求
USER_AGENTS = [
    # Chrome on Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    # Chrome on macOS
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    # Chrome on Linux
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
    # Edge on Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36 Edg/119.0.0.0",
]


def get_random_fingerprint() -> dict:
    """生成随机浏览器指纹"""
    # 随机屏幕分辨率
    viewports = [
        {"width": 1920, "height": 1080},
        {"width": 1536, "height": 864},
        {"width": 1440, "height": 900},
        {"width": 1366, "height": 768},
        {"width": 1280, "height": 800},
        {"width": 1680, "height": 1050},
        {"width": 1600, "height": 900},
    ]
    
    # 随机语言
    locales = ["zh-CN", "zh-TW", "en-US", "en-GB"]
    
    # 随机时区
    timezones = ["Asia/Shanghai", "Asia/Hong_Kong", "Asia/Taipei", "Asia/Singapore"]
    
    # 随机平台
    platforms = ["MacIntel", "Win32", "Linux x86_64"]
    
    # 随机硬件参数
    hardware_concurrency = random.choice([4, 6, 8, 12, 16])
    device_memory = random.choice([4, 8, 16, 32])
    
    # 随机 WebGL 信息
    webgl_vendors = ["Intel Inc.", "Google Inc.", "NVIDIA Corporation"]
    webgl_renderers = [
        "Intel Iris OpenGL Engine",
        "ANGLE (Intel, Intel(R) UHD Graphics 630, OpenGL 4.1)",
        "ANGLE (NVIDIA, GeForce GTX 1080 Ti, OpenGL 4.5)",
        "Mesa Intel(R) UHD Graphics 620",
    ]
    
    return {
        "user_agent": random.choice(USER_AGENTS),
        "viewport": random.choice(viewports),
        "locale": random.choice(locales),
        "timezone_id": random.choice(timezones),
        "platform": random.choice(platforms),
        "hardware_concurrency": hardware_concurrency,
        "device_memory": device_memory,
        "webgl_vendor": random.choice(webgl_vendors),
        "webgl_renderer": random.choice(webgl_renderers),
        "device_scale_factor": random.choice([1, 1.25, 1.5, 2]),
    }


# ==================== 代理管理 ====================

async def get_proxy() -> str | None:
    """获取下一个代理（轮询）

    根据 config.PROXY_ENABLED 开关决定是否使用代理
    - 当 PROXY_ENABLED=False 时，返回 None（不使用代理）
    - 当 PROXY_ENABLED=True 时，从代理池轮询获取代理
    """
    if not config.PROXY_ENABLED:
        return None
    return await proxy_manager.get_next_proxy()


# ==================== Cookie 管理 ====================

async def init_db_pool():
    """初始化数据库连接池"""
    global db_pool
    try:
        db_pool = await aiomysql.create_pool(
            host=config.DB_HOST,
            port=config.DB_PORT,
            user=config.DB_USER,
            password=config.DB_PASSWORD,
            db=config.DB_NAME,
            autocommit=True,
            minsize=1,
            maxsize=5,
        )
        print(f"[数据库] 连接池初始化成功")
    except Exception as e:
        print(f"[数据库] 连接失败: {e}")
        db_pool = None


async def load_cookies_from_db():
    """从数据库加载 cookie 列表"""
    global weibo_cookies
    if not db_pool:
        print("[数据库] 连接池未初始化，无法加载 cookie")
        return
    
    try:
        async with db_pool.acquire() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute("SELECT cookie FROM weibo_cookie where status=1")
                rows = await cursor.fetchall()
                weibo_cookies = [row[0] for row in rows if row[0]]
                print(f"[数据库] 加载了 {len(weibo_cookies)} 个 cookie")
    except Exception as e:
        print(f"[数据库] 加载 cookie 失败: {e}")


async def get_next_cookie() -> str | None:
    """轮询获取下一个 cookie（线程安全，支持自动重载）"""
    global cookie_index, weibo_cookies

    # 如果 cookie 列表为空，尝试重新加载
    if not weibo_cookies:
        print("[Cookie] 列表为空，尝试从数据库重新加载...")
        await load_cookies_from_db()

        # 重新加载后仍为空，返回 None
        if not weibo_cookies:
            print("[Cookie] ❌ 数据库中没有可用的 cookie")
            return None

    async with cookie_lock:
        cookie = weibo_cookies[cookie_index % len(weibo_cookies)]
        cookie_index += 1
        return cookie


async def mark_cookie_invalid(cookie_str: str):
    """标记并移除无效的 cookie"""
    global weibo_cookies

    async with cookie_lock:
        try:
            if cookie_str in weibo_cookies:
                weibo_cookies.remove(cookie_str)
                print(f"[Cookie] ⚠️ 已移除无效 cookie (剩余 {len(weibo_cookies)} 个)")

                # 如果列表为空，尝试重新加载
                if not weibo_cookies:
                    print("[Cookie] 列表已清空，尝试重新加载...")
                    await load_cookies_from_db()
        except Exception as e:
            print(f"[Cookie] 移除失败: {e}")


@lru_cache(maxsize=128)
def extract_xsrf_token(cookie_str: str) -> str | None:
    """从 cookie 字符串中提取 XSRF-TOKEN（带缓存）"""
    match = re.search(r'XSRF-TOKEN=([^;]+)', cookie_str)
    return match.group(1) if match else None


def parse_cookie_string(cookie_str: str) -> dict:
    """解析 cookie 字符串为字典（优化性能）"""
    cookies = {}
    if not cookie_str:
        return cookies

    # 一次性分割，避免多次字符串操作
    for item in cookie_str.split(';'):
        item = item.strip()
        if '=' in item:
            key, value = item.split('=', 1)
            cookies[key.strip()] = value.strip()
    return cookies


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    global browser_context, browser_page, browser_instance, playwright_instance, current_fingerprint, db_pool
    
    # 获取进程 ID
    pid = os.getpid()

    # 初始化数据库连接池并加载 cookie
    await init_db_pool()
    await load_cookies_from_db()

    # 加载代理列表并启动自动重载
    console.print(f"[cyan][PID:{pid}] 正在从 HTTP 接口加载代理...[/cyan]")
    await proxy_manager.load_proxies_from_http()
    await proxy_manager.start_auto_reload()

    # 获取一个代理用于此进程的浏览器
    current_proxy = await get_proxy()
    # current_proxy = None
    if current_proxy:
        print(f"[PID:{pid}] 使用代理: {current_proxy}")
    else:
        print(f"[PID:{pid}] 未获取到代理，将直连")

    # 启动时初始化浏览器
    print(f"[PID:{pid}] 正在初始化浏览器...")
    playwright_instance = await async_playwright().start()
    
    # 获取随机指纹
    current_fingerprint = get_random_fingerprint()
    current_fingerprint["pid"] = pid
    
    print(f"[PID:{pid}] 指纹信息:")
    print(f"  - UA: {current_fingerprint['user_agent'][:60]}...")
    print(f"  - Viewport: {current_fingerprint['viewport']}")
    print(f"  - Platform: {current_fingerprint['platform']}")
    print(f"  - WebGL: {current_fingerprint['webgl_renderer'][:40]}...")
    
    # 浏览器启动参数
    browser_args = [
        "--disable-blink-features=AutomationControlled",
        "--disable-features=IsolateOrigins,site-per-process",
        "--no-sandbox",
        "--disable-dev-shm-usage",
        "--disable-web-security",
        "--disable-features=VizDisplayCompositor",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-background-timer-throttling",
        "--disable-backgrounding-occluded-windows",
        "--disable-renderer-backgrounding",
        "--disable-infobars",
        "--disable-extensions",
        f"--window-size={current_fingerprint['viewport']['width']},{current_fingerprint['viewport']['height']}",
    ]
    
    # 使用持久化上下文（保留 cookie、历史等）
    if getattr(config, 'USE_PERSISTENT_CONTEXT', False):
        import pathlib
        user_data_dir = f"{config.USER_DATA_DIR}/worker_{pid}"
        pathlib.Path(user_data_dir).mkdir(parents=True, exist_ok=True)
        
        print(f"[PID:{pid}] 使用持久化上下文: {user_data_dir}")
        
        browser_context = await playwright_instance.chromium.launch_persistent_context(
            user_data_dir,
            headless=config.HEADLESS,
            args=browser_args,
            user_agent=current_fingerprint["user_agent"],
            viewport=current_fingerprint["viewport"],
            locale=current_fingerprint["locale"],
            timezone_id=current_fingerprint["timezone_id"],
            proxy={"server": current_proxy} if current_proxy else None,
            device_scale_factor=current_fingerprint["device_scale_factor"],
            has_touch=False,
            is_mobile=False,
            java_script_enabled=True,
            permissions=["geolocation"],
            geolocation={"latitude": 31.2304, "longitude": 121.4737},
        )
        browser_instance = None  # 持久化上下文没有单独的 browser 实例
        browser_page = browser_context.pages[0] if browser_context.pages else await browser_context.new_page()
    else:
        # 普通模式
        browser_instance = await playwright_instance.chromium.launch(
            headless=config.HEADLESS,
            args=browser_args
        )

        browser_context = await browser_instance.new_context(
            user_agent=current_fingerprint["user_agent"],
            viewport=current_fingerprint["viewport"],
            locale=current_fingerprint["locale"],
            timezone_id=current_fingerprint["timezone_id"],
            proxy={"server": current_proxy} if current_proxy else None,
            device_scale_factor=current_fingerprint["device_scale_factor"],
            has_touch=False,
            is_mobile=False,
            java_script_enabled=True,
            permissions=["geolocation"],
            geolocation={"latitude": 31.2304, "longitude": 121.4737},
        )
        browser_page = await browser_context.new_page()
    
    # 应用 stealth 反检测
    stealth = Stealth()
    await stealth.apply_stealth_async(browser_page)
    
    # 注入额外的反检测脚本（使用动态指纹参数）
    await browser_context.add_init_script(f"""
        // 覆盖 webdriver 属性
        Object.defineProperty(navigator, 'webdriver', {{
            get: () => undefined
        }});
        
        // 添加 chrome 对象
        window.chrome = {{
            runtime: {{}},
            loadTimes: function() {{}},
            csi: function() {{}},
            app: {{}}
        }};
        
        // 模拟插件
        Object.defineProperty(navigator, 'plugins', {{
            get: () => [
                {{
                    0: {{type: "application/x-google-chrome-pdf", suffixes: "pdf", description: "Portable Document Format"}},
                    description: "Portable Document Format",
                    filename: "internal-pdf-viewer",
                    length: 1,
                    name: "Chrome PDF Plugin"
                }},
                {{
                    0: {{type: "application/pdf", suffixes: "pdf", description: ""}},
                    description: "",
                    filename: "mhjfbmdgcfjbbpaeojofohoefgiehjai",
                    length: 1,
                    name: "Chrome PDF Viewer"
                }}
            ]
        }});
        
        // 模拟语言
        Object.defineProperty(navigator, 'languages', {{
            get: () => ['{current_fingerprint["locale"]}', 'zh', 'en-US', 'en']
        }});
        
        // 模拟硬件并发数
        Object.defineProperty(navigator, 'hardwareConcurrency', {{
            get: () => {current_fingerprint["hardware_concurrency"]}
        }});
        
        // 模拟内存
        Object.defineProperty(navigator, 'deviceMemory', {{
            get: () => {current_fingerprint["device_memory"]}
        }});
        
        // 模拟平台
        Object.defineProperty(navigator, 'platform', {{
            get: () => '{current_fingerprint["platform"]}'
        }});
        
        // 移除自动化特征
        delete window.cdc_adoQpoasnfa76pfcZLmcfl_Array;
        delete window.cdc_adoQpoasnfa76pfcZLmcfl_Promise;
        delete window.cdc_adoQpoasnfa76pfcZLmcfl_Symbol;
        
        // WebGL 指纹
        const getParameter = WebGLRenderingContext.prototype.getParameter;
        WebGLRenderingContext.prototype.getParameter = function(parameter) {{
            if (parameter === 37445) {{
                return '{current_fingerprint["webgl_vendor"]}';
            }}
            if (parameter === 37446) {{
                return '{current_fingerprint["webgl_renderer"]}';
            }}
            return getParameter.call(this, parameter);
        }};
    """) 
    
    # 添加 cookie（如果配置了）
    if config.COOKIES:
        await browser_context.add_cookies(config.COOKIES)
    
    # 先访问微博首页初始化，模拟真实用户行为
    try:
        await browser_page.goto("https://m.weibo.cn", wait_until="domcontentloaded", timeout=30000)
        # 等待页面稳定后再模拟行为
        await asyncio.sleep(1)
        await simulate_human_behavior(browser_page)
    except Exception as e:
        print(f"[PID:{pid}] 初始化访问微博首页失败: {e}, 继续启动...")
    
    # 记录启动时间
    process_start_time = time.time()
    last_full_reset_time = time.time()
    
    print(f"[PID:{pid}] 浏览器初始化完成!")
    
    yield
    
    # 关闭时清理资源
    print(f"[PID:{pid}] 正在关闭浏览器...")
    await browser_context.close()
    await playwright_instance.stop()
    
    # 关闭数据库连接池
    if db_pool:
        db_pool.close()
        await db_pool.wait_closed()
        print(f"[PID:{pid}] 数据库连接池已关闭")

    # 关闭代理管理器
    await proxy_manager.close()


async def simulate_human_behavior(page: Page):
    """模拟真实用户行为 - 增强版"""
    try:
        # 初始停留，模拟用户观察页面
        await page.wait_for_timeout(random.randint(1500, 3000))

        # 随机鼠标移动 - 更自然的移动轨迹
        try:
            # 第一次鼠标移动
            start_x, start_y = random.randint(100, 400), random.randint(100, 400)
            end_x, end_y = random.randint(100, 400), random.randint(100, 400)
            await page.mouse.move(start_x, start_y)
            await page.wait_for_timeout(random.randint(100, 300))
            await page.mouse.move(end_x, end_y)

            # 随机滚动 - 多次小幅滚动更自然
            for _ in range(random.randint(2, 4)):
                try:
                    scroll_amount = random.randint(30, 120)
                    await page.evaluate(f"window.scrollBy(0, {scroll_amount})")
                    await page.wait_for_timeout(random.randint(200, 600))
                except Exception:
                    break

            # 模拟阅读停留 - 更长的停留时间
            await page.wait_for_timeout(random.randint(1000, 2500))

            # 第二次鼠标移动，可能悬停在某些元素上
            await page.mouse.move(
                random.randint(150, 450),
                random.randint(150, 450)
            )

            # 模拟用户可能的点击行为（不实际点击，只是移动到可点击元素附近）
            await page.wait_for_timeout(random.randint(300, 800))

        except Exception as e:
            print(f"[模拟行为] 交互失败（可能页面已导航）: {e}")

    except Exception as e:
        print(f"[模拟行为] 执行失败: {e}")


async def smart_delay():
    """智能延迟 - 根据请求次数和风控状态调整延迟"""
    global requests_since_rest, last_captcha_time

    pid = os.getpid()

    # 检查是否需要休息 - 降低阈值，更频繁休息
    if requests_since_rest >= max(config.REQUESTS_BEFORE_REST - 3, 5):  # 从默认值降低3次，最少5次
        rest_time = config.REST_DURATION_BASE + 10 + random.randint(0, config.REST_DURATION_RANDOM + 10)  # 增加休息时间
        print(f"[PID:{pid}] 达到 {requests_since_rest} 次请求，休息 {rest_time} 秒...")
        await asyncio.sleep(rest_time)
        requests_since_rest = 0
        return

    # 检查是否在验证码冷却期 - 增加冷却时间
    time_since_captcha = time.time() - last_captcha_time
    if last_captcha_time > 0 and time_since_captcha < config.CAPTCHA_COOLDOWN * 1.5:  # 冷却时间增加50%
        wait_time = config.CAPTCHA_COOLDOWN * 1.5 - time_since_captcha
        print(f"[PID:{pid}] 验证码冷却中，等待 {wait_time:.0f} 秒...")
        await asyncio.sleep(wait_time)
        return  # 冷却等待后直接返回，不再叠加延迟

    # 触发过验证码后，长期保持较高延迟（15分钟内）
    if last_captcha_time > 0 and time_since_captcha < 900:
        extra_delay = 3 + random.uniform(1, 4)
        print(f"[PID:{pid}] 验证码后恢复期，额外延迟 {extra_delay:.1f} 秒")
        await asyncio.sleep(extra_delay)

    # 正常随机延迟 - 增加基础延迟
    delay = config.REQUEST_DELAY_BASE + 2 + random.uniform(1, config.REQUEST_DELAY_RANDOM + 2)
    await asyncio.sleep(delay)


async def maybe_refresh_browser():
    """检查是否需要刷新浏览器状态"""
    global request_count, last_full_reset_time, requests_since_rest
    
    pid = os.getpid()
    
    # 检查是否需要完全重置（定时重置）
    minutes_since_reset = (time.time() - last_full_reset_time) / 60
    if minutes_since_reset >= config.FULL_RESET_INTERVAL_MINUTES:
        print(f"[PID:{pid}] 运行已达 {minutes_since_reset:.0f} 分钟，执行完全重置...")
        await reset_browser_with_new_fingerprint()
        last_full_reset_time = time.time()
        requests_since_rest = 0
        
        # 完全重置后休息一段时间
        rest_time = config.FULL_RESET_REST_DURATION + random.randint(0, 30)
        print(f"[PID:{pid}] 完全重置完成，休息 {rest_time} 秒...")
        await asyncio.sleep(rest_time)
        return
    
    # 检查是否需要定期刷新指纹
    if request_count > 0 and request_count % config.REFRESH_BROWSER_EVERY == 0:
        print(f"[PID:{pid}] 达到 {config.REFRESH_BROWSER_EVERY} 次请求，刷新浏览器状态...")
        await reset_browser_with_new_fingerprint()


async def reset_browser_with_new_fingerprint():
    """重置浏览器并生成新的指纹（加锁防止并发冲突）"""
    global browser_context, browser_page, browser_instance, current_fingerprint, last_keyword, fingerprint_reset_count, playwright_instance

    # 🔒 使用锁防止并发重置
    async with browser_lock:
        pid = os.getpid()
        fingerprint_reset_count += 1

        # 获取新的代理
        new_proxy = await get_proxy()
        if new_proxy:
            print(f"[PID:{pid}] 切换代理: {new_proxy}")
        else:
            print(f"[PID:{pid}] 未获取到新代理，将直连")

        print(f"[PID:{pid}] 正在重置浏览器指纹... (第 {fingerprint_reset_count} 次重置)")
    
    # 关闭旧的浏览器上下文和实例
    if browser_context:
        try:
            await browser_context.close()
        except Exception as e:
            print(f"[PID:{pid}] 关闭上下文失败: {e}")
    
    # 如果 browser_instance 存在，尝试关闭它
    if browser_instance:
        try:
            await browser_instance.close()
            print(f"[PID:{pid}] 已关闭旧的浏览器实例")
        except Exception as e:
            print(f"[PID:{pid}] 关闭浏览器实例失败: {e}")

    # 🧹 清理残留的 chrome 进程（防止文件描述符耗尽）
    try:
        import subprocess
        # 杀掉当前进程相关的残留 chrome 进程
        subprocess.run("pkill -9 -f 'chrome.*playwright' 2>/dev/null", shell=True, capture_output=True)
        subprocess.run("pkill -9 -f 'chromium.*playwright' 2>/dev/null", shell=True, capture_output=True)
        # 等待一下让系统释放资源
        await asyncio.sleep(0.5)
    except Exception as e:
        print(f"[PID:{pid}] 清理残留进程失败: {e}")
    
    # 生成新的指纹
    current_fingerprint = get_random_fingerprint()
    current_fingerprint["pid"] = pid
    current_fingerprint["reset_count"] = fingerprint_reset_count
    
    print(f"[PID:{pid}] 新指纹信息:")
    print(f"  - UA: {current_fingerprint['user_agent'][:50]}...")
    print(f"  - Viewport: {current_fingerprint['viewport']}")
    print(f"  - Platform: {current_fingerprint['platform']}")
    
    # 浏览器启动参数
    browser_args = [
        "--disable-blink-features=AutomationControlled",
        "--disable-features=IsolateOrigins,site-per-process",
        "--no-sandbox",
        "--disable-dev-shm-usage",
        "--disable-web-security",
        "--disable-infobars",
        f"--window-size={current_fingerprint['viewport']['width']},{current_fingerprint['viewport']['height']}",
    ]
    
    # 根据配置选择创建方式
    if getattr(config, 'USE_PERSISTENT_CONTEXT', False):
        import pathlib
        # 为每次重置使用新的子目录
        user_data_dir = f"{config.USER_DATA_DIR}/worker_{pid}_reset_{fingerprint_reset_count}"
        pathlib.Path(user_data_dir).mkdir(parents=True, exist_ok=True)
        
        browser_context = await playwright_instance.chromium.launch_persistent_context(
            user_data_dir,
            headless=config.HEADLESS,
            args=browser_args,
            user_agent=current_fingerprint["user_agent"],
            viewport=current_fingerprint["viewport"],
            locale=current_fingerprint["locale"],
            timezone_id=current_fingerprint["timezone_id"],
            proxy={"server": new_proxy} if new_proxy else None,
            device_scale_factor=current_fingerprint["device_scale_factor"],
            has_touch=False,
            is_mobile=False,
            java_script_enabled=True,
            permissions=["geolocation"],
            geolocation={"latitude": 31.2304, "longitude": 121.4737},
        )
        browser_page = browser_context.pages[0] if browser_context.pages else await browser_context.new_page()
    else:
        # 普通模式 - 重新创建 browser_instance
        try:
            browser_instance = await playwright_instance.chromium.launch(
                headless=config.HEADLESS,
                args=browser_args
            )
            print(f"[PID:{pid}] 重新启动浏览器实例成功")
        except Exception as e:
            print(f"[PID:{pid}] 启动浏览器失败: {e}")
            raise

        browser_context = await browser_instance.new_context(
            user_agent=current_fingerprint["user_agent"],
            viewport=current_fingerprint["viewport"],
            locale=current_fingerprint["locale"],
            timezone_id=current_fingerprint["timezone_id"],
            proxy={"server": new_proxy} if new_proxy else None,
            device_scale_factor=current_fingerprint["device_scale_factor"],
            has_touch=False,
            is_mobile=False,
            java_script_enabled=True,
            permissions=["geolocation"],
            geolocation={"latitude": 31.2304, "longitude": 121.4737},
        )
        browser_page = await browser_context.new_page()
    
    # 应用 stealth 反检测
    stealth = Stealth()
    await stealth.apply_stealth_async(browser_page)
    
    # 注入反检测脚本
    await browser_context.add_init_script(f"""
        Object.defineProperty(navigator, 'webdriver', {{ get: () => undefined }});
        window.chrome = {{ runtime: {{}}, loadTimes: function() {{}}, csi: function() {{}}, app: {{}} }};
        Object.defineProperty(navigator, 'plugins', {{
            get: () => [{{ 0: {{type: "application/x-google-chrome-pdf"}}, name: "Chrome PDF Plugin" }}]
        }});
        Object.defineProperty(navigator, 'languages', {{ get: () => ['{current_fingerprint["locale"]}', 'zh', 'en'] }});
        Object.defineProperty(navigator, 'hardwareConcurrency', {{ get: () => {current_fingerprint["hardware_concurrency"]} }});
        Object.defineProperty(navigator, 'deviceMemory', {{ get: () => {current_fingerprint["device_memory"]} }});
        Object.defineProperty(navigator, 'platform', {{ get: () => '{current_fingerprint["platform"]}' }});
        const getParameter = WebGLRenderingContext.prototype.getParameter;
        WebGLRenderingContext.prototype.getParameter = function(p) {{
            if (p === 37445) return '{current_fingerprint["webgl_vendor"]}';
            if (p === 37446) return '{current_fingerprint["webgl_renderer"]}';
            return getParameter.call(this, p);
        }};
    """)
    
    # 添加 cookie
    if config.COOKIES:
        await browser_context.add_cookies(config.COOKIES)
    
    # 重新访问微博首页
    try:
        await browser_page.goto("https://m.weibo.cn", wait_until="domcontentloaded", timeout=30000)
        await simulate_human_behavior(browser_page)
    except Exception as e:
        print(f"[PID:{pid}] 重置后访问微博首页失败: {e}, 继续运行...")
    
    # 重置关键词缓存
    last_keyword = None
    
    print(f"[PID:{pid}] 浏览器指纹重置完成!")


app = FastAPI(
    title="微博采集代理 API",
    description="基于 Playwright 的微博采集代理接口，自动绕过极验验证",
    version="1.0.0",
    lifespan=lifespan,
)


# 搜索类型枚举
class SearchType(int, Enum):
    REALTIME = 61  # 实时
    VIDEO = 64     # 视频


def build_search_url(keyword: str, page: int = 1, search_type: int = 61) -> str:
    """构建搜索 URL
    
    Args:
        keyword: 搜索关键词
        page: 页码
        search_type: 搜索类型，61=实时，64=视频
    """
    encoded_keyword = quote(keyword)
    container_id = f"100103type={search_type}&q={encoded_keyword}&t="
    encoded_container_id = quote(container_id, safe='')
    return f"https://m.weibo.cn/api/container/getIndex?containerid={encoded_container_id}&page_type=searchall&page={page}"


async def handle_captcha(page: Page) -> bool:
    """处理极验验证码
    
    Returns:
        是否成功通过验证
    """
    print("检测到验证码，等待极验无感验证...")
    
    # 等待页面加载并让极验无感验证自动完成
    await page.wait_for_timeout(3000)
    
    # 检查是否有滑块验证码弹窗
    captcha_iframe = await page.query_selector("iframe[src*='geetest']")
    if captcha_iframe:
        print("检测到滑块验证码，需要手动处理...")
        # 坘到验证码弹窗，等待更长时间让用户手动处理（如果是有头模式）
        if not config.HEADLESS:
            print("请在浏览器中完成滑块验证...")
            await page.wait_for_timeout(15000)  # 等待 15 秒让用户完成
        return False
    
    return True


async def fetch_search_result(keyword: str, page: int = 1, search_type: int = 61, use_cookie: bool = False) -> dict:
    """使用 Playwright 驱动浏览器访问搜索页面，拦截 API 响应

    Args:
        keyword: 搜索关键词
        page: 页码
        search_type: 搜索类型，61=实时，64=视频
        use_cookie: 是否使用数据库中的 Cookie（默认 False）
    """
    global browser_page, browser_context, browser_instance, current_fingerprint, request_count, requests_since_rest, last_captcha_time, last_keyword, fingerprint_reset_count, playwright_instance

    pid = os.getpid()

    # 智能延迟
    await smart_delay()

    # 检查是否需要刷新浏览器
    await maybe_refresh_browser()

    max_fingerprint_retries = 3  # 最多更换 3 次指纹

    # 加载 Cookie 到浏览器上下文（如果启用）
    if use_cookie:
        cookie_str = await get_next_cookie()
        if cookie_str:
            cookie_dict = parse_cookie_string(cookie_str)
            try:
                await browser_context.clear_cookies()
                for name, value in cookie_dict.items():
                    await browser_context.add_cookies([{
                        "name": name,
                        "value": value,
                        "domain": ".weibo.cn",
                        "path": "/"
                    }])
                print(f"[搜索] 已加载 Cookie")
            except Exception as e:
                print(f"[搜索] 加载 Cookie 失败: {e}")

    # 构建搜索页面 URL
    containerid = f"100103type={search_type}&q={quote(keyword)}"
    search_url = f"https://m.weibo.cn/search?containerid={containerid}"

    # 构建目标 API URL（用于 fetch 请求）
    # API URL 格式: https://m.weibo.cn/api/container/getIndex?containerid=100103type%3D{search_type}%26q%3D{keyword}%26t%3D&page_type=searchall&page={page}
    target_api_url = f"https://m.weibo.cn/api/container/getIndex?containerid=100103type%3D{search_type}%26q%3D{quote(keyword)}%26t%3D&page_type=searchall&page={page}"

    # 多次指纹重试循环
    for fingerprint_retry in range(max_fingerprint_retries):
        # 检查浏览器状态
        if not browser_page or browser_page.is_closed():
            print(f"[搜索] 浏览器不可用，重置...")
            await reset_browser_with_new_fingerprint()
            continue

        try:
            # # 先访问微博首页进行预热，模拟真实用户行为
            # print(f"[搜索] 预热：先访问微博首页")
            # try:
            #     await browser_page.goto("https://m.weibo.cn", wait_until="domcontentloaded", timeout=20000)
            #     await asyncio.sleep(random.randint(1, 2))
            #     # 模拟用户在首页的简单行为
            #     await simulate_human_behavior(browser_page)
            #     print(f"[搜索] 预热完成")
            # except Exception as e:
            #     print(f"[搜索] 预热失败（继续执行）: {e}")

            # 访问搜索页面
            print(f"[搜索] 访问搜索页面: {search_url}")

            try:
                await browser_page.goto(search_url, wait_until="domcontentloaded", timeout=30000)
                current_url = browser_page.url
                print(f"[搜索] 页面加载成功: {current_url}")

                # 页面加载后额外停留，模拟用户观察搜索结果
                await asyncio.sleep(random.randint(2, 4))
            except Exception as e:
                error_msg = str(e)
                print(f"[搜索] 页面加载失败: {error_msg}")

                # 检查是否是网络错误
                if any(err in error_msg for err in ["ERR_TIMED_OUT", "ERR_CONNECTION", "TIMEOUT", "net::"]):
                    print(f"[搜索] 网络错误，更换指纹重试...")
                    last_captcha_time = time.time()
                    await reset_browser_with_new_fingerprint()
                    await asyncio.sleep(2)
                    continue

            # 等待页面完全稳定
            await asyncio.sleep(random.randint(1, 2))

            # 直接使用目标 API URL 进行 fetch 请求
            print(f"[搜索] 使用浏览器 fetch 请求: {target_api_url}")

            # 使用浏览器 fetch 请求 API
            api_result = await browser_page.evaluate(f"""
                async () => {{
                    try {{
                        const response = await fetch('{target_api_url}', {{
                            method: 'GET',
                            credentials: 'include',
                            headers: {{
                                'Accept': 'application/json, text/plain, */*',
                                'X-Requested-With': 'XMLHttpRequest',
                                'MWeibo-Pwa': '1',
                                'Referer': '{search_url}',
                                'Sec-Fetch-Site': 'same-origin',
                                'Sec-Fetch-Mode': 'cors',
                                'Sec-Fetch-Dest': 'empty'
                            }}
                        }});

                        const status = response.status;
                        const data = await response.json();

                        return {{ ok: 1, status: status, data: data }};
                    }} catch (error) {{
                        return {{ ok: -1, error: error.message }};
                    }}
                }}
            """)

            if api_result.get("ok") == 1:
                response_data = api_result.get("data")
                http_status = api_result.get("status")
                print(f"[搜索] fetch 响应: HTTP {http_status}")

                # 检查是否触发验证码
                if isinstance(response_data, dict) and response_data.get("ok") == -100:
                    print(f"[搜索] API 返回验证码错误 (ok=-100)")

                    # 检查是否有验证码
                    has_geetest = await browser_page.evaluate("""
                        () => {
                            return !!document.querySelector('.geetest_holder') ||
                                   !!document.querySelector('#geetest-captcha') ||
                                   !!document.querySelector('[data-captcha-id]');
                        }
                    """)

                    if has_geetest:
                        if config.HEADLESS:
                            print(f"[搜索] 无头模式无法完成验证码，更换指纹重试...")
                            await reset_browser_with_new_fingerprint()
                            await asyncio.sleep(2)
                            continue
                        else:
                            print(f"[搜索] 等待用户完成验证码...")
                            await asyncio.sleep(30)
                            # 验证码完成后需要重新请求，继续下一次循环
                            continue

                    return {
                        "ok": -100,
                        "error": "触发验证码",
                        "data": response_data
                    }

                # 成功获取数据
                print(f"[搜索] 成功获取数据")
                request_count += 1
                requests_since_rest += 1
                return response_data
            else:
                error_msg = api_result.get("error", "未知错误")
                print(f"[搜索] fetch 失败: {error_msg}")
                return {
                    "ok": -1,
                    "error": f"fetch 失败: {error_msg}"
                }

        except Exception as e:
            error_msg = str(e)
            print(f"[搜索] 异常: {error_msg[:300]}")

            # 检查是否应该重试
            should_retry = any(err in error_msg.lower() for err in [
                "captcha", "geetest", "timeout", "network", "target closed", "net::", "context"
            ])

            if should_retry:
                print(f"[搜索] 错误可重试，更换指纹...")
                await reset_browser_with_new_fingerprint()
                await asyncio.sleep(2)
                continue

            return {
                "ok": -1,
                "error": f"请求异常: {error_msg[:200]}"
            }

    # 所有代理重试失败后，尝试直连一次
    print(f"[搜索] 所有代理尝试失败，尝试直连...")

    pid = os.getpid()
    fingerprint_reset_count += 1

    print(f"[PID:{pid}] 正在使用直连模式重置浏览器...")

    # 关闭旧的浏览器上下文和实例
    if browser_context:
        try:
            await browser_context.close()
        except Exception as e:
            print(f"[PID:{pid}] 关闭上下文失败: {e}")

    if browser_instance:
        try:
            await browser_instance.close()
        except Exception as e:
            print(f"[PID:{pid}] 关闭浏览器实例失败: {e}")

    # 生成新的指纹
    current_fingerprint = get_random_fingerprint()
    current_fingerprint["pid"] = pid
    current_fingerprint["reset_count"] = fingerprint_reset_count

    print(f"[PID:{pid}] 直连模式新指纹:")
    print(f"  - UA: {current_fingerprint['user_agent'][:50]}...")
    print(f"  - Viewport: {current_fingerprint['viewport']}")

    # 浏览器启动参数
    browser_args = [
        "--disable-blink-features=AutomationControlled",
        "--disable-features=IsolateOrigins,site-per-process",
        "--no-sandbox",
        "--disable-dev-shm-usage",
        "--disable-web-security",
        "--disable-infobars",
        f"--window-size={current_fingerprint['viewport']['width']},{current_fingerprint['viewport']['height']}",
    ]

    # 使用直连（proxy=None）
    try:
        browser_instance = await playwright_instance.chromium.launch(
            headless=config.HEADLESS,
            args=browser_args
        )
        print(f"[PID:{pid}] 直连模式启动浏览器成功")
    except Exception as e:
        print(f"[PID:{pid}] 启动浏览器失败: {e}")
        return {
            "ok": -100,
            "error": "所有尝试均失败",
            "msg": f"已尝试 {max_fingerprint_retries} 次代理，直连也失败"
        }

    browser_context = await browser_instance.new_context(
        user_agent=current_fingerprint["user_agent"],
        viewport=current_fingerprint["viewport"],
        locale=current_fingerprint["locale"],
        timezone_id=current_fingerprint["timezone_id"],
        proxy=None,  # 直连，不使用代理
        device_scale_factor=current_fingerprint["device_scale_factor"],
        has_touch=False,
        is_mobile=False,
        java_script_enabled=True,
        permissions=["geolocation"],
        geolocation={"latitude": 31.2304, "longitude": 121.4737},
    )

    browser_page = await browser_context.new_page()

    # 初始化访问
    try:
        await browser_page.goto("https://m.weibo.cn", wait_until="domcontentloaded", timeout=30000)
        await asyncio.sleep(1)
        await simulate_human_behavior(browser_page)
        print(f"[PID:{pid}] 直连模式初始化成功")
    except Exception as e:
        print(f"[PID:{pid}] 直连模式初始化失败: {e}")

    # 使用直连浏览器进行一次最终尝试
    print(f"[搜索] 使用直连模式进行最终尝试...")

    try:
        # 先预热访问首页
        # print(f"[搜索] 直连模式预热：访问微博首页")
        # try:
        #     await browser_page.goto("https://m.weibo.cn", wait_until="domcontentloaded", timeout=20000)
        #     await asyncio.sleep(random.randint(1, 2))
        #     await simulate_human_behavior(browser_page)
        #     print(f"[搜索] 直连模式预热完成")
        # except Exception as e:
        #     print(f"[搜索] 直连模式预热失败（继续执行）: {e}")

        # 访问搜索页面
        print(f"[搜索] 直连模式访问搜索页面: {search_url}")

        try:
            await browser_page.goto(search_url, wait_until="domcontentloaded", timeout=30000)
            current_url = browser_page.url
            print(f"[搜索] 直连模式页面加载成功: {current_url}")

            # 页面加载后额外停留
            await asyncio.sleep(random.randint(2, 4))
        except Exception as e:
            error_msg = str(e)
            print(f"[搜索] 直连模式页面加载失败: {error_msg}")
            return {
                "ok": -100,
                "error": "直连模式页面加载失败",
                "msg": error_msg[:200]
            }

        # 等待页面完全稳定
        await asyncio.sleep(random.randint(1, 2))

        # 使用目标 API URL 进行 fetch 请求
        print(f"[搜索] 使用直连浏览器 fetch 请求: {target_api_url}")

        # 使用浏览器 fetch 请求 API
        api_result = await browser_page.evaluate(f"""
            async () => {{
                try {{
                    const response = await fetch('{target_api_url}', {{
                        method: 'GET',
                        credentials: 'include',
                        headers: {{
                            'Accept': 'application/json, text/plain, */*',
                            'X-Requested-With': 'XMLHttpRequest',
                            'MWeibo-Pwa': '1',
                            'Referer': '{search_url}',
                            'Sec-Fetch-Site': 'same-origin',
                            'Sec-Fetch-Mode': 'cors',
                            'Sec-Fetch-Dest': 'empty'
                        }}
                    }});

                    const status = response.status;
                    const data = await response.json();

                    return {{ ok: 1, status: status, data: data }};
                }} catch (error) {{
                    return {{ ok: -1, error: error.message }};
                }}
            }}
        """)

        if api_result.get("ok") == 1:
            response_data = api_result.get("data")
            http_status = api_result.get("status")
            print(f"[搜索] 直连 fetch 响应: HTTP {http_status}")

            # 检查是否触发验证码
            if isinstance(response_data, dict) and response_data.get("ok") == -100:
                print(f"[搜索] 直连模式 API 返回验证码错误 (ok=-100)")
                return {
                    "ok": -100,
                    "error": "直连模式触发验证码",
                    "data": response_data
                }

            # 成功获取数据
            print(f"[搜索] 直连模式成功获取数据")
            request_count += 1
            requests_since_rest += 1
            return response_data
        else:
            error_msg = api_result.get("error", "未知错误")
            print(f"[搜索] 直连 fetch 失败: {error_msg}")
            return {
                "ok": -1,
                "error": f"直连 fetch 失败: {error_msg}"
            }

    except Exception as e:
        error_msg = str(e)
        print(f"[搜索] 直连模式异常: {error_msg[:300]}")
        return {
            "ok": -100,
            "error": "所有尝试均失败",
            "msg": f"已尝试 {max_fingerprint_retries} 次代理 + 1 次直连，最后错误: {error_msg[:200]}"
        }




@app.get("/")
async def root():
    """根路由"""
    return {
        "service": "微博采集 API",
        "version": "1.0.0",
        "pid": os.getpid(),
        "endpoints": {
            "/search": "搜索微博，返回原始 JSON",
            "/search/multi": "搜索多页微博",
            "/user/weibo": "获取用户微博列表",
            "/user/profile": "获取用户资料",
            "/fingerprint": "查看当前进程的浏览器指纹",
            "/test/network": "测试网络连接",
            "/test/proxies": "测试所有代理的可用性（完整测试）",
            "/proxy/status": "查看代理池状态",
            "/cookies/status": "查看 Cookie 状态",
            "/docs": "API 文档",
        }
    }


@app.get("/test/network")
async def test_network():
    """测试网络连接和浏览器状态"""
    global browser_page, browser_context

    results = {}

    # 1. 检查浏览器状态
    try:
        if browser_page:
            is_closed = browser_page.is_closed()
            results["browser"] = {
                "status": "ok" if not is_closed else "closed",
                "current_url": browser_page.url,
                "is_closed": is_closed
            }
        else:
            results["browser"] = {"status": "not_initialized"}
    except Exception as e:
        results["browser"] = {"status": "error", "error": str(e)[:200]}

    # 2. 测试 DNS 解析
    import socket
    try:
        ip = socket.gethostbyname("m.weibo.cn")
        results["dns"] = {"status": "ok", "ip": ip}
    except Exception as e:
        results["dns"] = {"status": "error", "error": str(e)}

    # 3. 测试 TCP 连接
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(5)
        result = sock.connect_ex(("m.weibo.cn", 443))
        sock.close()
        results["tcp"] = {"status": "ok" if result == 0 else "failed", "code": result}
    except Exception as e:
        results["tcp"] = {"status": "error", "error": str(e)}

    # 4. 测试浏览器访问微博首页
    if browser_page and not browser_page.is_closed():
        try:
            print("[网络测试] 尝试访问微博首页...")
            await browser_page.goto("https://m.weibo.cn", wait_until="domcontentloaded", timeout=15000)
            results["browser_access"] = {
                "status": "ok",
                "final_url": browser_page.url
            }
            print("[网络测试] 微博首页访问成功")
        except Exception as e:
            results["browser_access"] = {
                "status": "error",
                "error": str(e)[:300]
            }
            print(f"[网络测试] 浏览器访问失败: {str(e)[:200]}")

    return JSONResponse(content=results)


@app.get("/test/proxies")
async def test_proxies():
    """测试所有代理是否可用，返回每个代理的详细状态"""

    # 获取所有代理
    all_proxies = await proxy_manager.get_all_proxy_urls()

    if not all_proxies:
        return JSONResponse(content={
            "error": "代理池为空",
            "total_proxies": 0,
            "results": []
        })

    # 测试目标：访问微博搜索 API
    test_url = "https://www.baidu.com"
    test_timeout = 10  # 每个代理最多测试 10 秒

    results = {
        "total_proxies": len(all_proxies),
        "test_url": "https://www.baidu.com",
        "test_timeout_seconds": test_timeout,
        "results": [],
        "summary": {
            "working": 0,
            "blocked": 0,
            "failed": 0,
            "timeout": 0,
            "success_rate": "0%"
        }
    }

    print(f"[代理测试] 开始测试 {len(all_proxies)} 个代理...")

    # 逐个测试所有代理
    for index, proxy_url in enumerate(all_proxies):
        proxy_result = {
            "index": index + 1,
            "proxy": proxy_url,
            "status": "unknown",
            "response_time_ms": 0,
            "http_status": None,
            "error": None
        }

        start_time = time.time()
        try:
            async with httpx.AsyncClient(proxy=proxy_url, timeout=test_timeout) as client:
                response = await client.get(
                    test_url,
                    headers={
                        "Accept": "application/json, text/plain, */*",
                        "X-Requested-With": "XMLHttpRequest",
                        "MWeibo-Pwa": "1",
                        "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 Safari/604.1 Weibo (iPhone10,3__weibo__9.11.2__iphone__os16.0)"
                    }
                )

                response_time = int((time.time() - start_time) * 1000)
                proxy_result["response_time_ms"] = response_time
                proxy_result["http_status"] = response.status_code

                if response.status_code == 200:
                    proxy_result["status"] = "working"
                    results["summary"]["working"] += 1
                elif response.status_code in [418, 432, 403, 401]:
                    proxy_result["status"] = "blocked"
                    proxy_result["error"] = f"HTTP {response.status_code} (风控)"
                    results["summary"]["blocked"] += 1
                else:
                    proxy_result["status"] = "failed"
                    proxy_result["error"] = f"HTTP {response.status_code}"
                    results["summary"]["failed"] += 1

        except httpx.TimeoutException:
            proxy_result["status"] = "timeout"
            proxy_result["error"] = "连接超时"
            results["summary"]["timeout"] += 1
        except httpx.ConnectError as e:
            proxy_result["status"] = "failed"
            proxy_result["error"] = f"连接失败: {str(e)[:80]}"
            results["summary"]["failed"] += 1
        except Exception as e:
            proxy_result["status"] = "error"
            proxy_result["error"] = str(e)[:80]
            results["summary"]["failed"] += 1

        results["results"].append(proxy_result)

        # 每测试 10 个代理输出一次进度
        if (index + 1) % 10 == 0:
            print(f"[代理测试] 已完成 {index + 1}/{len(all_proxies)} ({(index + 1) / len(all_proxies) * 100:.1f}%)")

    # 计算成功率
    total_tested = len(all_proxies)
    if total_tested > 0:
        success_rate = (results["summary"]["working"] / total_tested) * 100
        results["summary"]["success_rate"] = f"{success_rate:.1f}%"

    print(f"[代理测试] 完成! 成功: {results['summary']['working']}, "
          f"被封: {results['summary']['blocked']}, "
          f"失败: {results['summary']['failed']}, "
          f"超时: {results['summary']['timeout']}")

    return JSONResponse(content=results)


@app.get("/fingerprint")
async def get_fingerprint():
    """获取当前进程的浏览器指纹和统计信息"""
    if current_fingerprint:
        minutes_running = (time.time() - last_full_reset_time) / 60 if last_full_reset_time > 0 else 0
        next_full_reset = max(0, config.FULL_RESET_INTERVAL_MINUTES - minutes_running)
        
        return JSONResponse(content={
            "pid": current_fingerprint.get("pid"),
            "stats": {
                "total_requests": request_count,
                "requests_since_rest": requests_since_rest,
                "fingerprint_resets": fingerprint_reset_count,
                "minutes_since_reset": round(minutes_running, 1),
                "next_full_reset_in_minutes": round(next_full_reset, 1),
                "next_rest_in": max(0, config.REQUESTS_BEFORE_REST - requests_since_rest),
                "next_refresh_in": config.REFRESH_BROWSER_EVERY - (request_count % config.REFRESH_BROWSER_EVERY) if request_count > 0 else config.REFRESH_BROWSER_EVERY,
            },
            "fingerprint": {
                "user_agent": current_fingerprint.get("user_agent"),
                "viewport": current_fingerprint.get("viewport"),
                "locale": current_fingerprint.get("locale"),
                "timezone": current_fingerprint.get("timezone_id"),
                "platform": current_fingerprint.get("platform"),
                "hardware_concurrency": current_fingerprint.get("hardware_concurrency"),
                "device_memory": current_fingerprint.get("device_memory"),
                "webgl_vendor": current_fingerprint.get("webgl_vendor"),
                "webgl_renderer": current_fingerprint.get("webgl_renderer"),
                "device_scale_factor": current_fingerprint.get("device_scale_factor"),
            }
        })
    return JSONResponse(content={"error": "浏览器未初始化"}, status_code=503)


@app.get("/search")
async def search(
    keyword: str = Query(..., description="搜索关键词", min_length=1),
    page: int = Query(1, description="页码", ge=1, le=50),
    type: SearchType = Query(SearchType.REALTIME, description="搜索类型: realtime=实时, video=视频"),
    use_cookie: bool = Query(False, description="是否使用 Cookie（降低风控风险）"),
):
    """
    搜索微博

    - **keyword**: 搜索关键词
    - **page**: 页码（1-50）
    - **type**: 搜索类型（realtime=实时，video=视频）
    - **use_cookie**: 是否使用 Cookie（默认 True，推荐使用以降低风控风险）

    返回微博 API 的原始 JSON 响应
    """
    response = await fetch_search_result(keyword, page, type.value, use_cookie)
    return JSONResponse(content=response)


@app.get("/search/multi")
async def search_multi(
    keyword: str = Query(..., description="搜索关键词", min_length=1),
    pages: int = Query(3, description="爬取页数", ge=1, le=10),
    delay: float = Query(1.5, description="请求间隔（秒）", ge=0.5, le=5),
    type: SearchType = Query(SearchType.REALTIME, description="搜索类型: realtime=实时, video=视频"),
):
    """
    搜索多页微博

    - **keyword**: 搜索关键词
    - **pages**: 爬取页数（1-10）
    - **delay**: 请求间隔秒数
    - **type**: 搜索类型（realtime=实时，video=视频）

    返回多页合并的原始数据
    """
    all_responses = []

    for page_num in range(1, pages + 1):
        response = await fetch_search_result(keyword, page_num, type.value)
        all_responses.append({
            "page": page_num,
            "data": response
        })

        # 如果请求失败，停止继续爬取
        if response.get("ok") == -1:
            break

        if page_num < pages:
            # 随机延迟，避免触发风控
            await asyncio.sleep(delay + random.uniform(0.5, 1.5))
    
    return JSONResponse(content={
        "keyword": keyword,
        "type": type.value,
        "total_pages": pages,
        "results": all_responses
    })


# ==================== 用户微博列表接口 ====================

async def fetch_with_browser(
    url: str,
    headers: dict,
    cookies: dict,
    max_retries: int = 3,
    context: str = "请求",
    use_cookie: bool = True
) -> dict:
    """使用浏览器方式发送请求（完全模拟真实浏览器环境）

    Args:
        url: 请求 URL
        headers: 请求头（会自动合并浏览器默认 headers）
        cookies: 额外的 cookies（如果 use_cookie=True，会从数据库加载）
        max_retries: 最大重试次数
        context: 上下文标识（用于日志）
        use_cookie: 是否从数据库加载 Cookie

    Returns:
        统一格式的响应:
        {
            "ok": 1,              # 1=成功, -1=失败, -100=验证码
            "status": 200,        # HTTP 状态码
            "data": {...},        # 响应数据
            "error": "..."        # 错误信息（如果有）
        }
    """
    global browser_page, browser_context, last_captcha_time

    pid = os.getpid()

    # 从 URL 提取 host 用于 Cookie domain
    from urllib.parse import urlparse
    url_host = urlparse(url).netloc

    for attempt in range(max_retries):
        try:
            # 请求前随机延迟（模拟人类行为）
            await asyncio.sleep(random.uniform(0.3, 1.0))

            # 检查浏览器是否需要刷新
            current_time = time.time()
            if not browser_page or browser_page.is_closed():
                print(f"[PID:{pid}][{context}] 浏览器页面已关闭，重新初始化...")
                await reset_browser_with_new_fingerprint()

            # 从数据库加载 Cookie
            xsrf_token = None
            if use_cookie:
                cookie_str = await get_next_cookie()
                if cookie_str:
                    print(f"[PID:{pid}][{context}] 已加载 Cookie")
                    # 解析并添加 Cookie 到浏览器上下文
                    try:
                        cookie_dict = parse_cookie_string(cookie_str)
                        cookie_list = []
                        for name, value in cookie_dict.items():
                            # 动态设置 domain
                            if 'weibo' in url_host:
                                domain = '.weibo.com'
                            elif 'sina' in url_host:
                                domain = '.sina.com.cn'
                            else:
                                domain = url_host
                            cookie_list.append({
                                "name": name,
                                "value": value,
                                "domain": domain,
                                "path": "/"
                            })
                        await browser_context.add_cookies(cookie_list)

                        # 提取 XSRF-TOKEN
                        xsrf_token = extract_xsrf_token(cookie_str)
                        if xsrf_token:
                            print(f"[PID:{pid}][{context}] 已提取 XSRF-TOKEN")
                    except Exception as e:
                        print(f"[PID:{pid}][{context}] 添加自定义 Cookie 失败: {e}")

            # 从请求 headers 中提取 referer 用于确定访问页面
            referer = headers.get("referer", "")

            # 先访问目标页面建立会话上下文（关键步骤）
            if referer:
                print(f"[PID:{pid}][{context}] 先访问来源页建立会话: {referer}")
                try:
                    await browser_page.goto(referer, wait_until="domcontentloaded", timeout=20000)
                    await asyncio.sleep(random.uniform(0.5, 1.0))
                except Exception as e:
                    print(f"[PID:{pid}][{context}] 访问来源页失败: {e}，继续...")
            elif url_host:
                # 如果没有 referer，尝试访问主页
                if 'm.weibo.cn' in url_host:
                    page_url = "https://m.weibo.cn"
                elif 'weibo.com' in url_host:
                    page_url = "https://weibo.com"
                else:
                    page_url = f"https://{url_host}"
                print(f"[PID:{pid}][{context}] 先访问页面建立会话: {page_url}")
                try:
                    await browser_page.goto(page_url, wait_until="domcontentloaded", timeout=20000)
                    await asyncio.sleep(random.uniform(0.5, 1.0))
                except Exception as e:
                    print(f"[PID:{pid}][{context}] 访问页面失败: {e}，继续...")

            # 构建请求头，添加 x-xsrf-token
            request_headers = dict(headers)
            if xsrf_token:
                request_headers["x-xsrf-token"] = xsrf_token

            print(f"[PID:{pid}][{context}] 使用浏览器 fetch 请求: {url}")

            # 构建 headers JSON
            headers_json = json.dumps(request_headers)

            # 在浏览器中执行 fetch 请求
            api_result = await browser_page.evaluate(f"""
                async () => {{
                    try {{
                        const response = await fetch('{url}', {{
                            method: 'GET',
                            credentials: 'include',
                            headers: {headers_json}
                        }});

                        const status = response.status;
                        const data = await response.json();

                        return {{ ok: 1, status: status, data: data }};
                    }} catch (error) {{
                        return {{ ok: -1, error: error.message }};
                    }}
                }}
            """)

            print(f"[PID:{pid}][{context}] fetch 结果: {api_result}")

            if api_result.get("ok") == 1:
                response_data = api_result.get("data")
                http_status = api_result.get("status")
                print(f"[PID:{pid}][{context}] fetch 响应: HTTP {http_status}")

                # 检测风控状态码
                if http_status in [432, 418, 403, 401]:
                    print(f"[PID:{pid}][{context}] 触发风控 (HTTP {http_status})")

                    # 进入风控冷却期
                    cooldown = config.CAPTCHA_COOLDOWN + random.uniform(2, 5)
                    print(f"[PID:{pid}][{context}] 进入风控冷却期 {cooldown:.1f}s")
                    await asyncio.sleep(cooldown)

                    # 更换指纹重试
                    print(f"[PID:{pid}][{context}] 更换浏览器指纹重试...")
                    await reset_browser_with_new_fingerprint()
                    continue

                # 检查是否触发验证码
                if isinstance(response_data, dict) and response_data.get("ok") == -100:
                    print(f"[PID:{pid}][{context}] API 返回验证码错误 (ok=-100)")

                    # 检查页面中是否有验证码元素
                    has_geetest = await browser_page.evaluate("""
                        () => {
                            return !!document.querySelector('.geetest_holder') ||
                                   !!document.querySelector('#geetest-captcha') ||
                                   !!document.querySelector('[data-captcha-id]');
                        }
                    """)

                    if has_geetest:
                        if config.HEADLESS:
                            print(f"[PID:{pid}][{context}] 无头模式无法完成验证码，更换指纹重试...")
                            await reset_browser_with_new_fingerprint()
                            await asyncio.sleep(2)
                            continue
                        else:
                            print(f"[PID:{pid}][{context}] 等待用户完成验证码...")
                            await asyncio.sleep(30)
                            continue

                    return {
                        "ok": -100,
                        "status": http_status,
                        "error": "触发验证码或 Cookie 失效",
                        "data": response_data
                    }

                # 成功获取数据
                print(f"[PID:{pid}][{context}] 成功获取数据")
                return {
                    "ok": 1,
                    "status": http_status,
                    "data": response_data
                }
            else:
                error_msg = api_result.get("error", "未知错误")
                print(f"[PID:{pid}][{context}] fetch 失败: {error_msg}")

                # 如果是最后一次重试，返回错误
                if attempt == max_retries - 1:
                    return {
                        "ok": -1,
                        "status": 0,
                        "error": f"fetch 失败: {error_msg}"
                    }

                # 否则更换指纹重试
                await reset_browser_with_new_fingerprint()
                await asyncio.sleep(2)
                continue

        except Exception as e:
            error_msg = str(e)
            print(f"[PID:{pid}][{context}] 异常: {error_msg[:300]}")

            # 检查是否应该重试
            should_retry = any(err in error_msg.lower() for err in [
                "captcha", "geetest", "timeout", "network", "target closed", "net::", "context"
            ])

            if should_retry and attempt < max_retries - 1:
                print(f"[PID:{pid}][{context}] 错误可重试，更换指纹...")
                await reset_browser_with_new_fingerprint()
                await asyncio.sleep(2)
                continue

            return {
                "ok": -1,
                "status": 0,
                "error": f"请求异常: {error_msg[:200]}"
            }

    # 所有重试都失败
    return {
        "ok": -1,
        "status": 0,
        "error": f"所有重试失败 (尝试 {max_retries} 次)"
    }


def get_random_http_fingerprint() -> dict:
    """生成随机 HTTP 指纹（多样化 headers）- 增强版"""

    # 随机 User-Agent（基于真实浏览器）
    user_agents = [
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36",
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36",
    ]

    # 随机 Accept-Language
    accept_languages = [
        "zh-CN,zh;q=0.9,en;q=0.8",
        "zh-CN,zh;q=0.9",
        "zh-CN,zh;q=0.9,en-US;q=0.8,en;q=0.7",
        "zh-CN,zh;q=0.9,ja;q=0.8,en;q=0.7",
    ]

    # 随机 Sec-CH-UA
    sec_ch_uas = [
        '"Not(A:Brand";v="8", "Chromium";v="144", "Google Chrome";v="144"',
        '"Chromium";v="144", "Google Chrome";v="144", "Not-A.Brand";v="8"',
        '"Google Chrome";v="143", "Chromium";v="143", "Not-A.Brand";v="24"',
        '"Not_A Brand";v="8", "Chromium";v="144", "Google Chrome";v="144"',
    ]

    # 随机 Sec-CH-UA-Platform
    sec_ch_platforms = [
        '"macOS"',
        '"Windows"',
        '"Linux"',
    ]

    # 随机 Accept-Encoding
    accept_encodings = [
        "gzip, deflate, br, zstd",
        "gzip, deflate, br",
        "gzip, deflate",
    ]

    # 生成随机 traceparent（模拟真实的分布式追踪 ID）
    trace_id = ''.join(random.choices('0123456789abcdef', k=32))
    parent_id = ''.join(random.choices('0123456789abcdef', k=16))
    traceparent = f"00-{trace_id}-{parent_id}-00"

    user_agent = random.choice(user_agents)

    return {
        "user-agent": user_agent,
        "accept": "application/json, text/plain, */*",
        "accept-language": random.choice(accept_languages),
        "accept-encoding": random.choice(accept_encodings),
        "sec-ch-ua": random.choice(sec_ch_uas),
        "sec-ch-ua-platform": random.choice(sec_ch_platforms),
        "traceparent": traceparent,
    }


async def fetch_with_proxy_retry(
    url: str,
    headers: dict,
    cookies: dict,
    method: str = "GET",
    max_retries: int = 30,  # 最多尝试 30 个不同 IP
    context: str = "请求"
) -> httpx.Response:
    """使用代理重试机制发送 HTTP 请求（新版 - 动态轮询代理池）

    失效策略：只有超时超过 360 秒的代理才标记为失效

    增强功能：
    1. 动态从代理池轮询获取代理，最多尝试 30 个不同 IP
    2. 超时 >= 360 秒才标记代理失效
    3. 其他错误（风控、HTTP错误、连接失败）不标记失效，直接重试
    4. 每次重试自动更换 HTTP 指纹（UA、headers）
    5. 智能延迟模拟人类行为
    6. 优化资源管理，避免文件描述符耗尽
    """
    pid = os.getpid()
    last_error = None

    # 🔥 创建单个 session 重用（避免每次重试都创建新 session）
    try:
        async with AsyncSession(impersonate="chrome120") as session:
            # 动态轮询代理池，最多尝试 max_retries 次
            for attempt in range(1, max_retries + 1):
                # 请求前随机延迟（模拟人类行为）
                await asyncio.sleep(random.uniform(0.3, 1.0))

                # 从代理池获取下一个可用代理
                proxy = await proxy_manager.get_next_proxy()

                if proxy is None:
                    console.print(f"[red][PID:{pid}][{context}] 🔴 无可用代理（尝试 {attempt}/{max_retries}），放弃请求[/red]")
                    break

                console.print(f"[cyan][PID:{pid}][{context}] 🔄 代理 {attempt}/{max_retries}: {proxy[:50]}...[/cyan]")

                # 生成随机 HTTP 指纹
                fingerprint = get_random_http_fingerprint()

                # 合并 headers（优先使用传入的 headers）
                merged_headers = {**fingerprint, **headers}

                console.print(f"[dim][PID:{pid}][{context}] 🎭 UA: {fingerprint['user-agent'][:60]}...[/dim]")

                # 记录请求开始时间
                start_time = time.time()

                try:
                    # 🔥 使用同一个 session 发送请求（重用连接）
                    if method == "GET":
                        response = await session.get(
                            url,
                            headers=merged_headers,
                            cookies=cookies,
                            proxy=proxy,
                            timeout=30
                        )
                    else:
                        response = await session.post(
                            url,
                            headers=merged_headers,
                            cookies=cookies,
                            proxy=proxy,
                            timeout=30
                        )

                    elapsed = time.time() - start_time

                    # 🔍 检测风控或 HTTP 错误 - 触发风控后立即更换代理
                    if response.status_code in [432, 418, 403, 414]:
                        console.print(f"[yellow][PID:{pid}][{context}] ⚠️ 触发风控 (HTTP {response.status_code})，立即更换代理[/yellow]")

                        # 🔥 关键修复：触发风控的代理标记为失效，避免重复使用
                        await proxy_manager.mark_proxy_failed(proxy)
                        console.print(f"[yellow][PID:{pid}][{context}] 🗑️ 已将触发风控的代理标记为失效[/yellow]")

                        # 进入风控冷却期
                        cooldown = config.CAPTCHA_COOLDOWN + random.uniform(3, 8)
                        console.print(f"[yellow][PID:{pid}][{context}] ⏰ 进入风控冷却期 {cooldown:.1f}s[/yellow]")
                        await asyncio.sleep(cooldown)

                        # 继续尝试下一个代理
                        continue

                    # HTTP 4xx/5xx 错误 - 不标记失效，仅重试
                    if response.status_code >= 400:
                        console.print(f"[yellow][PID:{pid}][{context}] ⚠️ HTTP 错误 {response.status_code}，不标记失效[/yellow]")
                        continue

                    # 请求成功 - 构造兼容 httpx.Response 的对象
                    console.print(f"[green][PID:{pid}][{context}] ✅ 成功 (HTTP {response.status_code}, 耗时 {elapsed:.2f}s)[/green]")

                    # 创建兼容对象
                    class CompatibleResponse:
                        def __init__(self, curl_response):
                            self.status_code = curl_response.status_code
                            self.headers = curl_response.headers
                            self.content = curl_response.content
                            self._text = curl_response.text
                            self._curl_response = curl_response

                        def json(self):
                            return self._curl_response.json()

                        @property
                        def text(self):
                            return self._text

                    return CompatibleResponse(response)

                except Exception as e:
                    elapsed = time.time() - start_time
                    error_msg = str(e)
                    last_error = e

                    # 🔥 只有超时 >= 360 秒才标记代理失效
                    if "timeout" in error_msg.lower() or "timed out" in error_msg.lower():
                        if elapsed >= config.PROXY_TIMEOUT_THRESHOLD:
                            console.print(
                                f"[red][PID:{pid}][{context}] ⏱️ 请求超时 {elapsed:.1f}s (>= {config.PROXY_TIMEOUT_THRESHOLD}s)，"
                                f"标记代理失效[/red]"
                            )
                            await proxy_manager.mark_proxy_failed(proxy)
                        else:
                            console.print(
                                f"[yellow][PID:{pid}][{context}] ⏱️ 请求超时 {elapsed:.1f}s (< {config.PROXY_TIMEOUT_THRESHOLD}s)，"
                                f"不标记失效[/yellow]"
                            )
                        continue

                    # 连接失败 - 不标记失效，仅重试
                    elif "connect" in error_msg.lower() or "connection" in error_msg.lower():
                        console.print(f"[yellow][PID:{pid}][{context}] ❌ 代理连接失败: {error_msg[:100]}，不标记失效[/yellow]")
                        continue

                    # 其他异常 - 不标记失败，仅重试
                    else:
                        console.print(f"[yellow][PID:{pid}][{context}] ⚠️ 异常: {error_msg[:100]}，不标记失效[/yellow]")
                        continue

            # 所有代理都失败了
            console.print(f"[red][PID:{pid}][{context}] 🔴 已尝试 {max_retries} 个代理均失败，放弃请求[/red]")

    except Exception as e:
        # Session 创建失败
        console.print(f"[red][PID:{pid}][{context}] ❌ 创建 session 失败: {e}[/red]")
        raise

    # 生成新的随机指纹
    fingerprint = get_random_http_fingerprint()
    merged_headers = {**fingerprint, **headers}

    print(f"[PID:{pid}][{context}] 🎭 直连 UA: {fingerprint['user-agent'][:60]}...")

    try:
        start_time = time.time()

        # 🔥 直连模式使用 curl_cffi
        async with AsyncSession(impersonate="chrome120") as session:
            if method == "GET":
                response = await session.get(
                    url,
                    headers=merged_headers,
                    cookies=cookies,
                    timeout=30
                )
            else:
                response = await session.post(
                    url,
                    headers=merged_headers,
                    cookies=cookies,
                    timeout=30
                )

            elapsed = time.time() - start_time

            # 检测风控状态码
            if response.status_code in [432, 418, 403, 401]:
                print(f"[PID:{pid}][{context}] 🔴 直连也触发风控 (HTTP {response.status_code})")
                raise Exception(f"所有代理和直连均触发风控 (HTTP {response.status_code})")

            print(f"[PID:{pid}][{context}] ✅ 直连请求成功 (HTTP {response.status_code}, 耗时 {elapsed:.2f}s) [curl_cffi/Chrome120]")

            # 创建兼容对象
            class CompatibleResponse:
                def __init__(self, curl_response):
                    self.status_code = curl_response.status_code
                    self.headers = curl_response.headers
                    self.content = curl_response.content
                    self._text = curl_response.text
                    self._curl_response = curl_response

                def json(self):
                    return self._curl_response.json()

                @property
                def text(self):
                    return self._text

            return CompatibleResponse(response)

    except Exception as e:
        # 直连也失败，抛出错误
        raise Exception(f"[{context}] 已尝试 {max_retries} 个代理和直连均失败: {str(e)}")


async def fetch_user_weibo(uid: str, page: int = 1, since_id: str = "") -> dict:
    """获取指定用户的微博列表（使用代理重试方式 - 支持指纹轮换和 Cookie 自动更换）"""

    pid = os.getpid()

    # 构建请求 URL
    url = f"https://weibo.com/ajax/statuses/mymblog?uid={uid}&page={page}&feature=0"
    if since_id:
        url += f"&since_id={since_id}"

    # 构建请求头（使用浏览器真实请求头作为参考）
    headers = {
        "accept": "application/json, text/plain, */*",
        "accept-language": "zh-CN,zh;q=0.9,en;q=0.8",
        "client-version": "3.0.0",
        "priority": "u=1, i",
        "referer": f"https://weibo.com/u/{uid}",
        "x-requested-with": "XMLHttpRequest",
        # 重要：服务端版本号，风控可能会检查
        "server-version": "v2026.02.06.1",
    }

    # 从数据库获取 Cookie
    cookie_str = await get_next_cookie()
    if not cookie_str:
        print(f"[PID:{pid}][用户微博] ❌ 无可用 Cookie")
        return {"ok": -1, "error": "无可用 Cookie"}

    # 解析 Cookie 字符串为字典
    cookies = parse_cookie_string(cookie_str)

    # 提取 XSRF-TOKEN 并添加到请求头
    xsrf_token = extract_xsrf_token(cookie_str)
    if xsrf_token:
        headers["x-xsrf-token"] = xsrf_token
        print(f"[PID:{pid}][用户微博] 已提取 XSRF-TOKEN")
    else:
        print(f"[PID:{pid}][用户微博] ⚠️ 未找到 XSRF-TOKEN")

    try:
        print(f"[PID:{pid}][用户微博] 开始请求: {url}")

        # 使用代理重试方式请求
        response = await fetch_with_proxy_retry(
            url=url,
            headers=headers,
            cookies=cookies,
            method="GET",
            max_retries=15,
            context="用户微博"
        )

        # 处理返回结果
        if response.status_code == 200:
            data = response.json()
            if data and data.get("ok") == 1:
                print(f"[PID:{pid}][用户微博] ✅ 采集成功")
                return data
            elif data and data.get("ok") == -100:
                print(f"[PID:{pid}][用户微博] ⚠️ 触发验证码或 Cookie 失效")
                return {"ok": -1, "error": "触发验证码或 Cookie 失效"}
            else:
                error_msg = data.get("msg", "未知错误") if data else "无返回数据"
                print(f"[PID:{pid}][用户微博] ❌ 返回错误: {error_msg}")
                return {"ok": -1, "error": error_msg}
        else:
            error_msg = f"HTTP {response.status_code}"
            print(f"[PID:{pid}][用户微博] ❌ 请求失败: {error_msg}")
            return {"ok": -1, "error": error_msg}

    except Exception as e:
        error_msg = str(e)
        print(f"[PID:{pid}][用户微博] ❌ 异常: {error_msg[:200]}")
        return {"ok": -1, "error": error_msg}


async def fetch_weibo_longtext(mid: str) -> dict:
    """获取指定微博的全文长文（使用代理重试方式 - 支持指纹轮换和 Cookie 自动更换）"""

    pid = os.getpid()

    # 构建请求 URL
    url = f"https://weibo.com/ajax/statuses/longtext?id={mid}"

    # 构建请求头（使用浏览器真实请求头作为参考）
    headers = {
        "accept": "application/json, text/plain, */*",
        "accept-language": "zh-CN,zh;q=0.9,en;q=0.8",
        "client-version": "3.0.0",
        "priority": "u=1, i",
        "referer": f"https://weibo.com/detail/{mid}",
        "x-requested-with": "XMLHttpRequest",
        # 重要：服务端版本号，风控可能会检查
        "server-version": "v2026.02.06.1",
    }

    # 从数据库获取 Cookie
    cookie_str = await get_next_cookie()
    if not cookie_str:
        print(f"[PID:{pid}][微博长文] ❌ 无可用 Cookie")
        return {"ok": -1, "error": "无可用 Cookie"}

    # 解析 Cookie 字符串为字典
    cookies = parse_cookie_string(cookie_str)

    # 提取 XSRF-TOKEN 并添加到请求头
    xsrf_token = extract_xsrf_token(cookie_str)
    if xsrf_token:
        headers["x-xsrf-token"] = xsrf_token
        print(f"[PID:{pid}][微博长文] 已提取 XSRF-TOKEN")
    else:
        print(f"[PID:{pid}][微博长文] ⚠️ 未找到 XSRF-TOKEN")

    try:
        print(f"[PID:{pid}][微博长文] 开始请求: {url}")

        # 使用代理重试方式请求
        response = await fetch_with_proxy_retry(
            url=url,
            headers=headers,
            cookies=cookies,
            method="GET",
            max_retries=15,
            context="微博长文"
        )

        # 处理返回结果
        if response.status_code == 200:
            data = response.json()
            if data and data.get("ok") == 1:
                print(f"[PID:{pid}][微博长文] ✅ 采集成功")
                return data
            elif data and data.get("ok") == -100:
                print(f"[PID:{pid}][微博长文] ⚠️ 触发验证码或 Cookie 失效")
                return {"ok": -1, "error": "触发验证码或 Cookie 失效"}
            else:
                error_msg = data.get("msg", "未知错误") if data else "无返回数据"
                print(f"[PID:{pid}][微博长文] ❌ 返回错误: {error_msg}")
                return {"ok": -1, "error": error_msg}
        else:
            error_msg = f"HTTP {response.status_code}"
            print(f"[PID:{pid}][微博长文] ❌ 请求失败: {error_msg}")
            return {"ok": -1, "error": error_msg}

    except Exception as e:
        error_msg = str(e)
        print(f"[PID:{pid}][微博长文] ❌ 异常: {error_msg[:200]}")
        return {"ok": -1, "error": error_msg}


async def fetch_user_profile(uid: str) -> dict:
    """获取指定用户的详细信息（使用增强版 httpx 方式 - 支持指纹轮换和 Cookie 自动更换）"""

    last_error = None
    pid = os.getpid()

    # 🔥 不限制尝试次数，直到所有 Cookie 都试完
    max_attempts = 50  # 最大尝试次数（防止无限循环）
    attempted_cookies = set()  # 记录已尝试的 cookie，避免重复

    for attempt in range(max_attempts):
        cookie_str = await get_next_cookie()
        if not cookie_str:
            # Cookie 列表为空，已经尝试重新加载但仍然没有
            return {"ok": -1, "error": "No cookies available after reload"}

        # 如果这个 cookie 已经尝试过，说明已经轮了一圈
        if cookie_str in attempted_cookies:
            print(f"[PID:{pid}][用户资料] 所有 {len(attempted_cookies)} 个 Cookie 都已尝试，全部失效")
            return {"ok": -1, "error": f"All {len(attempted_cookies)} cookies failed, last error: {last_error}"}

        attempted_cookies.add(cookie_str)

        # 提取 XSRF-TOKEN
        xsrf_token = extract_xsrf_token(cookie_str)
        if not xsrf_token:
            last_error = "XSRF-TOKEN not found in cookie"
            print(f"[PID:{pid}][用户资料] Cookie {attempt + 1}: ❌ 缺少 XSRF-TOKEN，标记为无效")
            # 标记并移除无效 Cookie
            await mark_cookie_invalid(cookie_str)
            continue

        # 构建请求 URL
        url = f"https://weibo.com/ajax/profile/info?uid={uid}"

        # 完整请求头（参考真实浏览器）
        headers = {
            "accept": "application/json, text/plain, */*",
            "accept-language": "zh-CN,zh;q=0.9,en;q=0.8",  # 会被随机指纹覆盖
            "cache-control": "no-cache",
            "client-version": "3.0.0",
            "pragma": "no-cache",
            "priority": "u=1, i",
            "referer": f"https://weibo.com/u/{uid}",
            "sec-ch-ua-mobile": "?0",
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "same-origin",
            "x-requested-with": "XMLHttpRequest",
            "x-xsrf-token": xsrf_token,
        }

        # 解析 cookie
        cookie_dict = parse_cookie_string(cookie_str)

        try:
            print(f"[PID:{pid}][用户资料] Cookie {attempt + 1}: 开始请求")

            # 使用增强版 fetch（自动更换所有代理和指纹）
            response = await fetch_with_proxy_retry(
                url=url,
                headers=headers,
                cookies=cookie_dict,
                max_retries=30,
                context="用户资料"
            )

            if response.status_code == 200:
                data = response.json()
                # 检查返回数据是否有效
                if data.get("ok") == 1:
                    print(f"[PID:{pid}][用户资料] ✅ Cookie 成功 (尝试 {attempt + 1}，有效 Cookie)")
                    return data
                elif data.get("ok") == -100:
                    # 触发验证码或 Cookie 失效
                    last_error = "触发验证码或 Cookie 失效"
                    print(f"[PID:{pid}][用户资料] ⚠️ 触发验证码 (尝试 {attempt + 1})，标记为无效")
                    # 标记并移除无效 Cookie
                    await mark_cookie_invalid(cookie_str)
                    # 等待一段时间后换 Cookie 重试
                    await asyncio.sleep(random.uniform(2, 4))
                    continue
                else:
                    last_error = data.get("msg", "未知错误")
                    print(f"[PID:{pid}][用户资料] Cookie 返回错误 (尝试 {attempt + 1}): {last_error}")
                    # 标记并移除无效 Cookie
                    await mark_cookie_invalid(cookie_str)
                    continue
            elif response.status_code in [401, 403]:
                # Cookie 认证失败
                last_error = f"HTTP {response.status_code} - Cookie 认证失败"
                print(f"[PID:{pid}][用户资料] ❌ Cookie 认证失败 (尝试 {attempt + 1}): {last_error}")
                # 标记并移除无效 Cookie
                await mark_cookie_invalid(cookie_str)
                continue
            else:
                last_error = f"HTTP {response.status_code}"
                print(f"[PID:{pid}][用户资料] ❌ 失败 (尝试 {attempt + 1}): {last_error}")
                continue

        except Exception as e:
            last_error = str(e)
            print(f"[PID:{pid}][用户资料] ❌ 异常 (尝试 {attempt + 1}): {last_error[:200]}")

            # 如果是所有代理都失败，可能是网络问题或被全面封禁
            if "所有" in last_error and "失败" in last_error:
                print(f"[PID:{pid}][用户资料] ⚠️ 所有代理都失败，可能遭遇全面封禁")
                # 不标记 Cookie 为无效，等待后重试
                await asyncio.sleep(random.uniform(5, 10))

            continue

    # 达到最大尝试次数
    return {
        "ok": -1,
        "error": f"Max attempts ({max_attempts}) reached, last error: {last_error}"
    }


@app.get("/user/weibo")
async def get_user_weibo(
    uid: str = Query(..., description="用户 UID", min_length=1),
    page: int = Query(1, description="页码", ge=1, le=100),
    since_id: str | None = Query(None, description="分页游标（可选，用于翻页）"),
):
    """
    获取指定用户的微博列表
    
    - **uid**: 用户 UID
    - **page**: 页码
    - **since_id**: 分页游标（可选，从上一页响应中获取）
    
    返回微博 API 的原始 JSON 响应
    Cookie 采用轮询方式，每次请求更换一个
    """
    response = await fetch_user_weibo(uid, page, since_id or "")
    return JSONResponse(content=response)


@app.get("/user/profile")
async def get_user_profile(
    uid: str = Query(..., description="用户 UID", min_length=1),
):
    """
    获取指定用户的详细信息
    
    - **uid**: 用户 UID
    
    返回用户信息，包括昵称、简介、粉丝数、关注数、微博数等
    Cookie 采用轮询方式，每次请求更换一个
    """
    response = await fetch_user_profile(uid)
    return JSONResponse(content=response)


@app.get("/weibo/longtext")
async def get_weibo_longtext(
    id: str = Query(..., description="微博 ID（mid）", min_length=1),
):
    """
    获取指定微博的全文长文

    - **id**: 微博 ID（mid）

    返回微博长文 API 的原始 JSON 响应（data.longTextContent 即全文）
    Cookie 采用轮询方式，每次请求更换一个
    """
    response = await fetch_weibo_longtext(id)
    return JSONResponse(content=response)


@app.get("/user/weibo/multi")
async def get_user_weibo_multi(
    uid: str = Query(..., description="用户 UID", min_length=1),
    pages: int = Query(3, description="爬取页数", ge=1, le=20),
    delay: float = Query(1.0, description="请求间隔（秒）", ge=0.5, le=5),
):
    """
    获取指定用户的多页微博
    
    - **uid**: 用户 UID
    - **pages**: 爬取页数
    - **delay**: 请求间隔秒数
    
    自动处理 since_id 分页
    """
    all_responses = []
    since_id = ""
    
    for page_num in range(1, pages + 1):
        response = await fetch_user_weibo(uid, page_num, since_id)
        all_responses.append({
            "page": page_num,
            "data": response
        })
        
        # 提取下一页的 since_id
        if response.get("ok") == 1 and response.get("data"):
            since_id = response["data"].get("since_id", "")
        else:
            # 请求失败或没有更多数据，停止
            break
        
        if page_num < pages:
            await asyncio.sleep(delay + random.uniform(0.2, 0.8))
    
    return JSONResponse(content={
        "uid": uid,
        "total_pages": len(all_responses),
        "results": all_responses
    })


@app.get("/cookies/reload")
async def reload_cookies():
    """重新加载数据库中的 cookie"""
    await load_cookies_from_db()
    return JSONResponse(content={
        "ok": 1,
        "count": len(weibo_cookies),
        "current_index": cookie_index
    })


@app.get("/cookies/status")
async def cookies_status():
    """查看 cookie 状态"""
    return JSONResponse(content={
        "total": len(weibo_cookies),
        "current_index": cookie_index % len(weibo_cookies) if weibo_cookies else 0,
        "usage_count": cookie_index
    })


@app.get("/proxy/status")
async def proxy_status():
    """获取代理池实时状态"""
    status = await proxy_manager.get_proxy_status()
    return JSONResponse(content=status)


@app.get("/proxy/reload")
async def reload_proxy():
    """手动重新加载代理池"""
    success = await proxy_manager.reload()
    status = await proxy_manager.get_proxy_status()
    return JSONResponse(content={
        "ok": 1 if success else 0,
        "message": "代理池重载成功" if success else "代理池重载失败",
        "status": status
    })


if __name__ == "__main__":
    import argparse
    import uvicorn
    
    parser = argparse.ArgumentParser(description="微博采集代理 API 服务")
    parser.add_argument("--host", default="0.0.0.0", help="监听地址")
    parser.add_argument("--port", type=int, default=8002, help="监听端口")
    parser.add_argument("--workers", type=int, default=1, help="工作进程数，每个进程有独立的浏览器指纹")
    parser.add_argument("--reload", action="store_true", help="开启热重载（开发模式）")
    args = parser.parse_args()
    
    print(f"启动微博采集代理 API 服务")
    print(f"  - 地址: {args.host}:{args.port}")
    print(f"  - 进程数: {args.workers}")
    print(f"  - 每个进程将使用不同的浏览器指纹")
    
    if args.reload:
        # 开发模式，单进程 + 热重载
        uvicorn.run("api:app", host=args.host, port=args.port, reload=True)
    else:
        # 生产模式，多进程
        uvicorn.run("api:app", host=args.host, port=args.port, workers=args.workers)
