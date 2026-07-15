# Task 3: Verify Proxy Loading End-to-End

## Files
- Test: `test_proxy.py`
- Test: `test_http_proxy.py`

## Global Constraints
- Do not restructure `proxy_manager.py`; make a surgical provider-format change.
- Keep old JSON/list proxy response support as a compatibility fallback.
- Add fixed credentials to `config.py` as `PROXY_AUTH_USER = "5CDBEC47"` and `PROXY_AUTH_PASSWORD = "48BC8939D827"`.
- Set `PROXY_HTTP_URL` to `https://exclusive.proxy.qg.net/replace?key=5CDBEC47&num=1&area=&isp=0&format=txt&seq=\r\n&distinct=false&keep_alive=1440` and `PROXY_HTTP_PARAMS = {}`.
- Do not change database, browser lifecycle, API endpoint, or Cookie behavior.

## Interfaces
- Consumes:
  - `ProxyManager.load_proxies_from_http() -> bool`
  - `ProxyManager.get_next_proxy() -> str | None`
  - `ProxyManager.mark_proxy_failed(proxy_url: str)`
  - `ProxyManager.get_proxy_status() -> dict`
- Produces: verified runtime behavior with authenticated proxy URLs.

## Steps

1. Compile all touched and related scripts:

```bash
uv run python -m py_compile config.py proxy_manager.py test_proxy.py test_http_proxy.py
```

Expected: command exits 0 with no output.

2. Run proxy manager script test:

```bash
uv run python test_proxy.py
```

Expected:
- The script prints `测试代理管理器（HTTP 接口版）`.
- `测试从 HTTP 接口加载代理` prints `结果: 成功` if the provider is reachable.
- The proxy values printed by `测试轮询获取代理` start with `http://5CDBEC47:48BC8939D827@`.
- `代理池状态` shows `总代理数` greater than 0 when the provider is reachable.

If the provider is temporarily unreachable, the script may fall back to `/tmp/weibo_proxy_cache.json`; confirm any printed proxy URLs are still authenticated.

3. Run HTTP proxy connectivity script:

```bash
uv run python test_http_proxy.py
```

Expected:
- The script prints `测试单个代理连接`.
- The `测试代理:` line starts with `http://5CDBEC47:48BC8939D827@`.
- Either the test target returns HTTP 200, or failures are logged and the proxy is marked failed without crashing.

4. Manual status check if API is running:

```bash
curl http://localhost:8000/proxy/reload
curl http://localhost:8000/proxy/status
```

Expected: reload returns a successful response, and status sample proxies remain internal dicts containing `ip` and `port`; API consumers still receive authenticated URLs from `get_proxy()` internally.
