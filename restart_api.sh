#!/bin/bash
# ============================================
# 微博爬虫 API 服务重启脚本
# ============================================

set -e  # 遇到错误时退出

# ==================== 配置项 ====================
APP_NAME="weibo-api"
APP_DIR="/Users/felixwang/devspace/pdmi-crawler/weibo-crawler"
API_SCRIPT="api.py"

# 使用 uv 管理
UV_CMD="uv"
XVFB_CMD="xvfb-run"
XVFB_ARGS="-a"

# API 服务配置
HOST="0.0.0.0"
PORT="8002"
WORKERS="1"

# PID 文件位置
PID_FILE="$APP_DIR/$APP_NAME.pid"
LOG_DIR="$APP_DIR/logs"
LOG_FILE="$LOG_DIR/api_$(date +%Y%m%d_%H%M%S).log"

# ==================== 颜色输出 ====================
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# ==================== 函数定义 ====================

log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

log_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# 查找所有相关进程
find_all_processes() {
    # 查找所有相关的进程：
    # 1. 占用端口的进程
    # 2. api.py 相关进程
    # 3. multiprocessing.spawn (worker 进程)
    # 4. xvfb-run 和 uv 进程
    {
        # 方法1：通过端口查找（最可靠）
        if command -v lsof &> /dev/null; then
            lsof -ti:$PORT 2>/dev/null
        fi

        # 方法2：通过 api.py 查找
        pgrep -f "$API_SCRIPT" 2>/dev/null

        # 方法3：通过 multiprocessing.spawn 查找（worker 进程）
        pgrep -f "multiprocessing.spawn" 2>/dev/null

        # 方法4：通过 uv run 查找
        pgrep -f "uv run.*$API_SCRIPT" 2>/dev/null

        # 方法5：通过 xvfb-run 查找
        pgrep -f "xvfb-run.*$API_SCRIPT" 2>/dev/null
    } | sort -u | grep -v '^$'
}

# 检查进程是否运行
is_running() {
    # 检查是否有任何相关进程在运行
    PIDS=$(find_all_processes)
    [ -n "$PIDS" ]
}

# 停止服务
stop_service() {
    log_info "正在停止 $APP_NAME 服务..."

    # 首先尝试使用 PID 文件
    if [ -f "$PID_FILE" ]; then
        PID=$(cat "$PID_FILE")
        if ps -p "$PID" > /dev/null 2>&1; then
            log_info "找到 PID 文件记录的进程 (PID: $PID)"
            kill "$PID" 2>/dev/null || true
        fi
        rm -f "$PID_FILE"
    fi

    # 查找所有相关进程（处理多 worker 情况）
    PIDS=$(find_all_processes)

    if [ -n "$PIDS" ]; then
        log_info "找到运行中的进程: $PIDS"
        log_info "正在停止所有相关进程..."

        # 优雅停止所有进程
        for pid in $PIDS; do
            kill "$pid" 2>/dev/null || true
        done

        # 等待进程结束（最多 30 秒）
        for i in {1..30}; do
            REMAINING=$(find_all_processes)
            if [ -z "$REMAINING" ]; then
                log_success "所有服务进程已停止"
                return 0
            fi
            sleep 1
        done

        # 如果优雅停止失败，强制杀死所有进程
        log_warning "优雅停止失败，强制终止所有进程..."
        for pid in $(find_all_processes); do
            kill -9 "$pid" 2>/dev/null || true
        done
        log_success "所有服务进程已强制停止"
    else
        log_warning "未找到运行中的服务进程"
    fi

    # 额外检查：清理可能占用端口的进程
    if command -v lsof &> /dev/null; then
        PORT_PID=$(lsof -ti:$PORT 2>/dev/null)
        if [ -n "$PORT_PID" ]; then
            log_warning "发现占用端口 $PORT 的进程: $PORT_PID"
            kill -9 $PORT_PID 2>/dev/null || true
        fi
    fi
}

# 启动服务
start_service() {
    log_info "正在启动 $APP_NAME 服务..."

    # 检查是否已在运行
    if is_running; then
        log_warning "服务已在运行中 (PID: $(cat $PID_FILE))"
        return 1
    fi

    # 创建日志目录
    mkdir -p "$LOG_DIR"

    # 进入项目目录
    cd "$APP_DIR" || {
        log_error "无法进入目录: $APP_DIR"
        exit 1
    }

    # 检查 uv 是否可用
    if ! command -v uv &> /dev/null; then
        log_error "uv 未安装，请先安装: curl -LsSf https://astral.sh/uv/install.sh | sh"
        exit 1
    fi

    # 检查 xvfb-run 是否可用（仅 Linux 需要）
    USE_XVFB=0
    PLATFORM=$(uname -s)
    if [ "$PLATFORM" = "Linux" ] && command -v xvfb-run &> /dev/null; then
        USE_XVFB=1
        log_info "使用 xvfb-run 运行（虚拟显示）"
    elif [ "$PLATFORM" = "Darwin" ]; then
        log_info "macOS 环境，直接运行（无头模式）"
    else
        log_warning "Linux 环境但未找到 xvfb-run，将直接运行（可能导致 Playwright 在无头模式下失败）"
    fi

    # 构建启动命令
    if [ $USE_XVFB -eq 1 ]; then
        START_CMD="$XVFB_CMD $XVFB_ARGS $UV_CMD run python $API_SCRIPT --host $HOST --port $PORT --workers $WORKERS"
    else
        START_CMD="$UV_CMD run python $API_SCRIPT --host $HOST --port $PORT --workers $WORKERS"
    fi

    # 启动服务
    log_info "启动命令: $START_CMD"
    log_info "日志文件: $LOG_FILE"

    nohup bash -c "$START_CMD" \
        >> "$LOG_FILE" 2>&1 &

    # 保存 PID
    MAIN_PID=$!
    echo $MAIN_PID > "$PID_FILE"
    log_info "主进程 PID: $MAIN_PID"

    # 等待服务启动（先等主进程启动）
    sleep 3

    # 检查主进程是否还在运行
    if ! ps -p "$MAIN_PID" > /dev/null 2>&1; then
        log_error "主进程启动后立即退出，请查看日志: $LOG_FILE"
        return 1
    fi

    # 等待子进程创建
    sleep 5

    # 验证服务是否启动成功（检查主进程或端口）
    if ps -p "$MAIN_PID" > /dev/null 2>&1; then
        log_success "服务启动成功! (主进程 PID: $MAIN_PID)"
        log_info "访问地址: http://$HOST:$PORT"
        log_info "日志文件: $LOG_FILE"

        # 显示找到的子进程数量
        CHILD_PIDS=$(find_all_processes | grep -v "^$MAIN_PID$" | grep -v '^$' | wc -l)
        if [ "$CHILD_PIDS" -gt 0 ]; then
            log_info "找到 $CHILD_PIDS 个子进程"
        fi

        return 0
    else
        log_error "服务启动失败，主进程已退出，请查看日志: $LOG_FILE"
        return 1
    fi
}

# 重启服务
restart_service() {
    log_info "=========================================="
    log_info "重启 $APP_NAME 服务"
    log_info "=========================================="

    stop_service
    sleep 6
    start_service
}

# 查看服务状态
status_service() {
    PIDS=$(find_all_processes)

    if [ -n "$PIDS" ]; then
        log_success "服务正在运行"
        echo "  端口: $PORT"
        echo "  地址: http://$HOST:$PORT"
        echo ""
        echo "  进程列表:"
        local total_mem=0
        for pid in $PIDS; do
            echo "    - PID: $pid"

            # 显示内存使用情况
            if command -v ps &> /dev/null; then
                MEM_KB=$(ps -o rss= -p "$pid" 2>/dev/null | tr -d ' ')
                if [ -n "$MEM_KB" ]; then
                    MEM_MB=$(echo "$MEM_KB 1024" | awk '{printf "%.2f", $1/$2}')
                    echo "      内存: ${MEM_MB} MB"
                    total_mem=$(echo "$total_mem $MEM_KB" | awk '{print $1 + $2}')
                fi
            fi

            # 显示运行时间
            if command -v ps &> /dev/null; then
                ELAPSED=$(ps -o etime= -p "$pid" 2>/dev/null | tr -d ' ')
                if [ -n "$ELAPSED" ]; then
                    echo "      运行时间: $ELAPSED"
                fi
            fi
        done

        # 显示总内存
        if [ "$total_mem" -gt 0 ]; then
            TOTAL_MEM_MB=$(echo "$total_mem 1024" | awk '{printf "%.2f", $1/$2}')
            echo ""
            echo "  总内存: ${TOTAL_MEM_MB} MB"
        fi

        return 0
    else
        log_warning "服务未运行"
        return 1
    fi
}

# 查看日志
view_logs() {
    if [ -f "$LOG_FILE" ]; then
        tail -f "$LOG_FILE"
    else
        # 查找最新的日志文件
        LATEST_LOG=$(ls -t "$LOG_DIR"/api_*.log 2>/dev/null | head -1)
        if [ -n "$LATEST_LOG" ]; then
            log_info "查看日志: $LATEST_LOG"
            tail -f "$LATEST_LOG"
        else
            log_error "未找到日志文件"
            exit 1
        fi
    fi
}

# 显示帮助信息
show_help() {
    echo "=========================================="
    echo "微博爬虫 API 服务管理脚本"
    echo "=========================================="
    echo ""
    echo "用法: $0 [命令]"
    echo ""
    echo "命令:"
    echo "  start    - 启动服务"
    echo "  stop     - 停止服务"
    echo "  restart  - 重启服务 (默认)"
    echo "  status   - 查看服务状态"
    echo "  logs     - 查看实时日志"
    echo "  help     - 显示此帮助信息"
    echo ""
    echo "示例:"
    echo "  $0              # 重启服务"
    echo "  $0 start        # 启动服务"
    echo "  $0 stop         # 停止服务"
    echo "  $0 status       # 查看状态"
    echo "  $0 logs         # 查看日志"
    echo ""
}

# ==================== 主程序 ====================

# 没有参数时默认重启
COMMAND=${1:-restart}

case "$COMMAND" in
    start)
        start_service
        ;;
    stop)
        stop_service
        ;;
    restart)
        restart_service
        ;;
    status)
        status_service
        ;;
    logs)
        view_logs
        ;;
    help|--help|-h)
        show_help
        ;;
    *)
        log_error "未知命令: $COMMAND"
        echo ""
        show_help
        exit 1
        ;;
esac
