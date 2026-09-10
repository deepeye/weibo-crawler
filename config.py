"""
微博爬虫配置文件
"""

# 搜索关键词
KEYWORDS = ["高市早苗"]

# 爬取页数
MAX_PAGES = 5

# ==================== 风控配置 ====================

# 请求间隔基础值（秒）- 实际间隔会在此基础上随机浮动
REQUEST_DELAY_BASE = 2  # 从 1 增加到 2
REQUEST_DELAY_RANDOM = 2  # 从 0 增加到 2（实际延迟 2-4 秒）

# 连续请求次数阈值，达到后自动休息
REQUESTS_BEFORE_REST = 10
REST_DURATION_BASE = 3  # 休息基础时间（秒）
REST_DURATION_RANDOM = 1  # 随机增加 0-15 秒

# 定期刷新浏览器状态（每多少次请求后刷新）
REFRESH_BROWSER_EVERY = 30

# 触发验证码后的冷却时间（秒）
CAPTCHA_COOLDOWN = 10  # 从 5 增加到 10 秒

# 定时完全重置（每运行多少分钟后强制重置所有状态）
FULL_RESET_INTERVAL_MINUTES = 30

# 每次完全重置后的休息时间（秒）
FULL_RESET_REST_DURATION = 5

# ==================== 增强型频率限制配置 ====================

# 令牌桶容量（最大并发请求数）
RATE_LIMITER_CAPACITY = 10

# 令牌恢复速率（每秒恢复的令牌数）
RATE_LIMITER_REFILL_RATE = 1.0

# 请求监控窗口大小（统计最近 N 个请求）
REQUEST_MONITOR_WINDOW_SIZE = 100

# 风控检测阈值
BLOCKED_RATE_THRESHOLD = 0.3  # 被封率超过 30% 触发降速
SUCCESS_RATE_THRESHOLD = 0.5  # 成功率低于 50% 触发降速

# ==================== 浏览器配置 ====================

# 无头模式 - 服务器上使用 True
HEADLESS = True

# 是否使用持久化上下文（关闭以避免 SingletonLock 冲突）
USE_PERSISTENT_CONTEXT = False
USER_DATA_DIR = "/tmp/weibo-browser-data"

# ==================== 输出配置 ====================

OUTPUT_DIR = "output"
OUTPUT_FORMAT = "json"  # json 或 csv

# ==================== Cookie 配置 ====================
# 从浏览器复制你的 cookie，登录用户触发验证码概率更低
COOKIES = [
    # {"name": "SUB", "value": "xxx", "domain": ".weibo.cn"},
]

# User-Agent
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36"

# ==================== 代理配置 ====================
# 代理启用开关（设置为 False 禁用代理）
PROXY_ENABLED = False

# HTTP 代理接口配置
PROXY_HTTP_URL = "https://exclusive.proxy.qg.net/get?key=5CDBEC47&num=3&area=&isp=0&format=json&distinct=true&keep_alive=1440"
PROXY_HTTP_PARAMS = {}
PROXY_AUTH_USER = "5CDBEC47"
PROXY_AUTH_PASSWORD = "48BC8939D827"
PROXY_RELOAD_INTERVAL = 36000  # 代理池自动更新间隔（秒）

# 代理失效检测阈值
PROXY_TIMEOUT_THRESHOLD = 360  # 超时超过此秒数才标记代理失效

# 代理池共享文件（多 worker 共享）
PROXY_CACHE_FILE = "/tmp/weibo_proxy_cache.json"  # 代理池缓存文件路径
PROXY_CACHE_MAX_AGE = 90  # 缓存文件最大有效期（秒），超过此时间视为过期

# 代理池强制释放配置（get 返回 NO_AVAILABLE_CHANNEL 时触发）
PROXY_QUERY_URL = "https://exclusive.proxy.qg.net/query?key=5CDBEC47"  # 查询池子在用 IP
PROXY_DELETE_URL = "https://exclusive.proxy.qg.net/delete?key=5CDBEC47"  # 释放指定 IP（&ip=ip1,ip2,...）
PROXY_RELEASE_NUM = 3  # 单次释放的 IP 数量
PROXY_RELEASE_MAX_ATTEMPTS = 3  # delete→get 最多重试次数，全部失败则放弃
PROXY_RELEASE_LOCK_FILE = "/tmp/weibo_proxy_release.lock"  # 多 worker 释放互斥锁文件
PROXY_RELEASE_LOCK_TIMEOUT = 30  # 获取释放锁的最长等待秒数，超时跳过本次释放

# ==================== 废弃：Redis 代理池配置 ====================
# 已迁移到 HTTP 接口，以下配置不再使用
PROXY_REDIS_HOST = "10.19.0.123"
PROXY_REDIS_PORT = 6379
PROXY_REDIS_PASSWORD = "8085fbee2a31add7d363A60D440C2c9ea145d60e6f6f59ce9ca8APQ"
PROXY_REDIS_DB = 0
PROXY_REDIS_KEY = "proxy"

# 旧的单代理配置（保留兼容）
PROXY = None  # 从 HTTP 接口动态获取

# ==================== 数据库配置 ====================
DB_HOST = "rds1-bd.hubpd-internal.com"
DB_PORT = 6000
DB_USER = "crawl_prod"
DB_PASSWORD = "WNfUSjJZzsf5sn74j4qCQeLp5"
DB_NAME = "crawler_data_center"

# ==================== MAPI（微博原生 App API）配置 ====================
# 用于 /weibo/longtext 端点构造 statuses/extend 请求（uid+mid 参数化）。
# 这些值来自 App 抓包；签名 s/i 经验证可用，{} 仅表示该 mid 无扩展数据。
MAPI_GSID = "_2AkMvpUuAf8NhqwJRmP8Qzmjmb4Rxyg_EieKZ-bpbJRM3HRl-3D9kqlUttRWJWcA8gH4muqPim7vBRGT4uVmwwg.."
MAPI_S = "62772b60"           # 请求签名
MAPI_I = "dirs6dm"            # 签名伴随标识
MAPI_DID = "c12eff4f7422e24aa153918615a3508373d91bac"  # 设备 ID
MAPI_AID = "01AR_xW5_q."      # App ID
MAPI_UA = "samsung-SM-G7108V__weibo__6.12.3__android__android4.3"  # 设备/版本（URL 参数）
MAPI_FROM = "106C395010"      # 渠道
