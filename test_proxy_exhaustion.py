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
