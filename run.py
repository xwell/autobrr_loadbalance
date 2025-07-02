#!/usr/bin/env python3
"""
qBittorrent Load Balancer 启动脚本
"""

import sys
import os
from main import main

if __name__ == "__main__":
    print("=" * 60)
    print("qBittorrent 负载均衡器")
    print("=" * 60)
    print()
    
    # 检查配置文件
    if not os.path.exists("config.json"):
        print("错误: 未找到配置文件 config.json")
        print("请先复制并修改 config.json 文件中的qBittorrent实例配置")
        sys.exit(1)
    
    print("正在启动负载均衡器...")
    print("使用 Ctrl+C 停止程序")
    print("-" * 60)
    
    try:
        exit_code = main()
        sys.exit(exit_code)
    except KeyboardInterrupt:
        print("\n程序已停止")
        sys.exit(0) 