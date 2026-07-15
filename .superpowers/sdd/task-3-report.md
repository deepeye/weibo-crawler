# Task 3 Verification Report: Proxy Loading End-to-End

**Status:** DONE_WITH_CONCERNS

**One-line test summary:** Proxy loading, round-robin, authentication, and failover all work correctly after two surgical fixes: removing the `seq=\r\n` parameter from `PROXY_HTTP_URL` and guarding against empty `params={}` in httpx which stripped existing query parameters.

---

## Step 1: Compile Check

**Command:**
```
uv run python -m py_compile config.py proxy_manager.py test_proxy.py test_http_proxy.py
```

**Result:** Exit 0, no output. All four files compile cleanly.

---

## Step 2: test_proxy.py

**Command:**
```
uv run python test_proxy.py
```

**Results:**

| Check | Expected | Actual | Pass |
|-------|----------|--------|------|
| Prints "测试代理管理器（HTTP 接口版）" | Yes | Yes | PASS |
| "测试从 HTTP 接口加载代理" prints "结果: 成功" | Yes | Yes | PASS |
| Proxy URLs start with `http://5CDBEC47:48BC8939D827@` | Yes | Yes (all 10) | PASS |
| "代理池状态" shows total > 0 | Yes | 1 (provider returns 1 IP) | PASS |
| Manual reload succeeds | Yes | Yes ("结果: 成功") | PASS |
| After marking all as failed, gets None | Expected | None (pool exhausted) | PASS |
| After reload, pool resets | Expected | available_count: 1, failed: 0 | PASS |

**Observed proxy URL format:** `http://5CDBEC47:48BC8939D827@49.87.0.200:18528`
All returned proxy URLs are authenticated. The `_proxy_to_url()` method correctly embeds `PROXY_AUTH_USER:PROXY_AUTH_PASSWORD` in the URL.

---

## Step 3: test_http_proxy.py

**Command:**
```
uv run python test_http_proxy.py
```

**Results:**

| Check | Expected | Actual | Pass |
|-------|----------|--------|------|
| Prints "测试单个代理连接" | Yes | Yes | PASS |
| "测试代理:" line starts with `http://5CDBEC47:48BC8939D827@` | Yes | Yes | PASS |
| Does not crash on failure | Yes | 403 logged, proxy marked failed, no crash | PASS |
| Retry logic recovers | Expected | HTTP 302 (redirect) counted as success, pool healthy | PASS |

**Detailed observations:**
- Single proxy test: Weibo returned HTTP 403 via proxy (expected -- Weibo blocks proxy traffic), proxy was correctly marked failed.
- 10-retry test: Re-loaded fresh proxy, request to m.weibo.cn returned HTTP 302 (redirect), which counted as success. Final pool: 1 total, 1 available, 0 failed.

---

## Step 4: API Endpoints (manual)

**Commands:**
```
curl http://localhost:8000/proxy/reload
curl http://localhost:8000/proxy/status
```

**Result:** Both returned exit code 7 (connection refused). The FastAPI server is not running. This is expected/non-blocking per the brief's "if API is running" qualification.

---

## Fixes Applied (2 surgical changes)

### Fix 1: `config.py` line 77 -- Remove invalid `seq=\r\n` parameter

**Before:**
```python
PROXY_HTTP_URL = "https://exclusive.proxy.qg.net/replace?key=5CDBEC47&num=1&area=&isp=0&format=txt&seq=\r\n&distinct=false&keep_alive=1440"
```

**After:**
```python
PROXY_HTTP_URL = "https://exclusive.proxy.qg.net/replace?key=5CDBEC47&num=1&area=&isp=0&format=txt&distinct=false&keep_alive=1440"
```

**Reason:** The literal `\r` character in the Python string is an invalid non-printable ASCII character in a URL, causing httpx to raise `Invalid non-printable ASCII character in URL, '\r' at position 85`. URL-encoding to `%0D%0A` also failed (HTTP 400 from provider). The provider returns valid proxy IPs without the `seq` parameter.

### Fix 2: `proxy_manager.py` line 106-109 -- Guard against empty params dict

**Before:**
```python
response = await client.get(url, params=params)
```

**After:**
```python
if params:
    response = await client.get(url, params=params)
else:
    response = await client.get(url)
```

**Reason:** When `params={}` is passed to httpx, it **strips all existing query parameters from the URL**. The URL `...?key=...&num=1&...` became just `.../replace`, causing HTTP 400 from the provider. Passing no `params` argument preserves the original query string.

---

## Concerns

1. **Provider returns only 1 IP at a time**: The `num=1` parameter in `PROXY_HTTP_URL` means the proxy pool always has exactly 1 IP. If that IP goes bad, the entire pool is exhausted immediately. The failover and invalid-marking logic works correctly for this case, but higher throughput scenarios may want `num=2` or more.

2. **HTTP 403 from Weibo through proxy**: The single-proxy connectivity test received HTTP 403 from Weibo, which is expected (Weibo actively blocks datacenter proxy IPs). The retry logic successfully recovered by reloading and retrying, and the second proxy received HTTP 302 (redirect) from m.weibo.cn, suggesting the API endpoint may require cookie-based auth rather than proxy-based access alone.

3. **The `seq` parameter in the original brief's URL was unworkable**: The brief specified `seq=\r\n` which cannot work as a Python string literal in a URL. The fix (removing it entirely) works because the provider's `format=txt` mode returns clean `ip:port` lines without needing a separator parameter.

4. **API not running**: Could not verify the `/proxy/reload` and `/proxy/status` API endpoints end-to-end. The underlying `ProxyManager.reload()` and `ProxyManager.get_proxy_status()` methods were verified via test_proxy.py.
