import requests
# 如果下面目标站不可用，请使用test.ipw.cn、ip.sb、ipinfo.io、ip-api.com、64.ipcheck.ing
targetURL = "https://ipinfo.io"
proxyAddr = "118.120.221.243:18341"
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