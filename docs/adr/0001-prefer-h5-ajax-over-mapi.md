# Prefer cookie-based H5/Ajax APIs over the native MAPI

Date: 2026-08-07
Status: accepted

## Context

Weibo exposes three request families (see `CONTEXT.md`): **H5-API** (`m.weibo.cn/api/...`) and **Ajax-API** (`weibo.com/ajax/...`), both cookie-authenticated; and **MAPI** (`api.weibo.cn/2/...`), the native app API authenticated with `gsid` + a per-request signed `s`/`i` pair. MAPI carries richer app-only data (e.g. `statuses/extend` full long-text) and was tempting to adopt.

## Decision

The crawler uses only the cookie-based H5-API and Ajax-API. It does **not** call MAPI.

## Why

MAPI requires reproducing the *full app request context* on every request: the app's `User-Agent` and specific headers, a matching egress IP, and a fresh `s`/`i` signature. A captured MAPI URL replayed as a bare HTTP GET is rejected silently by Weibo's Shanhai/Sora gateway - returning HTTP 200 with body `{}` (no `error_code`, no login redirect) - *before* signature or credential validation. This was confirmed empirically: a captured `statuses/extend` request that rendered correctly in-app returned `{}` when replayed without the app's headers, from both the user's environment and a separate non-app IP. Adopting MAPI would mean reverse-engineering and maintaining the signing scheme, header/IP fidelity, and `gsid` rotation - a continuous operational burden. Cookie-based H5/Ajax avoids all of it at the cost of some app-only fields.

## Consequences

- A bare-URL replay of any captured `api.weibo.cn` request will return `{}`; this is expected, not a credential failure. Don't debug `gsid`/`s` first - check request context.
- If app-only data (full long-text via `extend`, app-only profile fields) becomes a hard requirement, reopening this decision means building the app-context replay path; that cost is the reason this ADR exists.
