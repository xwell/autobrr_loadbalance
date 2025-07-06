#!/usr/bin/env python3
"""
Webhook 服务器模块
接收 autobrr 的 webhook 通知并处理种子数据
"""

import logging
import threading
import time
from datetime import datetime
from typing import Optional, Dict, Any

from flask import Flask, request, jsonify

logger = logging.getLogger(__name__)


class WebhookServer:
    """Webhook服务器，接收autobrr通知"""
    
    def __init__(self, torrent_manager: 'QBittorrentLoadBalancer', config: Dict[str, Any]):
        self.torrent_manager = torrent_manager
        self.config = config
        self.app = Flask(__name__)
        self.app.logger.disabled = True  # 禁用Flask的默认日志
        self.server_thread: Optional[threading.Thread] = None
        self.is_running = False
        
        # webhook配置
        self.webhook_port = config.get('webhook_port', 5000)
        self.webhook_path = config.get('webhook_path', '/webhook')
        # 安全提示：建议设置复杂的webhook_path作为安全措施
        
        self._setup_routes()
    
    def _setup_routes(self):
        """设置路由"""
        
        @self.app.route('/health', methods=['GET'])
        def health_check():
            """健康检查接口"""
            return jsonify({
                'status': 'ok',
                'timestamp': datetime.now().isoformat(),
                'instances_connected': len([i for i in self.torrent_manager.instances if i.is_connected])
            })
        
        @self.app.route(self.webhook_path, methods=['POST'])
        def webhook_handler():
            """处理webhook请求"""
            try:
                # 获取请求数据
                data = request.get_json()
                if not data:
                    logger.error("webhook请求缺少JSON数据")
                    return jsonify({'error': 'No JSON data'}), 400
                
                logger.info(f"收到webhook通知: {data.get('release_name', 'Unknown')}")
                
                # 处理种子数据
                success = self._process_webhook_data(data)
                
                if success:
                    return jsonify({'status': 'success', 'message': 'Torrent processed'})
                else:
                    return jsonify({'status': 'error', 'message': 'Failed to process torrent'}), 500
                    
            except Exception as e:
                logger.error(f"处理webhook请求时出错: {e}")
                return jsonify({'error': 'Internal server error'}), 500
    

    
    def _process_webhook_data(self, data: Dict[str, Any]) -> bool:
        """处理webhook数据"""
        try:
            torrent_data = self._extract_torrent_data(data)
            if not torrent_data:
                return False
            
            release_name, download_url, indexer, category = torrent_data
            logger.info(f"接收到种子：{release_name} (来源：{indexer})")
            
            # 传递给负载均衡器处理
            self.torrent_manager.add_pending_torrent(
                download_url=download_url,
                release_name=release_name,
                category=category or indexer
            )
            return True
            
        except Exception as e:
            logger.error(f"处理webhook数据时出错: {e}")
            return False
    
    def _extract_torrent_data(self, data: Dict[str, Any]) -> Optional[tuple]:
        """从webhook数据中提取种子信息"""
        release_name = data.get('release_name', '')
        download_url = data.get('download_url', '')
        indexer = data.get('indexer', '')
        category = data.get('category', '')
        
        if not release_name:
            logger.error("webhook数据缺少种子名称")
            return None
        
        if not download_url:
            logger.error("webhook数据缺少下载链接")
            return None
        
        return release_name, download_url, indexer, category
    

    
    def start(self):
        """启动webhook服务器"""
        if self.is_running:
            return
        
        self.is_running = True
        logger.info(f"启动webhook服务器: http://0.0.0.0:{self.webhook_port}{self.webhook_path}")
        
        self.server_thread = threading.Thread(target=self._run_server, daemon=True)
        self.server_thread.start()
        
        # 等待确保服务器启动
        time.sleep(1)
        
        status_msg = "webhook服务器启动成功" if self.is_running else "webhook服务器启动失败"
        logger.info(status_msg) if self.is_running else logger.error(status_msg)
    
    def _run_server(self):
        """运行Flask服务器"""
        try:
            self.app.run(
                host='0.0.0.0',
                port=self.webhook_port,
                debug=False,
                use_reloader=False,
                threaded=True
            )
        except Exception as e:
            logger.error(f"webhook服务器启动失败: {e}")
            self.is_running = False
    
    def stop(self):
        """停止webhook服务器"""
        if not self.is_running:
            return
        
        self.is_running = False
        logger.info("webhook服务器已停止")
    
 