import requests
# 如果下面目标站不可用，请使用test.ipw.cn、ip.sb、ipinfo.io、ip-api.com、64.ipcheck.ing
targetURL = "https://www.nfra.gov.cn/cbircweb/DocInfo/SelectDocByItemIdAndChild?itemId=4291&pageSize=18&pageIndex=1"
proxyAddr = "27.150.127.185:13408"
authKey = "5CDBEC47"
password = "48BC8939D827"
# 账密模式
proxyUrl = "http://%(user)s:%(password)s@%(server)s" % {
    "user": authKey,
    "password": password,
    "server": proxyAddr,
}
proxies = {
    "http": proxyUrl,
    "https": proxyUrl,
}
resp = requests.get(targetURL, proxies=proxies)
print(resp.text)