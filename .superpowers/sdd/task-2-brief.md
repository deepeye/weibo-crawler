# Task 2: Parse Plain-Text Proxies and Build Authenticated URLs

## Files
- Modify: `proxy_manager.py:18-338`

## Global Constraints
- Do not restructure `proxy_manager.py`; make a surgical provider-format change.
- Keep old JSON/list proxy response support as a compatibility fallback.
- Add fixed credentials to `config.py` as `PROXY_AUTH_USER = "5CDBEC47"` and `PROXY_AUTH_PASSWORD = "48BC8939D827"`.
- Set `PROXY_HTTP_URL` to `https://exclusive.proxy.qg.net/replace?key=5CDBEC47&num=1&area=&isp=0&format=txt&seq=\r\n&distinct=false&keep_alive=1440` and `PROXY_HTTP_PARAMS = {}`.
- Do not change database, browser lifecycle, API endpoint, or Cookie behavior.

## Interfaces
- Consumes:
  - `config.PROXY_AUTH_USER: str`
  - `config.PROXY_AUTH_PASSWORD: str`
  - plain text response body containing one or more tokens like `118.120.221.243:18341`, separated by whitespace, `\r\n`, or commas.
  - old JSON response bodies in either `{"status": true, "proxy": [...]}` or `[...]` shape.
- Produces:
  - `ProxyManager._parse_proxy_text(text: str) -> list[dict]`
  - `ProxyManager._normalize_proxy_response(data) -> list[dict] | None`
  - `ProxyManager._proxy_to_url(proxy: dict) -> str`
  - `get_next_proxy() -> str | None` returning `http://5CDBEC47:48BC8939D827@ip:port`.
  - `get_all_proxy_urls() -> list[str]` returning authenticated URLs.

## Steps

1. Add helper methods inside `ProxyManager` after `__init__`:

```python
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
        """规范化旧 JSON 格式代理响应"""
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
```

2. In `load_proxies_from_http()`, replace the `response.json()` and JSON-only parsing block with:

```python
                    text = response.text.strip()
                    data = self._parse_proxy_text(text)

                    if data:
                        console.print(f"[dim][PID:{pid}] HTTP接口文本响应: {text}[/dim]")
                    else:
                        try:
                            json_data = response.json()
                        except ValueError:
                            console.print(f"[yellow][PID:{pid}] HTTP接口返回空代理列表或无法解析: {text}[/yellow]")
                            break

                        console.print(f"[dim][PID:{pid}] HTTP接口 JSON 响应: {json_data}[/dim]")
                        data = self._normalize_proxy_response(json_data)

                    if not data:
                        console.print(f"[yellow][PID:{pid}] HTTP接口返回空代理列表[/yellow]")
                        break
```

3. Update proxy URL construction paths:

In `get_next_proxy()`, replace `return f"http://{proxy['ip']}:{proxy['port']}"` with `return self._proxy_to_url(proxy)`.

In `mark_proxy_failed()`, replace `url = f"http://{proxy['ip']}:{proxy['port']}"` with `url = self._proxy_to_url(proxy)`.

In `get_all_proxy_urls()`, replace `return [f"http://{p['ip']}:{p['port']}" for p in proxies]` with `return [self._proxy_to_url(p) for p in proxies]`.

4. Run syntax check:

```bash
uv run python -m py_compile proxy_manager.py
```

Expected: command exits 0 with no output.
