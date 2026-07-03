"""
代理管理器 - 从 HTTP 接口获取代理列表并实现轮询（支持多 worker 共享）
"""

import os
import json
import asyncio
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

    async def load_proxies_from_http(self, max_retries: int = 3) -> bool:
        """从 HTTP 接口获取代理列表（支持 429 错误重试 + 多 worker 文件共享）

        策略：
        1. 尝试从 HTTP 接口获取代理
        2. 成功后写入共享文件，供其他 worker 使用
        3. 失败时从共享文件读取（如果未过期）

        Args:
            max_retries: 遇到 429 错误时的最大重试次数

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

        # 🔥 第二步：HTTP 接口失败，尝试从共享文件读取
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

    async def get_next_proxy(self) -> str | None:
        """获取下一个可用代理

        自动跳过失效代理，循环轮询

        Returns:
            代理URL (http://ip:port)，无可用代理返回 None
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

            # 所有代理都失效
            failed_rate = len(self._failed_proxies) / len(self._proxies) * 100
            console.print(f"[red][PID:{pid}] 代理池耗尽：所有 {len(self._proxies)} 个IP都已失效 ({failed_rate:.1f}%)[/red]")
            return None

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
