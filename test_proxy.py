"""
测试代理管理器 - HTTP 接口版本
"""
import asyncio
from proxy_manager import proxy_manager, ProxyManager


def test_normalize_qg_json_response():
    manager = ProxyManager()
    data = {
        "code": "SUCCESS",
        "data": {
            "task_id": "yqjke3qf8yaegjqO",
            "ips": [
                {
                    "proxy_ip": "171.213.204.198",
                    "server": "171.213.204.198:15403",
                    "area_code": 510100,
                    "area": "四川省成都市",
                    "isp": "电信",
                    "deadline": "2026-07-03 18:28:52",
                },
                {
                    "proxy_ip": "180.126.50.4",
                    "server": "180.126.50.4:12733",
                    "area_code": 320900,
                    "area": "江苏省盐城市",
                    "isp": "电信",
                    "deadline": "2026-07-03 18:28:52",
                },
            ],
            "num": 2,
        },
        "request_id": "dc67cf8d-7b36-4985-aa4b-f14af268dd03",
    }

    assert manager._normalize_proxy_response(data) == [
        {"ip": "171.213.204.198", "port": "15403"},
        {"ip": "180.126.50.4", "port": "12733"},
    ]


async def main():
    print("=== 测试代理管理器（HTTP 接口版）===\n")

    # 测试从 HTTP 接口加载代理
    print("1. 测试从 HTTP 接口加载代理...")
    success = await proxy_manager.load_proxies_from_http()
    print(f"   结果: {'成功' if success else '失败'}\n")

    # 测试轮询获取代理
    print("2. 测试轮询获取代理（10 次）...")
    for i in range(10):
        proxy = await proxy_manager.get_next_proxy()
        print(f"   第 {i+1} 次: {proxy}")

    # 测试失效标记
    print("\n3. 测试失效标记...")
    proxy = await proxy_manager.get_next_proxy()
    if proxy:
        print(f"   获取代理: {proxy}")
        await proxy_manager.mark_proxy_failed(proxy)
        print(f"   已标记失效")

    # 获取代理状态
    print("\n4. 代理池状态...")
    status = await proxy_manager.get_proxy_status()
    print(f"   总代理数: {status['total_proxies']}")
    print(f"   可用代理数: {status['available_count']}")
    print(f"   失效代理数: {status['failed_count']}")
    print(f"   失效率: {status['failed_rate']*100:.2f}%")
    print(f"   当前索引: {status['current_index']}")
    print(f"   前 5 个代理: {status['sample_proxies']}")

    # 测试跳过失效代理
    print("\n5. 测试跳过失效代理（再获取 5 次）...")
    for i in range(5):
        proxy = await proxy_manager.get_next_proxy()
        print(f"   第 {i+1} 次: {proxy}")

    # 测试手动重载
    print("\n6. 测试手动重载代理池...")
    success = await proxy_manager.reload()
    print(f"   结果: {'成功' if success else '失败'}")

    # 重载后检查状态
    status = await proxy_manager.get_proxy_status()
    print(f"   重载后可用代理数: {status['available_count']}")
    print(f"   重载后失效代理数: {status['failed_count']}")

    # 关闭连接
    await proxy_manager.close()
    print("\n✅ 测试完成!")


if __name__ == "__main__":
    asyncio.run(main())
