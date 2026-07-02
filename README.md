# 启动服务
nohup xvfb-run -a uv run python api.py --workers 4 > output.log 2>&1 &

# weibo-crawler
## 启动命令

cd weibo-crawler
# 之前（无头模式，容易被检测）
uv run python api.py --workers 2

# 现在（虚拟显示器 + 有头模式，更接近真实浏览器）
xvfb-run -a uv run python api.py --workers 2

### 或者如果要用无头模式，修改 config.py
HEADLESS = True
USE_PERSISTENT_CONTEXT = False


# 重启服务（默认）
./restart_api.sh

# 或指定命令
./restart_api.sh start    # 启动服务
./restart_api.sh stop     # 停止服务
./restart_api.sh restart  # 重启服务
./restart_api.sh status   # 查看状态
./restart_api.sh logs     # 查看实时日志
./restart_api.sh help     # 帮助信息
