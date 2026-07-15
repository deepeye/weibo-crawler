# Task 1: Configure the New Proxy Provider

## Files
- Modify: `config.py:72-90`

## Global Constraints
- Do not restructure `proxy_manager.py`; make a surgical provider-format change.
- Keep old JSON/list proxy response support as a compatibility fallback.
- Add fixed credentials to `config.py` as `PROXY_AUTH_USER = "5CDBEC47"` and `PROXY_AUTH_PASSWORD = "48BC8939D827"`.
- Set `PROXY_HTTP_URL` to `https://exclusive.proxy.qg.net/replace?key=5CDBEC47&num=1&area=&isp=0&format=txt&seq=\r\n&distinct=false&keep_alive=1440` and `PROXY_HTTP_PARAMS = {}`.
- Do not change database, browser lifecycle, API endpoint, or Cookie behavior.

## Interfaces
- Consumes: existing config constants `PROXY_HTTP_URL` and `PROXY_HTTP_PARAMS`.
- Produces:
  - `PROXY_HTTP_URL: str`
  - `PROXY_HTTP_PARAMS: dict`
  - `PROXY_AUTH_USER: str`
  - `PROXY_AUTH_PASSWORD: str`

## Steps

1. Update proxy config constants.

Replace the current HTTP proxy interface block in `config.py` with this exact block:

```python
# HTTP 代理接口配置
PROXY_HTTP_URL = "https://exclusive.proxy.qg.net/replace?key=5CDBEC47&num=1&area=&isp=0&format=txt&seq=\r\n&distinct=false&keep_alive=1440"
PROXY_HTTP_PARAMS = {}
PROXY_AUTH_USER = "5CDBEC47"
PROXY_AUTH_PASSWORD = "48BC8939D827"
```

Leave `PROXY_RELOAD_INTERVAL`, `PROXY_TIMEOUT_THRESHOLD`, and cache config unchanged.

2. Run syntax check:

```bash
uv run python -m py_compile config.py
```

Expected: command exits 0 with no output.
