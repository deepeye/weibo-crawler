# Task 3 Review: Proxy Loading End-to-End

## Spec Verdict: ✅

The implementation achieves all functional goals from the brief. The one deviation from the literal spec text (removing `seq=\r\n` from `PROXY_HTTP_URL`) is necessary because the specified value is structurally impossible in a URL — it is a spec defect, not an implementation defect.

## Quality Verdict: approved

Both fixes are minimal, surgical, and correct. Verification evidence covers every testable expectation.

---

## Detailed Analysis

### Requirement Coverage

| # | Requirement from brief | Status |
|---|------------------------|--------|
| 1 | Compile all four files (exit 0) | ✅ |
| 2 | test_proxy.py prints header, loads proxies, shows authenticated URLs | ✅ All 7 checks pass |
| 3 | test_http_proxy.py shows auth URLs, handles failure gracefully | ✅ All 4 checks pass |
| 4 | API endpoints `/proxy/reload` and `/proxy/status` | ⏭️ Server not running (non-blocking per brief qualification) |
| 5 | Proxy URLs start with `http://5CDBEC47:48BC8939D827@` | ✅ Confirmed in both test scripts |
| 6 | Pool exhaustion and reload work correctly | ✅ mark-then-exhaust, then reload-then-reset |
| 7 | Round-robin cycling works | ✅ 10 consecutive `get_next_proxy` calls return valid URLs |

### Fix 1: Remove `seq=\r\n` from PROXY_HTTP_URL

**Spec Deviation Analysis**

The user's plan explicitly specified:
```
PROXY_HTTP_URL = "...&format=txt&seq=\r\n&distinct=false..."
```

The report found that the literal `\r` character (ASCII 0x0D) violates httpx's URL validation: `Invalid non-printable ASCII character in URL, '\r' at position 85`. Two remediation paths were explored:

1. **URL-encode to `%0D%0A`**: This is the RFC 3986-compliant way to encode `\r\n` in a query string. However, the qg.net provider returned HTTP 400 when receiving `seq=%0D%0A` — the server does not accept this format.

2. **Remove the `seq` parameter entirely**: The provider's `format=txt` mode returns clean `ip:port` lines by default, separated by newlines. The text parser (`_parse_proxy_text`) handles this correctly. All proxy loading works without the `seq` parameter.

This is an **acceptable implementation adjustment**, not a blocker, because:
- The spec requirement is physically impossible as written (invalid URL character)
- The URL-encoded alternative was tested and rejected by the provider (HTTP 400)
- The system functions correctly without the parameter
- No alternative implementation could satisfy the literal spec

**Recommendation**: Inform the user of the deviation. If the provider documentation specifies `seq=\r\n` as a required parameter for some behavior (e.g., controlling the response line separator for multi-IP responses when `num>1`), the user should verify with the provider whether the separator behavior matters for their use case.

### Fix 2: Guard against empty `params={}` in httpx

**Reason**: `httpx.AsyncClient.get(url, params={})` strips all existing query parameters from `url` — the URL `...?key=5CDBEC47&num=1&...` becomes `.../replace` (bare path). This caused HTTP 400.

**The fix** (proxy_manager.py lines 107-110):
```python
if params:
    response = await client.get(url, params=params)
else:
    response = await client.get(url)
```

This is exactly right. When `params` is falsy (empty dict `{}`), the URL is used as-is with all its embedded query parameters preserved. The fix is 3 lines, no new helper, no restructuring.

**Side note**: This is a well-known httpx behavior — `params={}` triggers `MergeDict` logic that replaces the URL's query string with nothing. The fix is the idiomatic workaround.

---

## Verification Evidence Assessment

### Sufficiency

All verification steps from the brief were performed:

1. **Compile check**: Exit 0 for all four files. **Sufficient.**
2. **test_proxy.py**: 7 explicit checks documented with actual outputs matching expectations. Proxy URLs confirmed to start with `http://5CDBEC47:48BC8939D827@`. **Sufficient.**
3. **test_http_proxy.py**: 4 checks documented. HTTP 403 from Weibo handled gracefully (proxy marked failed, no crash). Retry logic recovered successfully (HTTP 302). **Sufficient.**
4. **API endpoints**: Server not running. The underlying methods (`ProxyManager.reload()` and `ProxyManager.get_proxy_status()`) were verified indirectly via test_proxy.py. **Acceptable** — the brief explicitly qualifies this as "if API is running."

### Gaps

None. The only untested step is explicitly conditional in the brief.

---

## Cross-File Consistency

- `config.py` line 77: `PROXY_HTTP_URL` — matches the post-fix value (no `seq=\r\n`)
- `config.py` line 78: `PROXY_HTTP_PARAMS = {}` — matches brief
- `config.py` lines 79-80: `PROXY_AUTH_USER` / `PROXY_AUTH_PASSWORD` — match brief
- `proxy_manager.py` lines 107-110: Consumes `PROXY_HTTP_PARAMS` correctly with the empty-dict guard
- `proxy_manager.py` line 81: `_proxy_to_url` reads from `config.PROXY_AUTH_USER` and `config.PROXY_AUTH_PASSWORD`
- `test_proxy.py` and `test_http_proxy.py`: No hardcoded credentials — both import from `proxy_manager` and `config`
- No other files reference `PROXY_HTTP_URL` or `PROXY_HTTP_PARAMS`

---

## Concerns from the Report (Re-evaluated)

1. **Provider returns only 1 IP**: The `num=1` parameter is set in the spec by the user. The single-IP pool is by design, not a defect. The failover logic (exhaust → reload → retry) works correctly for this case.

2. **HTTP 403 from Weibo through proxy**: Expected. Weibo blocks datacenter proxy IPs. The retry and failover logic handles this correctly. Not a defect.

3. **`seq` parameter unworkable**: Addressed in Fix 1 analysis above. Necessary deviation.

4. **API not running**: Non-blocking. Underlying methods verified through test_proxy.py.

---

## Findings

### Critical

None.

### Important

1. **`seq=\r\n` removal is a spec deviation requiring user awareness.** See Fix 1 analysis for full details. The URL as specified in the brief is physically impossible (invalid non-printable ASCII character). The URL-encoded alternative was rejected by the provider. The system works correctly without the parameter. The user should confirm this is acceptable and, if the provider documentation ascribes specific behavior to `seq`, verify behavior under multi-IP responses.

### Minor / Informational

- The report mentions "URL-encoding to `%0D%0A` also failed (HTTP 400 from provider)" but this test was done during verification and is not reflected in the final code. The final code simply removes the parameter. No action needed.
