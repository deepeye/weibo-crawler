import asyncio
from playwright.async_api import async_playwright
import json
import random


class WeiboSearchAutomation:
    def __init__(self):
        self.browser = None
        self.context = None
        self.page = None
        
    async def init_browser(self, headless=False):
        """初始化浏览器"""
        playwright = await async_playwright().start()
        
        # 启动浏览器，使用 chromium
        self.browser = await playwright.chromium.launch(
            headless=headless,
            args=[
                '--disable-blink-features=AutomationControlled',
                '--disable-dev-shm-usage',
                '--no-sandbox'
            ]
        )
        
        # 创建浏览器上下文，模拟真实用户环境
        self.context = await self.browser.new_context(
            viewport={'width': 390, 'height': 844},  # iPhone 12 Pro 尺寸
            user_agent='Mozilla/5.0 (iPhone; CPU iPhone OS 14_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/14.0 Mobile/15E148 Safari/604.1',
            locale='zh-CN',
            timezone_id='Asia/Shanghai',
            geolocation={'longitude': 121.4737, 'latitude': 31.2304},  # 上海
            permissions=['geolocation'],
            device_scale_factor=3,
            is_mobile=True,
            has_touch=True
        )
        
        # 注入脚本，隐藏 webdriver 特征
        await self.context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {
                get: () => undefined
            });
            
            // 模拟真实的 Chrome 对象
            window.chrome = {
                runtime: {}
            };
            
            // 覆盖 permissions
            const originalQuery = window.navigator.permissions.query;
            window.navigator.permissions.query = (parameters) => (
                parameters.name === 'notifications' ?
                    Promise.resolve({ state: Notification.permission }) :
                    originalQuery(parameters)
            );
        """)
        
        self.page = await self.context.new_page()
        print("浏览器初始化完成")
        
    async def human_like_delay(self, min_ms=500, max_ms=1500):
        """模拟人类操作延迟"""
        delay = random.uniform(min_ms, max_ms) / 1000
        await asyncio.sleep(delay)
        
    async def move_mouse_naturally(self, element):
        """自然地移动鼠标到元素"""
        box = await element.bounding_box()
        if box:
            # 随机偏移，模拟真实用户不会精确点击中心点
            x = box['x'] + box['width'] * random.uniform(0.3, 0.7)
            y = box['y'] + box['height'] * random.uniform(0.3, 0.7)
            await self.page.mouse.move(x, y)
            await self.human_like_delay(100, 300)
    
    async def solve_slide_captcha(self):
        """解决滑块验证码"""
        print("检测到滑块验证码，开始处理...")
        
        try:
            # 等待滑块出现
            slider = await self.page.wait_for_selector(
                '.geetest_slider_button', 
                timeout=10000
            )
            
            if slider:
                print("找到滑块元素")
                
                # 获取滑块和滑轨信息
                slider_box = await slider.bounding_box()
                
                # 获取滑轨容器
                track = await self.page.query_selector('.geetest_slider')
                track_box = await track.bounding_box()
                
                if slider_box and track_box:
                    # 计算需要滑动的距离
                    distance = track_box['width'] - slider_box['width'] - 10
                    print(f"需要滑动距离: {distance}px")
                    
                    # 模拟人类滑动：先加速，后减速，带随机抖动
                    await self.human_slide(slider, distance)
                    
                    # 等待验证结果
                    await asyncio.sleep(2)
                    
                    # 检查是否验证成功
                    success = await self.page.query_selector('.geetest_success')
                    if success:
                        print("✓ 滑块验证成功！")
                        return True
                    else:
                        print("✗ 滑块验证失败，可能需要重试")
                        return False
                        
        except Exception as e:
            print(f"处理滑块验证码时出错: {e}")
            return False
    
    async def human_slide(self, element, distance):
        """模拟人类滑动行为"""
        box = await element.bounding_box()
        start_x = box['x'] + box['width'] / 2
        start_y = box['y'] + box['height'] / 2
        
        # 移动到滑块起始位置
        await self.page.mouse.move(start_x, start_y)
        await self.human_like_delay(200, 400)
        
        # 按下鼠标
        await self.page.mouse.down()
        await self.human_like_delay(100, 200)
        
        # 分段滑动，模拟人类行为
        steps = random.randint(20, 30)
        current_x = start_x
        
        for i in range(steps):
            # 使用缓动函数（前快后慢）
            progress = i / steps
            easing = 1 - (1 - progress) ** 3  # cubic ease-out
            
            # 添加随机抖动
            jitter = random.uniform(-2, 2)
            
            # 计算新位置
            new_x = start_x + (distance * easing) + jitter
            new_y = start_y + random.uniform(-2, 2)  # Y轴也加点抖动
            
            await self.page.mouse.move(new_x, new_y)
            
            # 随机延迟，模拟人类不均匀的移动速度
            delay = random.uniform(10, 30)
            await asyncio.sleep(delay / 1000)
            
            current_x = new_x
        
        # 到达终点后稍微停顿
        await self.human_like_delay(100, 300)
        
        # 释放鼠标
        await self.page.mouse.up()
        print("滑动完成")
    
    async def search_weibo(self, keyword="tesla"):
        """搜索微博内容"""
        try:
            # 访问微博移动版
            print(f"正在访问微博移动版...")
            await self.page.goto('https://m.weibo.cn/', wait_until='networkidle')
            await self.human_like_delay(1000, 2000)
            
            # 查找并点击搜索框
            print("查找搜索框...")
            search_input = await self.page.wait_for_selector(
                'input[type="search"], .weibo-search-input, input[placeholder*="搜索"]',
                timeout=10000
            )
            
            # 点击搜索框
            await self.move_mouse_naturally(search_input)
            await search_input.click()
            await self.human_like_delay(500, 1000)
            
            # 输入搜索关键词（模拟真实打字速度）
            print(f"输入搜索关键词: {keyword}")
            for char in keyword:
                await search_input.type(char)
                await asyncio.sleep(random.uniform(0.1, 0.3))
            
            await self.human_like_delay(500, 1000)
            
            # 按回车或点击搜索按钮
            await self.page.keyboard.press('Enter')
            print("提交搜索...")
            
            # 等待页面加载
            await asyncio.sleep(3)
            
            # 检查是否出现验证码
            captcha_exists = await self.page.query_selector('.geetest_holder, .geetest_slider')
            
            if captcha_exists:
                print("检测到验证码...")
                success = await self.solve_slide_captcha()
                
                if success:
                    print("验证码通过，等待搜索结果...")
                    await asyncio.sleep(2)
                else:
                    print("验证码未通过，可能需要手动处理")
                    # 等待用户手动完成
                    await asyncio.sleep(10)
            
            # 等待搜索结果加载
            await self.page.wait_for_load_state('networkidle')
            
            # 截图保存
            await self.page.screenshot(path='weibo_search_result.png', full_page=True)
            print("已保存搜索结果截图: weibo_search_result.png")
            
            # 获取页面内容
            content = await self.page.content()
            
            # 尝试提取微博卡片
            cards = await self.page.query_selector_all('.card-wrap, .weibo-top, article')
            print(f"\n找到 {len(cards)} 条微博")
            
            # 提取前几条微博的文本
            for i, card in enumerate(cards[:5], 1):
                try:
                    text_content = await card.inner_text()
                    print(f"\n--- 微博 {i} ---")
                    print(text_content[:200] + "..." if len(text_content) > 200 else text_content)
                except:
                    pass
            
            # 拦截并记录 API 请求
            await self.capture_api_requests()
            
            return True
            
        except Exception as e:
            print(f"搜索过程出错: {e}")
            await self.page.screenshot(path='error_screenshot.png')
            print("已保存错误截图: error_screenshot.png")
            return False
    
    async def capture_api_requests(self):
        """捕获和记录 API 请求"""
        print("\n监听 API 请求...")
        
        # 设置请求拦截
        async def handle_route(route):
            request = route.request
            
            # 记录微博 API 请求
            if 'api/container/getIndex' in request.url or 'gcaptcha4.geetest.com' in request.url:
                print(f"\n捕获到请求:")
                print(f"URL: {request.url}")
                print(f"Method: {request.method}")
                print(f"Headers: {json.dumps(dict(request.headers), indent=2, ensure_ascii=False)}")
                
                # 继续请求并获取响应
                response = await route.fetch()
                body = await response.text()
                
                print(f"Response Status: {response.status}")
                print(f"Response Body: {body[:500]}...")
                
                # 保存到文件
                with open('api_requests.log', 'a', encoding='utf-8') as f:
                    f.write(f"\n{'='*60}\n")
                    f.write(f"URL: {request.url}\n")
                    f.write(f"Method: {request.method}\n")
                    f.write(f"Headers: {json.dumps(dict(request.headers), indent=2, ensure_ascii=False)}\n")
                    f.write(f"Response: {body}\n")
            
            await route.continue_()
        
        await self.page.route("**/*", handle_route)
        await asyncio.sleep(5)  # 监听5秒
    
    async def close(self):
        """关闭浏览器"""
        if self.browser:
            await self.browser.close()
            print("浏览器已关闭")


async def main():
    """主函数"""
    automation = WeiboSearchAutomation()
    
    try:
        # 初始化浏览器（headless=False 可以看到浏览器操作）
        await automation.init_browser(headless=False)
        
        # 执行微博搜索
        await automation.search_weibo(keyword="tesla")
        
        # 保持浏览器打开一段时间，方便查看结果
        print("\n浏览器将在 30 秒后关闭...")
        await asyncio.sleep(30)
        
    except Exception as e:
        print(f"执行过程中出错: {e}")
        
    finally:
        await automation.close()


if __name__ == "__main__":
    print("=" * 60)
    print("微博搜索自动化工具 - 使用 Playwright")
    print("=" * 60)
    print("\n准备启动浏览器...\n")
    
    asyncio.run(main())