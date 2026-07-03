# Proxy Text Auth URL Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the proxy provider with the new plain-text `ip:port` endpoint and return authenticated proxy URLs in the form `http://user:password@ip:port`.

**Architecture:** Keep the existing `ProxyManager` lifecycle, cache file, round-robin selection, failed-proxy tracking, and status shape. Normalize provider responses into the existing internal proxy record shape `{"ip": str, "port": str}`; only URL construction changes to include auth credentials from `config.py`.

**Tech Stack:** Python 3.10+, `uv`, `httpx`, existing script-style tests.

## Global Constraints

- Do not restructure `proxy_manager.py`; make a surgical provider-format change.
- Keep old JSON/list proxy response support as a compatibility fallback.
- Add fixed credentials to `config.py` as `PROXY_AUTH_USER = "5CDBEC47"` and `PROXY_AUTH_PASSWORD = "48BC8939D827"`.
- Set `PROXY_HTTP_URL` to `https://exclusive.proxy.qg.net/replace?key=5CDBEC47&num=1&area=&isp=0&format=txt&seq=\r\n&distinct=false&keep_alive=1440` and `PROXY_HTTP_PARAMS = {}`.
- Do not change database, browser lifecycle, API endpoint, or Cookie behavior.

---

## File Structure

- Modify `config.py`: update proxy provider URL, clear provider params, add auth credential constants next to proxy config.
- Modify `proxy_manager.py`: add small helper methods for parsing text responses, normalizing old JSON responses, and constructing authenticated URLs. Update all URL-return/comparison paths to use the helper.
- No new production files.
- No pytest suite exists; verification uses syntax compile and the existing script tests.

---

### Task 1: Configure the New Proxy Provider

**Files:**
- Modify: `config.py:72-90`

**Interfaces:**
- Consumes: existing config constants `PROXY_HTTP_URL` and `PROXY_HTTP_PARAMS`.
- Produces:
  - `PROXY_HTTP_URL: str`
  - `PROXY_HTTP_PARAMS: dict`
  - `PROXY_AUTH_USER: str`
  - `PROXY_AUTH_PASSWORD: str`

- [ ] **Step 1: Update proxy config constants**

Replace the current HTTP proxy interface block in `config.py` with this exact block:

```python
# HTTP 代理接口配置
PROXY_HTTP_URL = "https://exclusive.proxy.qg.net/replace?key=5CDBEC47&num=1&area=&isp=0&format=txt&seq=\r\n&distinct=false&keep_alive=1440"
PROXY_HTTP_PARAMS = {}
PROXY_AUTH_USER = "5CDBEC47"
PROXY_AUTH_PASSWORD = "48BC8939D827"
```

Leave `PROXY_RELOAD_INTERVAL`, `PROXY_TIMEOUT_THRESHOLD`, and cache config unchanged.

- [ ] **Step 2: Run syntax check**

Run:

```bash
uv run python -m py_compile config.py
```

Expected: command exits 0 with no output.

---

### Task 2: Parse Plain-Text Proxies and Build Authenticated URLs

**Files:**
- Modify: `proxy_manager.py:18-338`

**Interfaces:**
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

- [ ] **Step 1: Add helper methods inside `ProxyManager` after `__init__`**

Add this code after the `__init__` method:

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

- [ ] **Step 2: Replace response parsing in `load_proxies_from_http()`**

Inside `load_proxies_from_http()`, replace this block:

```python
                    data = response.json()
                    console.print(f"[dim][PID:{pid}] HTTP接口响应: {data}[/dim]")

                    # 解析实际返回格式：{"status": true, "proxy": [...]}
                    if isinstance(data, dict):
                        # 检查 status 字段
                        if not data.get("status"):
                            console.print(f"[yellow][PID:{pid}] HTTP接口返回 status=false[/yellow]")
                            break  # 跳出重试循环，尝试从文件读取

                        # 提取 proxy 字段
                        proxy_list = data.get("proxy")
                        if not isinstance(proxy_list, list):
                            console.print(
                                f"[yellow][PID:{pid}] HTTP接口返回格式错误，"
                                f"proxy 字段预期 list，实际 {type(proxy_list)}[/yellow]"
                            )
                            break

                        data = proxy_list  # 使用提取的代理列表

                    elif isinstance(data, list):
                        # 直接是列表格式（向后兼容）
                        pass
                    else:
                        console.print(f"[yellow][PID:{pid}] HTTP接口返回格式错误，预期 dict 或 list，实际 {type(data)}[/yellow]")
                        break

                    # 验证代理列表不为空
                    if not data:
                        console.print(f"[yellow][PID:{pid}] HTTP接口返回空代理列表[/yellow]")
                        break
```

with this exact block:

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

- [ ] **Step 3: Update proxy URL construction paths**

In `get_next_proxy()`, replace:

```python
                proxy = self._proxies[idx]
                return f"http://{proxy['ip']}:{proxy['port']}"
```

with:

```python
                proxy = self._proxies[idx]
                return self._proxy_to_url(proxy)
```

In `mark_proxy_failed()`, replace:

```python
                url = f"http://{proxy['ip']}:{proxy['port']}"
                if url == proxy_url:
```

with:

```python
                url = self._proxy_to_url(proxy)
                if url == proxy_url:
```

In `get_all_proxy_urls()`, replace:

```python
        return [f"http://{p['ip']}:{p['port']}" for p in proxies]
```

with:

```python
        return [self._proxy_to_url(p) for p in proxies]
```

- [ ] **Step 4: Run syntax check**

Run:

```bash
uv run python -m py_compile proxy_manager.py
```

Expected: command exits 0 with no output.

---

### Task 3: Verify Proxy Loading End-to-End

**Files:**
- Test: `test_proxy.py`
- Test: `test_http_proxy.py`

**Interfaces:**
- Consumes:
  - `ProxyManager.load_proxies_from_http() -> bool`
  - `ProxyManager.get_next_proxy() -> str | None`
  - `ProxyManager.mark_proxy_failed(proxy_url: str)`
  - `ProxyManager.get_proxy_status() -> dict`
- Produces: verified runtime behavior with authenticated proxy URLs.

- [ ] **Step 1: Compile all touched and related scripts**

Run:

```bash
uv run python -m py_compile config.py proxy_manager.py test_proxy.py test_http_proxy.py
```

Expected: command exits 0 with no output.

- [ ] **Step 2: Run proxy manager script test**

Run:

```bash
uv run python test_proxy.py
```

Expected:

- The script prints `测试代理管理器（HTTP 接口版）`.
- `测试从 HTTP 接口加载代理` prints `结果: 成功` if the provider is reachable.
- The proxy values printed by `测试轮询获取代理` start with `http://5CDBEC47:48BC8939D827@`.
- `代理池状态` shows `总代理数` greater than 0 when the provider is reachable.

If the provider is temporarily unreachable, the script may fall back to `/tmp/weibo_proxy_cache.json`; confirm any printed proxy URLs are still authenticated.

- [ ] **Step 3: Run HTTP proxy connectivity script**

Run:

```bash
uv run python test_http_proxy.py
```

Expected:

- The script prints `测试单个代理连接`.
- The `测试代理:` line starts with `http://5CDBEC47:48BC8939D827@`.
- Either the test target returns HTTP 200, or failures are logged and the proxy is marked failed without crashing.

- [ ] **Step 4: Manual status check if API is running**

If the API service is already running, reload proxies and inspect status:

```bash
curl http://localhost:8000/proxy/reload
curl http://localhost:8000/proxy/status
```

Expected: reload returns a successful response, and status sample proxies remain internal dicts containing `ip` and `port`; API consumers still receive authenticated URLs from `get_proxy()` internally.

---

## Self-Review

- Spec coverage: The plan updates `PROXY_HTTP_URL`, adds fixed auth constants, parses plain-text `ip:port`, builds authenticated proxy URLs, preserves old JSON/list compatibility, and verifies with existing scripts.
- Placeholder scan: No TBD/TODO placeholders remain.
- Type consistency: New helper method names and return types match all consuming steps.
