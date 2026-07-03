"""
测试增强型浏览器请求功能
"""
import asyncio
import httpx


async def test_browser_fetch():
    """测试浏览器方式请求和监控"""
    print("=" * 60)
    print("测试浏览器增强型请求功能")
    print("=" * 60)
    
    # 1. 获取当前指纹和监控状态
    async with httpx.AsyncClient() as client:
        response = await client.get("http://localhost:8000/fingerprint")
        if response.status_code == 200:
            data = response.json()
            print("\n【当前状态】")
            print(f"进程 PID: {data['pid']}")
            print(f"总请求数: {data['stats']['total_requests']}")
            
            if 'enhanced_monitor' in data:
                monitor = data['enhanced_monitor']
                print("\n【增强监控】")
                print(f"  HTTP 总请求: {monitor['total_http_requests']}")
                print(f"  成功: {monitor['success_count']}, 被封: {monitor['blocked_count']}, 失败: {monitor['error_count']}")
                print(f"  成功率: {monitor['success_rate']}")
                print(f"  被封率: {monitor['blocked_rate']}")
                print(f"  平均响应时间: {monitor['average_response_time']}")
                print(f"  需要降速: {monitor['should_slow_down']}")
            
            if 'rate_limiter' in data:
                limiter = data['rate_limiter']
                print("\n【速率限制器】")
                print(f"  当前令牌: {limiter['current_tokens']}/{limiter['capacity']}")
                print(f"  恢复速率: {limiter['refill_rate']} 令牌/秒")
                print(f"  状态: {limiter['status']}")
        else:
            print(f"获取指纹失败: {response.status_code}")
    
    # 2. 测试用户微博接口（使用浏览器方式）
    print("\n" + "=" * 60)
    print("测试用户微博接口（浏览器方式）")
    print("=" * 60)
    
    test_uid = "1191965271"  # 测试 UID
    
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(
                "http://localhost:8000/user/weibo",
                params={
                    "uid": test_uid,
                    "page": 1
                }
            )
            print(f"\n请求结果: HTTP {response.status_code}")
            
            if response.status_code == 200:
                data = response.json()
                if data.get("ok") == 1:
                    print("  ✅ 请求成功")
                    result_data = data.get("data", {})
                    list_data = result_data.get("list", [])
                    print(f"  获取到 {len(list_data)} 条微博")
                else:
                    print(f"  ⚠️  API 返回错误: {data.get('error')}")
            else:
                print(f"  ❌ 请求失败")
    except httpx.TimeoutException:
        print(f"\n请求: ⏱️  超时")
    except Exception as e:
        print(f"\n请求: ❌ 错误 - {e}")
    
    # 3. 测试用户资料接口（使用浏览器方式）
    print("\n" + "=" * 60)
    print("测试用户资料接口（浏览器方式）")
    print("=" * 60)
    
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(
                "http://localhost:8000/user/profile",
                params={
                    "uid": test_uid
                }
            )
            print(f"\n请求结果: HTTP {response.status_code}")
            
            if response.status_code == 200:
                data = response.json()
                if data.get("ok") == 1:
                    print("  ✅ 请求成功")
                    result_data = data.get("data", {})
                    user_info = result_data.get("user", {})
                    print(f"  用户昵称: {user_info.get('screen_name')}")
                    print(f"  粉丝数: {user_info.get('followers_count')}")
                else:
                    print(f"  ⚠️  API 返回错误: {data.get('error')}")
            else:
                print(f"  ❌ 请求失败")
    except httpx.TimeoutException:
        print(f"\n请求: ⏱️  超时")
    except Exception as e:
        print(f"\n请求: ❌ 错误 - {e}")
    
    # 4. 再次获取状态
    print("\n" + "=" * 60)
    print("请求后状态")
    print("=" * 60)
    
    async with httpx.AsyncClient() as client:
        response = await client.get("http://localhost:8000/fingerprint")
        if response.status_code == 200:
            data = response.json()
            
            if 'enhanced_monitor' in data:
                monitor = data['enhanced_monitor']
                print("\n【更新后的监控数据】")
                print(f"  HTTP 总请求: {monitor['total_http_requests']}")
                print(f"  成功: {monitor['success_count']}, 被封: {monitor['blocked_count']}, 失败: {monitor['error_count']}")
                print(f"  成功率: {monitor['success_rate']}")
                print(f"  被封率: {monitor['blocked_rate']}")
                print(f"  平均响应时间: {monitor['average_response_time']}")
                print(f"  需要降速: {monitor['should_slow_down']}")
            
            if 'rate_limiter' in data:
                limiter = data['rate_limiter']
                print("\n【速率限制器】")
                print(f"  当前令牌: {limiter['current_tokens']}/{limiter['capacity']}")
                print(f"  恢复速率: {limiter['refill_rate']} 令牌/秒")
                print(f"  状态: {limiter['status']}")
    
    print("\n" + "=" * 60)
    print("✅ 测试完成！")
    print("\n说明：")
    print("- 所有请求都使用 Playwright 浏览器方式")
    print("- 完全模拟真实浏览器环境")
    print("- 自动处理 Cookie、指纹、TLS 等")
    print("- 显著降低被风控的概率")
    print("=" * 60)


if __name__ == "__main__":
    print("\n⚠️  请确保 API 服务已启动: uv run python api.py --workers 1\n")
    try:
        asyncio.run(test_browser_fetch())
    except KeyboardInterrupt:
        print("\n\n测试中断")
    except Exception as e:
        print(f"\n\n测试失败: {e}")
