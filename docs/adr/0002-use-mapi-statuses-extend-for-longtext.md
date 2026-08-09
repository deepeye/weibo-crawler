# Use MAPI statuses/extend for /weibo/longtext (supersedes ADR-0001)

Date: 2026-08-08
Status: accepted
Supersedes: ADR-0001

## Context

ADR-0001 avoided MAPI based on a misdiagnosis: a captured `statuses/extend` URL replayed as a bare GET returned `{}`, which was read as "the Shanhai/Sora gateway silently rejects context-less replays." On direct testing with a valid captured signature, that was wrong - MAPI `api.weibo.cn/2/statuses/extend` works. The fixed captured `s`/`i`/`gsid` are accepted across different `(uid, mid)` pairs (the signature is NOT param-bound over uid/mid), and `{}` is a genuine "this mid has no extended data" response, not a rejection.

## Decision

Use MAPI `statuses/extend` for `GET /weibo/longtext?uid=<uid>&mid=<mid>`: construct the URL with `uid`->`<requestuid>`, `mid`->`<requestmid>`, and the signature/device params (`s`/`i`/`gsid`/`did`/`aid`/`ua`/`from`) from `config.MAPI_*`. Transport is `httpx` (system resolver) - MAPI needs no TLS impersonation, proxy, or cookie, and `httpx` avoids the `curl-cffi` DNS failure observed on macOS under a fake-ip VPN.

## Why

MAPI returns the full extended status (richer than the H5 longtext-only path), the captured signature is reusable across mids (not param-bound), and no reverse-engineering or app capture-replay is needed. The earlier H5 detail-page approach (`m.weibo.cn/detail/{mid}`, commit `66e018b`) was a detour caused by the misdiagnosis and is superseded.

## Consequences

- `{}` from `/weibo/longtext` means the mid has no extended data - not a failure. Don't debug the signature first.
- `config.MAPI_*` holds captured values. The signature is not param-bound, so one capture covers many mids; but if Weibo rotates `s`/`i`/`gsid`, all requests fail and they must be re-captured.
- ADR-0001 is superseded; its "gateway rejection" premise was incorrect.
