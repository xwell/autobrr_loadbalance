#!/bin/bash

# qBittorrent 负载均衡器 Docker 启动脚本

set -e

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# 打印带颜色的消息
print_message() {
    echo -e "${2}${1}${NC}"
}

# 检查Docker是否安装
check_docker() {
    if ! command -v docker &> /dev/null; then
        print_message "Docker 未安装！请先安装 Docker。" $RED
        exit 1
    fi
    
    if ! command -v docker-compose &> /dev/null; then
        print_message "Docker Compose 未安装！请先安装 Docker Compose。" $RED
        exit 1
    fi
}

# 创建必要的目录
create_directories() {
    print_message "创建必要的目录..." $BLUE
    mkdir -p torrents logs
    chmod 755 torrents logs
}

# 检查配置文件
check_config() {
    if [ ! -f "config.json" ]; then
        print_message "未找到配置文件 config.json，创建模板..." $YELLOW
        cat > config.json << 'EOF'
{
    "qbittorrent_instances": [
        {
            "name": "qBittorrent-1",
            "url": "http://192.168.1.100:8080",
            "username": "admin",
            "password": "your_password",
            "traffic_check_url": "",
            "traffic_limit": 0
        },
        {
            "name": "qBittorrent-2", 
            "url": "http://192.168.1.101:8080",
            "username": "admin",
            "password": "your_password",
            "traffic_check_url": "",
            "traffic_limit": 0
        }
    ],
    "webhook_port": 5000,
    "webhook_path": "/webhook/secure-a8f9c2e1-4b3d-9876-abcd-ef0123456789",
    "max_new_tasks_per_instance": 2,
    "max_announce_retries": 30,
    "fast_announce_interval": 3,
    "reconnect_interval": 120,
    "max_reconnect_attempts": 1,
    "connection_timeout": 6,
    "primary_sort_key": "upload_speed",
    "log_dir": "./logs",
    "debug_add_stopped": false,
    "_comment": "注意，traffic_check_url需要配合其他工具使用，默认留空，不检查流量；traffic_limit 单位为MB"
} 
EOF
        print_message "已创建配置文件模板，请修改 config.json 中的 qBittorrent 实例信息！" $YELLOW
        print_message "配置完成后再次运行此脚本。" $YELLOW
        exit 0
    fi
}

# 构建Docker镜像
build_image() {
    print_message "构建 Docker 镜像..." $BLUE
    docker build -t qbittorrent-loadbalancer .
}

# 显示使用帮助
show_help() {
    echo "用法: $0 [选项]"
    echo ""
    echo "选项:"
    echo "  start           启动负载均衡器服务"
    echo "  stop            停止服务"
    echo "  restart         重启服务"
    echo "  logs            查看日志"
    echo "  build           构建镜像"
    echo "  prod            启动生产环境（同start）"
    echo "  status          查看服务状态"
    echo "  clean           清理停止的容器"
    echo "  help            显示此帮助信息"
}

# 主要功能
case "${1:-start}" in
    "start"|"prod")
        check_docker
        # create_directories
        check_config
        build_image
        print_message "启动负载均衡器..." $GREEN
        docker-compose up -d
        print_message "服务已启动！" $GREEN
        ;;
    
    "stop")
        print_message "停止服务..." $YELLOW
        docker-compose down 2>/dev/null || true
        print_message "服务已停止！" $GREEN
        ;;
    
    "restart")
        $0 stop
        sleep 2
        $0 start
        ;;
    
    "logs")
        docker-compose logs -f qbittorrent-loadbalancer 2>/dev/null || \
        print_message "未找到运行中的服务" $RED
        ;;
    
    "build")
        check_docker
        build_image
        print_message "镜像构建完成！" $GREEN
        ;;
    
    "status")
        print_message "服务状态:" $BLUE
        docker ps --filter "name=qbt" --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"
        ;;
    
    "clean")
        print_message "清理停止的容器..." $YELLOW
        docker container prune -f
        print_message "清理完成！" $GREEN
        ;;
    
    "help"|"-h"|"--help")
        show_help
        ;;
    
    *)
        print_message "未知选项: $1" $RED
        show_help
        exit 1
        ;;
esac 