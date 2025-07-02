# qBittorrent 负载均衡器

一个智能的 qBittorrent 多实例负载均衡器，能够监控 torrent 文件目录并自动将新的种子分配到最优的 qBittorrent 实例。（这句吹牛逼的介绍是Cursor写的，整个项目都是我动嘴，Cursor动手）

### Autobrr设置要求
Autobrr 的Action设置为Watch Dir，内容填写种子保存的目录和命名规则，例如
```
/seed/[{{.Indexer}}]-{{.TorrentName}}-[{{ now | date_modify "+8h" | date "15-04-05" }}].torrent
```
{{.Indexer}}这个标记就是站点的名称，建议带上，脚本会读取，用于种子分类。

{{ now | date_modify "+8h" | date "15-04-05" }}这个标记是为了记录种子下载时间，并且纠正到中国时间，如果不需要可以删掉

## 功能特点

- 🔄 **智能负载均衡**: 根据可配置的算法（上传速度、下载速度或活跃下载数）选择最优实例
- 📁 **自动文件监控**: 实时监控指定目录的新 torrent 文件
- 🏷️ **自动分类**: 从文件名自动提取分类标签（如 `[Movies]example.torrent` → `Movies`）
- 🔗 **自动重连**: 检测到连接断开时自动尝试重新连接
- 📢 **自动 Announce**: 在指定时间后自动重新汇报种子给 Tracker
- 🐛 **调试模式**: 支持将新添加的种子设置为暂停状态，便于调试
- 📊 **详细日志**: 支持控制台和文件日志，按日期轮转

## 配置文件说明

### 基本配置示例

```json
{
    "qbittorrent_instances": [
        {
            "name": "实例1",
            "url": "http://192.168.1.100:8080",
            "username": "admin",
            "password": "your_password"
        },
        {
            "name": "实例2", 
            "url": "http://192.168.1.101:8080",
            "username": "admin",
            "password": "your_password"
        }
    ],
    "torrent_watch_dir": "./torrents",
    "torrent_max_age_minutes": 5,
    "status_update_interval": 10,
    "max_new_tasks_per_instance": 2,
    "announce_delay_seconds": 60,
    "reconnect_interval": 180,
    "max_reconnect_attempts": 1,
    "connection_timeout": 6,
    "primary_sort_key": "upload_speed",
    "log_dir": "./logs",
    "debug_add_stopped": false
}
```

### 参数详细说明

#### qBittorrent 实例配置

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `qbittorrent_instances` | 数组 | ✅ | qBittorrent 实例列表 |
| `qbittorrent_instances[].name` | 字符串 | ✅ | 实例的友好名称，用于日志显示 |
| `qbittorrent_instances[].url` | 字符串 | ✅ | qBittorrent Web UI 的完整 URL |
| `qbittorrent_instances[].username` | 字符串 | ✅ | Web UI 登录用户名 |
| `qbittorrent_instances[].password` | 字符串 | ✅ | Web UI 登录密码 |

#### 文件监控配置

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `torrent_watch_dir` | 字符串 | 无 | 监控的 torrent 文件目录路径 |
| `torrent_max_age_minutes` | 数字 | 5 | torrent 文件的最大存活时间（分钟），超时会被删除 |

#### 负载均衡配置

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `primary_sort_key` | 字符串 | `"upload_speed"` | 主要排序因素，可选值：<br/>• `"upload_speed"` - 上传速度（小值优先）<br/>• `"download_speed"` - 下载速度（小值优先）<br/>• `"active_downloads"` - 活跃下载数（小值优先） |
| `max_new_tasks_per_instance` | 数字 | 2 | 每轮分配中单个实例的最大新任务数，例如一次来了四个种子，但是此参数设置为2，就会分给两个QB |

#### 系统行为配置

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `status_update_interval` | 数字 | 10 | 实例状态更新间隔（秒），每隔多少秒去获取一次QB的统计信息 |
| `announce_delay_seconds` | 数字 | 60 | 种子添加后的自动 announce 延迟（秒），自动汇报一次，获取更多peer |

#### 连接管理配置

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `reconnect_interval` | 数字 | 180 | 检查断开连接的间隔（秒） |
| `max_reconnect_attempts` | 数字 | 1 | 每次重连检查的最大尝试次数 |
| `connection_timeout` | 数字 | 10 | 连接超时时间（秒） |

#### 日志和调试配置

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `log_dir` | 字符串 | 自动检测 | 日志文件目录，如果为空则自动检测：<br/>• Docker环境：`/app/logs`<br/>• 本地环境：`./logs` |
| `debug_add_stopped` | 布尔值 | `false` | 调试模式：新添加的种子是否默认为暂停状态 |

## 使用方法

### 1. 配置文件设置

复制并修改 `config.json` 文件：
- 替换 `qbittorrent_instances` 中的 URL、用户名和密码
- 设置 `torrent_watch_dir` 为您要监控的目录
- 根据需要调整其他参数

### 2. 运行程序

```bash
# 直接运行
python main.py

# 或使用启动脚本
python run.py
```

### 3. 自动分类功能

程序支持从文件名自动提取分类：
- `[Movies]电影名称.torrent` → 分类：`Movies`
- `[TV]电视剧名称.torrent` → 分类：`TV`
- `[Music]音乐名称.torrent` → 分类：`Music`

### 4. 文件处理流程

1. 监控目录中出现新的 `.torrent` 文件
2. 检查文件大小稳定性（确保文件完全写入）
3. 根据负载均衡算法选择最优实例
4. 添加种子到选定的实例
5. 将处理完成的文件移动到 `processed` 子目录
6. 在指定时间后自动 announce

## Docker 部署

### 📦 Docker 文件说明

- `Dockerfile` - Docker 镜像构建文件
- `docker-compose.yml` - Docker Compose 配置
- `docker-start.sh` - 便捷启动脚本
- `.dockerignore` - Docker 构建忽略文件

### 🚀 快速开始

#### 方式一：使用便捷脚本（推荐）

1. **给脚本添加执行权限**
```bash
chmod +x docker-start.sh
```

2. **首次启动（自动创建配置模板）**
```bash
./docker-start.sh
```

3. **修改配置文件**
编辑生成的 `config.json` 文件，配置您的 qBittorrent 实例信息。

4. **启动服务**
```bash
./docker-start.sh start    # 生产模式
```

#### 方式二：手动使用 Docker Compose

1. **创建配置文件**
```bash
cp config.json.template config.json
# 编辑 config.json 配置您的 qBittorrent 实例
```

2. **创建必要目录**
```bash
mkdir -p torrents logs
```

3. **启动服务**
```bash
# 启动服务
docker-compose up -d
```

### 📋 Docker 脚本命令

```bash
./docker-start.sh <command>
```

| 命令 | 说明 |
|------|------|
| `start` / `prod` | 启动生产环境服务 |
| `stop` | 停止所有服务 |
| `restart` | 重启服务 |
| `logs` | 查看负载均衡器日志 |
| `build` | 重新构建Docker镜像 |
| `status` | 查看服务运行状态 |
| `clean` | 清理停止的容器 |
| `help` | 显示帮助信息 |

### 📁 Docker 目录结构

```
qbittorrent-loadbalancer/
├── main.py                    # 主程序
├── run.py                     # 启动脚本
├── requirements.txt           # Python依赖
├── config.json               # 配置文件（需要自己创建）
├── Dockerfile                # Docker镜像定义
├── docker-compose.yml        # Docker Compose 配置
├── docker-start.sh           # 便捷启动脚本
├── .dockerignore             # Docker构建忽略文件
├── torrents/                 # torrent文件目录（挂载）
│   └── processed/            # 处理完成的文件
└── logs/                     # 日志目录（挂载）
```

### ⚙️ Docker 配置说明

#### 环境变量

在 `docker-compose.yml` 中可以设置以下环境变量：

```yaml
environment:
  - PYTHONUNBUFFERED=1    # Python输出不缓冲
  - TZ=Asia/Shanghai      # 时区设置
```

#### 数据卷挂载

```yaml
volumes:
  - ./config.json:/app/config.json:rw      # 配置文件
  - ./torrents:/app/torrents:rw            # torrent文件目录
  - ./logs:/app/logs:rw                    # 日志目录
```

#### 网络配置

服务使用默认的Docker网络，确保可以访问到您的qBittorrent实例。

### 🛠️ 生产环境部署

#### 1. 准备配置文件

```json
{
    "qbittorrent_instances": [
        {
            "name": "server1",
            "url": "http://192.168.1.100:8080",
            "username": "admin",
            "password": "your_password"
        },
        {
            "name": "server2", 
            "url": "http://192.168.1.101:8080",
            "username": "admin",
            "password": "your_password"
        }
    ],
    "torrent_watch_dir": "/app/torrents",
    "torrent_max_age_minutes": 5,
    "status_update_interval": 10,
    "max_new_tasks_per_instance": 2,
    "announce_delay_seconds": 60,
    "reconnect_interval": 180,
    "max_reconnect_attempts": 1,
    "connection_timeout": 10,
    "primary_sort_key": "upload_speed",
    "log_dir": "/app/logs",
    "debug_add_stopped": false
}
```

#### 2. 启动生产服务

```bash
./docker-start.sh start
```

#### 3. 监控服务

```bash
# 查看服务状态
./docker-start.sh status

# 查看实时日志
./docker-start.sh logs

# 查看Docker状态
docker ps
```

### 🔧 Docker 故障排除

#### 1. 配置文件问题
```bash
# 检查配置文件语法
cat config.json | python -m json.tool
```

#### 2. 网络连接问题
```bash
# 检查容器网络
docker network ls

# 测试到qBittorrent实例的连接
docker run --rm alpine/curl curl -I http://your_qbittorrent_url:8080
```

#### 3. 服务日志
```bash
# 查看详细日志
docker-compose logs qbittorrent-loadbalancer

# 查看特定时间的日志
docker-compose logs --since="2024-01-01" qbittorrent-loadbalancer
```

#### 4. 重新构建镜像
```bash
# 清理并重新构建
docker-compose down
docker rmi qbittorrent-loadbalancer
./docker-start.sh build
./docker-start.sh start
```

### 📊 Docker 健康检查

Docker 镜像包含健康检查功能：

```bash
# 查看健康状态
docker ps --format "table {{.Names}}\t{{.Status}}"

# 查看健康检查日志
docker inspect qbt-loadbalancer | grep -A 10 "Health"
```

### 🔄 Docker 更新和维护

#### 更新代码
```bash
# 停止服务
./docker-start.sh stop

# 拉取最新代码
git pull

# 重新构建并启动
./docker-start.sh build
./docker-start.sh start
```

#### 数据备份
```bash
# 备份配置和日志
tar -czf backup-$(date +%Y%m%d).tar.gz config.json logs/

# 恢复
tar -xzf backup-20240101.tar.gz
```

### 🚨 Docker 注意事项

1. **确保 qBittorrent 实例可访问**
2. **配置正确的网络和防火墙规则**
3. **定期检查日志和服务状态**
4. **备份重要配置文件**
5. **监控磁盘空间使用情况**

## 日志文件

- `qbittorrent_loadbalancer.log`: 主日志文件（DEBUG级别）
- `qbittorrent_error.log`: 错误日志文件（ERROR级别）
- 日志文件按天轮转，保留7天

## 故障排除

### 常见问题

1. **连接失败**: 检查 qBittorrent Web UI 是否启用，URL、用户名和密码是否正确
2. **文件不被处理**: 检查文件权限，确保程序能读取监控目录
3. **重连失败**: 调整 `reconnect_interval` 和 `max_reconnect_attempts` 参数

### 调试技巧

- 启用 `debug_add_stopped: true` 可以暂停新添加的种子，便于调试
- 查看日志文件了解详细的运行状态
- 使用较短的 `status_update_interval` 获得更频繁的状态更新

## 性能优化

- 合理设置 `status_update_interval`：过短会增加网络负载，过长会降低响应速度
- 调整 `max_new_tasks_per_instance` 控制分配速度
- 根据网络环境调整 `connection_timeout` 