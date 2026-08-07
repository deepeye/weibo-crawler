# Weibo Crawler

微博采集服务：通过浏览器自动化 + HTTP 客户端采集微博搜索、用户微博与用户资料，核心约束是降低微博风控触发概率。

## Language

### Weibo API surfaces

项目通过三个**不同的请求家族**与微博通信，认证模型互不相同。混用它们是鉴权困惑的根源。

**MAPI**:
微博原生 App API，位于 `api.weibo.cn/2/...`。用 `gsid` 会话令牌鉴权，每请求带签名 `s`/`i` 对，并通过 `did`/`aid`/`ua` 标识调用设备。即微博 Android/iOS 客户端所用的接口。
_Avoid_: app API、open API、mobile API（与 H5-API 歧义）

**H5-API**:
移动端网页 API，位于 `m.weibo.cn/api/...`。Cookie 鉴权，服务于 H5 站点。本爬虫的搜索走此路。
_Avoid_: m 站 API、mobile API（与 MAPI 歧义）

**Ajax-API**:
桌面端网页 API，位于 `weibo.com/ajax/...`。Cookie 鉴权，服务于桌面站点。本爬虫的用户微博列表与用户资料走此路。
_Avoid_: PC API、web API

### MAPI auth terms

**gsid**:
MAPI 的会话令牌（`_2A...` 形态）。证明调用方账号/设备已登录。绑定账号与设备，会过期。
_Avoid_: token、session（过于泛化）

**s/i signature**:
MAPI 的每请求签名 `s`（MAC）及其伴随标识 `i`，由请求参数与源自 `gsid` 的密钥计算得出。无签名密钥无法伪造，且受重放保护。
_Avoid_: sign、签名（与本仓库语境的 open-platform 签名混淆）
