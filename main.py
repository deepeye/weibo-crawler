"""
微博搜索爬虫 - 基于 Playwright
自动绕过极验无感验证
"""

import json
import csv
import time
import re
from pathlib import Path
from urllib.parse import quote
from datetime import datetime

from playwright.sync_api import sync_playwright, Page, BrowserContext
from rich.console import Console
from rich.table import Table
from rich.progress import Progress, SpinnerColumn, TextColumn

import config

console = Console()


class WeiboCrawler:
    """微博搜索爬虫"""

    def __init__(self):
        self.results = []
        self.output_dir = Path(config.OUTPUT_DIR)
        self.output_dir.mkdir(exist_ok=True)

    def create_context(self, playwright) -> BrowserContext:
        """创建浏览器上下文"""
        browser = playwright.chromium.launch(
            headless=config.HEADLESS,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
            ]
        )

        context = browser.new_context(
            user_agent=config.USER_AGENT,
            viewport={"width": 1280, "height": 800},
            locale="zh-CN",
        )

        # 注入脚本绕过 webdriver 检测
        context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {
                get: () => undefined
            });
        """)

        # 添加 cookie（如果配置了）
        if config.COOKIES:
            context.add_cookies(config.COOKIES)

        return context

    def build_search_url(self, keyword: str, page: int = 1) -> str:
        """构建搜索 URL"""
        encoded_keyword = quote(keyword)
        container_id = f"100103type=61&q={encoded_keyword}&t="
        encoded_container_id = quote(container_id, safe='')
        return f"https://m.weibo.cn/api/container/getIndex?containerid={encoded_container_id}&page_type=searchall&page={page}"

    def parse_weibo_item(self, item: dict) -> dict | None:
        """解析单条微博数据"""
        try:
            card = item.get("card_type")
            if card != 9:  # 只处理微博卡片
                return None

            mblog = item.get("mblog", {})
            user = mblog.get("user", {})

            # 清理 HTML 标签
            text = mblog.get("text", "")
            clean_text = re.sub(r'<[^>]+>', '', text)

            return {
                "id": mblog.get("id"),
                "mid": mblog.get("mid"),
                "created_at": mblog.get("created_at"),
                "text": clean_text,
                "source": mblog.get("source", ""),
                "reposts_count": mblog.get("reposts_count", 0),
                "comments_count": mblog.get("comments_count", 0),
                "attitudes_count": mblog.get("attitudes_count", 0),
                "user_id": user.get("id"),
                "user_name": user.get("screen_name"),
                "user_followers": user.get("followers_count", 0),
                "user_verified": user.get("verified", False),
                "pics": [pic.get("url") for pic in mblog.get("pics", [])],
            }
        except Exception as e:
            console.print(f"[yellow]解析微博数据失败: {e}[/yellow]")
            return None

    def fetch_search_results(self, page: Page, keyword: str, page_num: int) -> list:
        """获取搜索结果"""
        url = self.build_search_url(keyword, page_num)
        
        # 使用 page.evaluate 发起请求，这样会自动带上验证参数
        try:
            response = page.evaluate(f"""
                async () => {{
                    const response = await fetch("{url}", {{
                        method: "GET",
                        headers: {{
                            "Accept": "application/json, text/plain, */*",
                            "X-Requested-With": "XMLHttpRequest",
                            "MWeibo-Pwa": "1"
                        }},
                        credentials: "include"
                    }});
                    return await response.json();
                }}
            """)

            if response.get("ok") == 1:
                data = response.get("data", {})
                cards = data.get("cards", [])
                
                results = []
                for card in cards:
                    # 处理 card_group 类型
                    if card.get("card_type") == 11:
                        for sub_card in card.get("card_group", []):
                            parsed = self.parse_weibo_item(sub_card)
                            if parsed:
                                results.append(parsed)
                    else:
                        parsed = self.parse_weibo_item(card)
                        if parsed:
                            results.append(parsed)
                
                return results
            else:
                console.print(f"[red]API 返回错误: {response.get('msg', '未知错误')}[/red]")
                return []

        except Exception as e:
            console.print(f"[red]请求失败: {e}[/red]")
            return []

    def search(self, keyword: str, max_pages: int = 5) -> list:
        """执行搜索"""
        all_results = []

        with sync_playwright() as p:
            context = self.create_context(p)
            page = context.new_page()

            # 先访问微博首页，让 JS 加载并初始化
            console.print("[cyan]正在初始化浏览器...[/cyan]")
            page.goto("https://m.weibo.cn", wait_until="networkidle")
            time.sleep(2)

            # 访问搜索页面，触发极验初始化
            search_url = f"https://m.weibo.cn/search?containerid=100103type%3D1%26q%3D{quote(keyword)}"
            page.goto(search_url, wait_until="networkidle")
            time.sleep(2)

            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                console=console
            ) as progress:
                task = progress.add_task(f"搜索: {keyword}", total=max_pages)

                for page_num in range(1, max_pages + 1):
                    progress.update(task, description=f"搜索: {keyword} (第 {page_num}/{max_pages} 页)")
                    
                    results = self.fetch_search_results(page, keyword, page_num)
                    
                    if not results:
                        console.print(f"[yellow]第 {page_num} 页无数据，停止翻页[/yellow]")
                        break

                    all_results.extend(results)
                    console.print(f"[green]第 {page_num} 页获取到 {len(results)} 条微博[/green]")

                    progress.advance(task)
                    
                    # 请求间隔
                    if page_num < max_pages:
                        time.sleep(config.REQUEST_DELAY)

            context.close()

        return all_results

    def save_results(self, results: list, keyword: str):
        """保存结果"""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_keyword = re.sub(r'[^\w\u4e00-\u9fff]', '_', keyword)

        if config.OUTPUT_FORMAT == "json":
            filename = self.output_dir / f"{safe_keyword}_{timestamp}.json"
            with open(filename, "w", encoding="utf-8") as f:
                json.dump(results, f, ensure_ascii=False, indent=2)
        else:
            filename = self.output_dir / f"{safe_keyword}_{timestamp}.csv"
            if results:
                with open(filename, "w", encoding="utf-8", newline="") as f:
                    writer = csv.DictWriter(f, fieldnames=results[0].keys())
                    writer.writeheader()
                    writer.writerows(results)

        console.print(f"[green]结果已保存至: {filename}[/green]")
        return filename

    def display_results(self, results: list, limit: int = 10):
        """在控制台显示结果"""
        table = Table(title=f"搜索结果 (显示前 {min(limit, len(results))} 条)")

        table.add_column("用户", style="cyan", width=15)
        table.add_column("内容", style="white", width=50)
        table.add_column("转发", justify="right", style="green")
        table.add_column("评论", justify="right", style="yellow")
        table.add_column("点赞", justify="right", style="red")
        table.add_column("时间", style="dim")

        for item in results[:limit]:
            text = item["text"][:47] + "..." if len(item["text"]) > 50 else item["text"]
            table.add_row(
                item["user_name"] or "未知",
                text,
                str(item["reposts_count"]),
                str(item["comments_count"]),
                str(item["attitudes_count"]),
                item["created_at"] or "",
            )

        console.print(table)


def main():
    """主函数"""
    console.print("[bold blue]微博搜索爬虫[/bold blue]")
    console.print("-" * 40)

    crawler = WeiboCrawler()

    for keyword in config.KEYWORDS:
        console.print(f"\n[bold]开始搜索: {keyword}[/bold]")
        
        results = crawler.search(keyword, max_pages=config.MAX_PAGES)
        
        if results:
            console.print(f"\n[green]共获取 {len(results)} 条微博[/green]")
            crawler.display_results(results)
            crawler.save_results(results, keyword)
        else:
            console.print(f"[yellow]未获取到任何结果[/yellow]")

    console.print("\n[bold green]爬取完成![/bold green]")


if __name__ == "__main__":
    main()
