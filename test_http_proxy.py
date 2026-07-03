"""
测试代理连接和重试逻辑 - HTTP 接口版本
"""
import asyncio
import httpx
from proxy_manager import proxy_manager


async def test_single_proxy():
    """测试单个代理连接"""
    print("=== 测试单个代理连接 ===\n")

    # 加载代理
    await proxy_manager.load_proxies_from_http()

    # 测试 URL
    test_url = "https://weibo.com/ajax/profile/info?uid=6589032261"

    proxy = await proxy_manager.get_next_proxy()
    print(f"测试代理: {proxy}")

    try:
        async with httpx.AsyncClient(proxy=proxy, timeout=10) as client:
            print(f"请求: {test_url}")
            response = await client.get(test_url)
            print(f"状态码: {response.status_code}")
            if response.status_code == 200:
                print("✓ 代理可用!")
            else:
                print(f"✗ HTTP 错误: {response.status_code}")
                await proxy_manager.mark_proxy_failed(proxy)
    except Exception as e:
        print(f"✗ 连接错误: {e}")
        await proxy_manager.mark_proxy_failed(proxy)


async def test_retry_logic():
    """测试 10 次重试逻辑"""
    print("\n\n=== 测试 10 次重试逻辑 ===\n")

    # 重新加载代理池
    await proxy_manager.reload()

    test_url = "https://m.weibo.cn/api/container/getIndex?containerid=100103type%3D61%26q%3D%E6%B5%8B%E8%AF%95%26t%3D&page_type=searchall&page=1"

    max_retries = 10
    for attempt in range(1, max_retries + 1):
        proxy = await proxy_manager.get_next_proxy()

        if proxy is None:
            print(f"[{attempt}/{max_retries}] 无可用代理")
            break

        print(f"[{attempt}/{max_retries}] 尝试代理: {proxy[:50]}...")

        try:
            async with httpx.AsyncClient(proxy=proxy, timeout=10) as client:
                response = await client.get(test_url)

                # 业务级失败检测
                if response.status_code in [432, 418, 403, 414]:
                    print(f"  ⚠️ 触发风控 (HTTP {response.status_code})")
                    await proxy_manager.mark_proxy_failed(proxy)
                    continue

                if response.status_code >= 400:
                    print(f"  ⚠️ HTTP 错误 {response.status_code}")
                    await proxy_manager.mark_proxy_failed(proxy)
                    continue

                # 成功
                print(f"  ✅ 成功 (HTTP {response.status_code})")
                break

        except httpx.TimeoutException:
            print(f"  ⏱️ 请求超时")
            await proxy_manager.mark_proxy_failed(proxy)
        except Exception as e:
            print(f"  ❌ 连接失败: {str(e)[:50]}")
            await proxy_manager.mark_proxy_failed(proxy)

    # 显示最终状态
    print("\n=== 最终代理池状态 ===")
    status = await proxy_manager.get_proxy_status()
    print(f"总代理数: {status['total_proxies']}")
    print(f"可用代理数: {status['available_count']}")
    print(f"失效代理数: {status['failed_count']}")
    print(f"失效率: {status['failed_rate']*100:.2f}%")


async def main():
    try:
        # 测试单个代理
        await test_single_proxy()

        # 测试重试逻辑
        await test_retry_logic()

    finally:
        await proxy_manager.close()
        print("\n✅ 测试完成!")


if __name__ == "__main__":
    asyncio.run(main())
