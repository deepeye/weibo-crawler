"""
代理管理器 - 从 HTTP 接口获取代理列表并实现轮询（支持多 worker 共享）
"""

import os
import json
import asyncio
import fcntl
import time
import httpx
from rich.console import Console
from pathlib import Path

import config

console = Console()
pid = os.getpid()


class ProxyManager:
    """代理管理器 - 从 HTTP 接口加载代理并轮询使用"""

    def __init__(self, reload_interval: int = 60):
        """初始化代理管理器

        Args:
            reload_interval: 自动重载代理的间隔时间（秒），默认 60 秒
        """
        self._proxies: list[dict] = []  # [{"ip": "x.x.x.x", "port": "1234"}, ...]
        self._failed_proxies: set[int] = set()  # 失效代理的索引集合
        self._current_index: int = 0  # 当前轮询位置
        self._lock = asyncio.Lock()
        self._reload_interval = reload_interval
        self._reload_task: asyncio.Task | None = None
        self._should_stop = False

    def _parse_proxy_text(self, text: str) -> list[dict]:
        """解析文本格式代理列表"""
        proxies = []
        for item in text.replace(',', '\n').split():
            item = item.strip()
            if not item or ':' not in item:
                continue
            ip, port = item.rsplit(':', 1)
            ip = ip.strip()
            port = port.strip()
            if ip and port:
                proxies.append({"ip": ip, "port": port})
        return proxies

    def _normalize_proxy_response(self, data) -> list[dict] | None:
        """规范化 JSON 格式代理响应"""
        if isinstance(data, dict) and data.get("code") == "SUCCESS":
            proxy_list = data.get("data", {}).get("ips")
            if not isinstance(proxy_list, list):
                console.print(
                    f"[yellow][PID:{pid}] HTTP接口返回格式错误，"
                    f"data.ips 字段预期 list，实际 {type(proxy_list)}[/yellow]"
                )
                return None

            proxies = []
            for proxy in proxy_list:
                if not isinstance(proxy, dict):
                    continue
                server = str(proxy.get("server", "")).strip()
                if ":" not in server:
                    continue
                ip, port = server.rsplit(":", 1)
                ip = ip.strip()
                port = port.strip()
                if ip and port:
                    proxies.append({"ip": ip, "port": port})
            return proxies

        if isinstance(data, dict):
            if not data.get("status"):
                console.print(f"[yellow][PID:{pid}] HTTP接口返回 status=false[/yellow]")
                return None

            proxy_list = data.get("proxy")
            if not isinstance(proxy_list, list):
                console.print(
                    f"[yellow][PID:{pid}] HTTP接口返回格式错误，"
                    f"proxy 字段预期 list，实际 {type(proxy_list)}[/yellow]"
                )
                return None
            data = proxy_list

        if not isinstance(data, list):
            console.print(f"[yellow][PID:{pid}] HTTP接口返回格式错误，预期 dict 或 list，实际 {type(data)}[/yellow]")
            return None

        proxies = []
        for proxy in data:
            if not isinstance(proxy, dict):
                continue
            ip = str(proxy.get("ip", "")).strip()
            port = str(proxy.get("port", "")).strip()
            if ip and port:
                proxies.append({"ip": ip, "port": port})
        return proxies

    def _proxy_to_url(self, proxy: dict) -> str:
        """构建带认证信息的代理 URL"""
        return f"http://{config.PROXY_AUTH_USER}:{config.PROXY_AUTH_PASSWORD}@{proxy['ip']}:{proxy['port']}"

    async def load_proxies_from_http(self, max_retries: int = 3, _skip_release: bool = False) -> bool:
        """从 HTTP 接口获取代理列表（支持 429 错误重试 + 多 worker 文件共享）

        策略：
        1. 尝试从 HTTP 接口获取代理
        2. 成功后写入共享文件，供其他 worker 使用
        3. 失败时从共享文件读取（如果未过期）
        4. 遇到 NO_AVAILABLE_CHANNEL 时触发强制释放流程（_skip_release=True 时跳过，供释放流程内部复用）

        Args:
            max_retries: 遇到 429 错误时的最大重试次数
            _skip_release: True 时遇到 NO_AVAILABLE_CHANNEL 直接返回 False（不触发释放、不读共享文件），
                由 _release_and_reget 复用本方法做 get+加载

        Returns:
            成功返回 True，失败返回 False（保留旧代理池）
        """
        import random
        import time

        url = config.PROXY_HTTP_URL
        params = config.PROXY_HTTP_PARAMS

        # 🔥 第一步：尝试从 HTTP 接口获取
        for attempt in range(1, max_retries + 1):
            try:
                async with httpx.AsyncClient(timeout=15.0) as client:
                    if params:
                        response = await client.get(url, params=params)
                    else:
                        response = await client.get(url)
                    response.raise_for_status()
                    text = response.text.strip()
                    try:
                        json_data = response.json()
                    except ValueError:
                        data = self._parse_proxy_text(text)
                        if data:
                            console.print(f"[dim][PID:{pid}] HTTP接口文本响应: {text}[/dim]")
                        else:
                            console.print(f"[yellow][PID:{pid}] HTTP接口返回空代理列表或无法解析: {text}[/yellow]")
                            break
                    else:
                        console.print(f"[dim][PID:{pid}] HTTP接口 JSON 响应: {json_data}[/dim]")
                        # 共享池无可用通道 → 触发强制释放流程
                        if isinstance(json_data, dict) and json_data.get("code") == "NO_AVAILABLE_CHANNEL":
                            if _skip_release:
                                console.print(f"[yellow][PID:{pid}] NO_AVAILABLE_CHANNEL（释放流程内部 get，不重复触发释放）[/yellow]")
                                break
                            console.print(f"[yellow][PID:{pid}] NO_AVAILABLE_CHANNEL：共享池已满，触发强制释放流程...[/yellow]")
                            released = await self._release_and_reget()
                            if released:
                                return True
                            console.print(f"[yellow][PID:{pid}] 强制释放未成功，回退到共享文件[/yellow]")
                            break
                        data = self._normalize_proxy_response(json_data)

                    if not data:
                        console.print(f"[yellow][PID:{pid}] HTTP接口返回空代理列表[/yellow]")
                        break

                    # ✅ HTTP 接口成功 - 保存到内存和文件
                    old_count = len(self._proxies)
                    old_failed = len(self._failed_proxies)

                    self._proxies = data
                    self._failed_proxies.clear()
                    self._current_index = 0

                    console.print(f"[green][PID:{pid}] ✅ HTTP接口成功加载 {len(data)} 个代理 (旧: {old_count}个, {old_failed}失效)[/green]")

                    # 打印前 3 个代理用于调试
                    for i, proxy in enumerate(self._proxies[:3]):
                        console.print(f"  [cyan]- 代理 {i+1}: {proxy.get('ip', 'N/A')}:{proxy.get('port', 'N/A')}[/cyan]")

                    # 🔥 写入共享文件，供其他 worker 使用
                    await self._save_proxies_to_file(data)

                    return True

            except httpx.TimeoutException:
                console.print(f"[yellow][PID:{pid}] HTTP接口调用超时（尝试 {attempt}/{max_retries}）[/yellow]")
                if attempt < max_retries:
                    wait_time = 2 ** attempt + random.uniform(0, 2)  # 指数退避
                    console.print(f"[yellow][PID:{pid}] 等待 {wait_time:.1f}s 后重试...[/yellow]")
                    await asyncio.sleep(wait_time)
                    continue
                break  # 最后一次重试失败，跳出循环

            except httpx.HTTPStatusError as e:
                status_code = e.response.status_code

                # 特殊处理 429 错误（请求过于频繁）
                if status_code == 429:
                    console.print(f"[yellow][PID:{pid}] HTTP 429 请求过于频繁（尝试 {attempt}/{max_retries}）[/yellow]")
                    if attempt < max_retries:
                        # 指数退避：4秒、8秒、16秒 + 随机抖动
                        wait_time = (2 ** attempt) * 2 + random.uniform(1, 5)
                        console.print(f"[yellow][PID:{pid}] 等待 {wait_time:.1f}s 后重试...[/yellow]")
                        await asyncio.sleep(wait_time)
                        continue
                    else:
                        console.print(f"[yellow][PID:{pid}] 已达最大重试次数，尝试从文件读取[/yellow]")
                        break
                else:
                    console.print(f"[yellow][PID:{pid}] HTTP接口返回错误 {status_code}[/yellow]")
                    break

            except Exception as e:
                console.print(f"[yellow][PID:{pid}] HTTP接口调用失败: {e}[/yellow]")
                break

        # 🔥 第二步：HTTP 接口失败，尝试从共享文件读取（释放流程内部调用跳过，避免读到已删除的旧代理）
        if _skip_release:
            return False
        console.print(f"[cyan][PID:{pid}] HTTP接口失败，尝试从共享文件读取...[/cyan]")
        return await self._load_proxies_from_file()

    async def _save_proxies_to_file(self, proxies: list[dict]) -> bool:
        """保存代理列表到共享文件

        Args:
            proxies: 代理列表

        Returns:
            成功返回 True，失败返回 False
        """
        import time

        try:
            cache_data = {
                "timestamp": time.time(),
                "pid": pid,
                "proxies": proxies
            }

            # 写入文件（原子操作：先写临时文件，再重命名）
            cache_file = Path(config.PROXY_CACHE_FILE)
            temp_file = cache_file.with_suffix('.tmp')

            # 确保目录存在
            cache_file.parent.mkdir(parents=True, exist_ok=True)

            # 写入临时文件
            with open(temp_file, 'w', encoding='utf-8') as f:
                json.dump(cache_data, f, ensure_ascii=False, indent=2)

            # 原子替换
            temp_file.replace(cache_file)

            console.print(f"[green][PID:{pid}] 已保存 {len(proxies)} 个代理到共享文件: {cache_file}[/green]")
            return True

        except Exception as e:
            console.print(f"[yellow][PID:{pid}] 保存代理到文件失败: {e}[/yellow]")
            return False

    async def _load_proxies_from_file(self) -> bool:
        """从共享文件加载代理列表

        Returns:
            成功返回 True，失败返回 False（保留旧代理池）
        """
        import time

        try:
            cache_file = Path(config.PROXY_CACHE_FILE)

            if not cache_file.exists():
                console.print(f"[yellow][PID:{pid}] 共享文件不存在: {cache_file}[/yellow]")
                return False

            # 读取文件
            with open(cache_file, 'r', encoding='utf-8') as f:
                cache_data = json.load(f)

            timestamp = cache_data.get('timestamp', 0)
            source_pid = cache_data.get('pid', 'unknown')
            proxies = cache_data.get('proxies', [])

            # 检查文件是否过期
            age = time.time() - timestamp
            if age > config.PROXY_CACHE_MAX_AGE:
                console.print(
                    f"[yellow][PID:{pid}] 共享文件已过期: {age:.1f}s "
                    f"(> {config.PROXY_CACHE_MAX_AGE}s)，保留旧代理池[/yellow]"
                )
                return False

            # 验证代理列表
            if not isinstance(proxies, list) or not proxies:
                console.print(f"[yellow][PID:{pid}] 共享文件中代理列表无效[/yellow]")
                return False

            # 🔥 从文件加载成功
            old_count = len(self._proxies)
            old_failed = len(self._failed_proxies)

            self._proxies = proxies
            self._failed_proxies.clear()
            self._current_index = 0

            console.print(
                f"[green][PID:{pid}] ✅ 从共享文件加载 {len(proxies)} 个代理 "
                f"(来源 PID:{source_pid}, 年龄: {age:.1f}s, 旧: {old_count}个, {old_failed}失效)[/green]"
            )

            # 打印前 3 个代理用于调试
            for i, proxy in enumerate(self._proxies[:3]):
                console.print(f"  [cyan]- 代理 {i+1}: {proxy.get('ip', 'N/A')}:{proxy.get('port', 'N/A')}[/cyan]")

            return True

        except Exception as e:
            console.print(f"[yellow][PID:{pid}] 从共享文件加载代理失败: {e}[/yellow]")
            return False

    async def _query_inuse_ips(self) -> list[str]:
        """查询代理池当前在用 IP 列表（共享池，含其他客户端占用）

        解析 query 响应 data.tasks[].ips[].proxy_ip 并扁平化。

        Returns:
            在用 IP 列表（按 query 返回顺序）；查询失败返回空列表
        """
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.get(config.PROXY_QUERY_URL)
                response.raise_for_status()
                json_data = response.json()
        except Exception as e:
            console.print(f"[yellow][PID:{pid}] 查询在用 IP 失败: {e}[/yellow]")
            return []

        if not isinstance(json_data, dict) or json_data.get("code") != "SUCCESS":
            console.print(f"[yellow][PID:{pid}] 查询在用 IP 返回非 SUCCESS: {json_data}[/yellow]")
            return []

        tasks = (json_data.get("data") or {}).get("tasks") or []
        ips: list[str] = []
        for task in tasks:
            for ip_obj in task.get("ips") or []:
                ip = str(ip_obj.get("proxy_ip", "")).strip()
                if ip:
                    ips.append(ip)
        console.print(f"[dim][PID:{pid}] 查询到 {len(ips)} 个在用 IP[/dim]")
        return ips

    async def _delete_ips(self, ips: list[str]) -> bool:
        """释放指定的代理 IP（共享池强制释放，可强杀活跃租约）

        Args:
            ips: 待释放的 IP 列表

        Returns:
            成功返回 True，失败返回 False
        """
        if not ips:
            return False
        ip_param = ",".join(ips)
        url = f"{config.PROXY_DELETE_URL}&ip={ip_param}"
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.get(url)
                response.raise_for_status()
                json_data = response.json()
        except Exception as e:
            console.print(f"[yellow][PID:{pid}] 释放 IP 失败: {e}[/yellow]")
            return False

        if isinstance(json_data, dict) and json_data.get("code") == "SUCCESS":
            console.print(f"[green][PID:{pid}] ✅ 已释放 {json_data.get('data')} 个 IP: {ip_param}[/green]")
            return True
        console.print(f"[yellow][PID:{pid}] 释放 IP 返回非 SUCCESS: {json_data}[/yellow]")
        return False

    async def _release_and_reget(self) -> bool:
        """共享池无可用通道时强制释放 IP 并重新获取（多 worker 文件锁互斥）

        流程（持文件锁，fcntl.flock 进程崩溃自动释放）：
        1. 先 get 一次：若其他 worker 已补满池子，直接返回，避免无谓删除
        2. 仍 NO_AVAILABLE_CHANNEL → query 取前 N 个在用 IP → delete → 进入下一轮 get
        3. 最多 PROXY_RELEASE_MAX_ATTEMPTS 次，全败则返回 False

        文件锁获取超时（PROXY_RELEASE_LOCK_TIMEOUT）则跳过本次释放，
        避免无限阻塞所有 worker 的代理获取。

        Returns:
            成功获取新代理返回 True，全部失败返回 False
        """
        lock_path = Path(config.PROXY_RELEASE_LOCK_FILE)
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock_fd = open(lock_path, "w")
        timeout = config.PROXY_RELEASE_LOCK_TIMEOUT
        deadline = time.monotonic() + timeout

        # 非阻塞获取文件锁，超时则跳过本次释放
        while True:
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    lock_fd.close()
                    console.print(
                        f"[yellow][PID:{pid}] 释放锁被其他 worker 占用，等待 {timeout}s 超时，"
                        f"跳过本次释放[/yellow]"
                    )
                    return False
                await asyncio.sleep(0.5)

        console.print(f"[cyan][PID:{pid}] 已获取释放锁，开始强制释放流程[/cyan]")
        try:
            for attempt in range(1, config.PROXY_RELEASE_MAX_ATTEMPTS + 1):
                # 先 get：其他 worker 可能已补满池子，避免无谓删除
                got = await self.load_proxies_from_http(_skip_release=True)
                if got:
                    console.print(f"[green][PID:{pid}] 释放流程第 {attempt} 轮：get 成功，无需释放[/green]")
                    return True

                # 仍 NO_AVAILABLE_CHANNEL → query 取前 N 个 + delete
                ips = await self._query_inuse_ips()
                target = ips[: config.PROXY_RELEASE_NUM]
                if not target:
                    console.print(f"[yellow][PID:{pid}] 释放流程第 {attempt} 轮：无可释放 IP，直接重试 get[/yellow]")
                    continue
                await self._delete_ips(target)
                # 下一轮循环开头会再 get
            console.print(
                f"[red][PID:{pid}] 释放流程 {config.PROXY_RELEASE_MAX_ATTEMPTS} 次全部失败，放弃[/red]"
            )
            return False
        finally:
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
            except Exception:
                pass
            lock_fd.close()

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

    async def mark_proxy_failed(self, proxy_url: str):
        """标记代理失效

        Args:
            proxy_url: 失效的代理URL
        """
        async with self._lock:
            # 从URL反查索引（支持认证URL和裸URL两种格式）
            for idx, proxy in enumerate(self._proxies):
                url = self._proxy_to_url(proxy)
                bare_url = f"http://{proxy['ip']}:{proxy['port']}"
                if url == proxy_url or bare_url == proxy_url:
                    if idx not in self._failed_proxies:
                        self._failed_proxies.add(idx)
                        failed_rate = len(self._failed_proxies) / len(self._proxies) * 100
                        console.print(
                            f"[yellow][PID:{pid}] 代理失效 [{idx+1}/{len(self._proxies)}]: "
                            f"{proxy['ip']}:{proxy['port']} (失效率: {failed_rate:.1f}%)[/yellow]"
                        )

                        # 失效率超过 80% 告警
                        if failed_rate > 80:
                            console.print(f"[red][PID:{pid}] ⚠️  警告：代理失效率已超过 80%！[/red]")
                    break

    async def get_proxy_status(self) -> dict:
        """获取代理池状态（用于监控）"""
        async with self._lock:
            total = len(self._proxies)
            failed = len(self._failed_proxies)
            return {
                "total_proxies": total,
                "available_count": total - failed,
                "failed_count": failed,
                "failed_rate": failed / total if total > 0 else 0,
                "current_index": self._current_index,
                "sample_proxies": self._proxies[:5] if self._proxies else []
            }

    async def get_all_proxies(self) -> list[dict]:
        """获取所有代理列表（向后兼容）"""
        if not self._proxies:
            await self.load_proxies_from_http()
        return self._proxies

    async def get_all_proxy_urls(self) -> list[str]:
        """获取所有代理的 URL 列表（向后兼容）"""
        proxies = await self.get_all_proxies()
        return [self._proxy_to_url(p) for p in proxies]

    async def reload(self) -> bool:
        """手动重新加载代理列表"""
        return await self.load_proxies_from_http()

    async def _auto_reload_loop(self):
        """每60秒自动更新代理池（避免多 worker 同时请求）"""
        import random

        # 首次启动时随机延迟 0-10 秒，避免多个 worker 同时请求
        initial_delay = random.uniform(0, 10)
        console.print(f"[dim][PID:{pid}] 自动更新将在 {initial_delay:.1f}s 后首次执行[/dim]")
        await asyncio.sleep(initial_delay)

        while not self._should_stop:
            try:
                old_count = len(self._proxies)
                old_failed = len(self._failed_proxies)

                success = await self.load_proxies_from_http()

                if success:
                    new_count = len(self._proxies)
                    console.print(
                        f"[cyan][PID:{pid}] 代理池自动更新: {old_count}个 ({old_failed}失效) -> "
                        f"{new_count}个 (全部可用)[/cyan]"
                    )
                else:
                    console.print(f"[yellow][PID:{pid}] 代理池自动更新失败，将在下个周期重试[/yellow]")

                # 等待下一个周期（添加随机抖动 ±5秒）
                wait_time = self._reload_interval + random.uniform(-5, 5)
                await asyncio.sleep(wait_time)

            except asyncio.CancelledError:
                break
            except Exception as e:
                console.print(f"[red][PID:{pid}] 自动重载异常: {e}[/red]")
                # 发生异常后等待一段时间再重试
                await asyncio.sleep(30)

    async def start_auto_reload(self):
        """启动自动重载任务"""
        if self._reload_task is None or self._reload_task.done():
            self._should_stop = False
            self._reload_task = asyncio.create_task(self._auto_reload_loop())
            console.print(f"[cyan][PID:{pid}] 代理自动更新已启动，间隔: {self._reload_interval} 秒[/cyan]")

    async def stop_auto_reload(self):
        """停止自动重载任务"""
        self._should_stop = True
        if self._reload_task and not self._reload_task.done():
            self._reload_task.cancel()
            try:
                await self._reload_task
            except asyncio.CancelledError:
                pass
            console.print(f"[cyan][PID:{pid}] 代理自动更新已停止[/cyan]")

    async def close(self):
        """关闭代理管理器"""
        await self.stop_auto_reload()


# 全局代理管理器实例
proxy_manager = ProxyManager(reload_interval=config.PROXY_RELOAD_INTERVAL)
