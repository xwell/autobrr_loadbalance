#!/usr/bin/env python3
"""
qBittorrent Load Balancer
监控torrent文件并智能分配到多个qBittorrent实例
"""

import os
import json
import time
import threading
import logging
import shutil
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass, field
import re

import qbittorrentapi
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler


# 常量定义
DEFAULT_CONFIG_FILE = "config.json"
DEFAULT_SLEEP_TIME = 1
TASK_PROCESSOR_SLEEP = 5
ERROR_RETRY_SLEEP = 5
BYTES_TO_KB = 1024
BYTES_TO_GB = 1024 ** 3
ANNOUNCE_WINDOW_TOLERANCE = 5
FILE_SIZE_CHECK_INTERVAL = 0.3  # 文件大小检查间隔（秒）
FILE_SIZE_CHECK_COUNT = 3  # 连续检查次数
RECONNECT_INTERVAL = 180  # 重连检查间隔（秒）
MAX_RECONNECT_ATTEMPTS = 1  # 每次最大重连尝试次数
CONNECTION_TIMEOUT = 10  # 连接超时时间（秒）

# 支持的排序键（所有均为小值优先）
SUPPORTED_SORT_KEYS = {
    'upload_speed': '上传速度',
    'download_speed': '下载速度',
    'active_downloads': '活跃下载数'
}
DEFAULT_PRIMARY_SORT_KEY = 'upload_speed'

# 创建一个简单的logger，避免在初始化之前输出日志
logger = logging.getLogger(__name__)

def setup_logging(log_dir=None):
    """设置日志配置，同时输出到控制台和文件"""
    # 获取根logger并清理其handlers，避免basicConfig造成的重复
    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    
    # 创建应用logger
    app_logger = logging.getLogger(__name__)
    app_logger.setLevel(logging.DEBUG)  # 设置为DEBUG级别，允许所有日志通过
    
    # 清除现有的handlers，避免重复
    app_logger.handlers.clear()
    
    # 日志格式
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    
    # 控制台输出 - 只显示INFO及以上级别
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(formatter)
    app_logger.addHandler(console_handler)
    
    # 文件输出（只有指定了log_dir才设置）
    if log_dir:
        try:
            # 创建日志目录
            if not os.path.exists(log_dir):
                os.makedirs(log_dir, exist_ok=True)
                
            # 文件处理器 - 按日期轮转
            from logging.handlers import TimedRotatingFileHandler
            file_handler = TimedRotatingFileHandler(
                filename=os.path.join(log_dir, 'qbittorrent_loadbalancer.log'),
                when='midnight',
                interval=1,
                backupCount=7,  # 保留7天的日志
                encoding='utf-8'
            )
            file_handler.setLevel(logging.DEBUG)  # 文件中记录更详细的日志
            file_handler.setFormatter(formatter)
            app_logger.addHandler(file_handler)
            
            # 错误日志单独文件
            error_handler = TimedRotatingFileHandler(
                filename=os.path.join(log_dir, 'qbittorrent_error.log'),
                when='midnight',
                interval=1,
                backupCount=7,
                encoding='utf-8'
            )
            error_handler.setLevel(logging.ERROR)
            error_handler.setFormatter(formatter)
            app_logger.addHandler(error_handler)
            
            app_logger.info(f"日志文件将保存到：{log_dir}")
            
        except Exception as e:
            # 如果文件日志设置失败，至少保证控制台日志正常
            print(f"警告: 无法设置文件日志: {e}")
    
    return app_logger


@dataclass
class InstanceInfo:
    """qBittorrent实例信息"""
    name: str
    url: str
    username: str
    password: str
    client: Optional[qbittorrentapi.Client] = None
    is_connected: bool = False
    upload_speed: float = 0.0  # KB/s
    download_speed: float = 0.0  # KB/s
    active_downloads: int = 0
    free_space: int = 0  # bytes
    new_tasks_count: int = 0  # 新分配的任务数
    last_update: datetime = field(default_factory=datetime.now)
    is_reconnecting: bool = False  # 是否正在重连中


@dataclass
class PendingTorrent:
    """待处理的torrent文件"""
    filepath: str
    created_time: datetime
    

class TorrentWatcher(FileSystemEventHandler):
    """Torrent文件监视器"""
    
    def __init__(self, torrent_manager: 'QBittorrentLoadBalancer') -> None:
        self.torrent_manager = torrent_manager
        
    def on_created(self, event) -> None:
        """处理文件创建事件"""
        if not event.is_directory and event.src_path.endswith('.torrent'):
            logger.info(f"发现新种子文件：{os.path.basename(event.src_path)}")
            # 在后台线程中检查文件大小稳定性，避免阻塞监控
            threading.Thread(
                target=self._check_file_size_stability,
                args=(event.src_path,),
                daemon=True
            ).start()
            
    def _check_file_size_stability(self, filepath: str) -> None:
        """检查文件大小稳定性"""
        try:
            last_size = -1
            stable_count = 0
            
            for _ in range(FILE_SIZE_CHECK_COUNT + 30):  # 多检查几次以确保稳定
                if not os.path.exists(filepath):
                    logger.debug(f"文件已不存在：{os.path.basename(filepath)}")
                    return
                    
                try:
                    current_size = os.path.getsize(filepath)
                    
                    if current_size == last_size:
                        stable_count += 1
                        if stable_count >= FILE_SIZE_CHECK_COUNT:
                            logger.debug(f"文件大小已稳定：{current_size} bytes")
                            self.torrent_manager.add_pending_torrent(filepath)
                            return
                    else:
                        stable_count = 0
                        last_size = current_size
                        
                except OSError:
                    # 文件可能被锁定或不可访问，稍后重试
                    pass
                    
                time.sleep(FILE_SIZE_CHECK_INTERVAL)
                
            logger.warning(f"文件大小检查超时，跳过：{os.path.basename(filepath)}")
            
        except Exception as e:
            logger.error(f"检查文件大小时出错：{filepath}，错误：{e}")


class QBittorrentLoadBalancer:
    """qBittorrent负载均衡器"""
    
    def __init__(self, config_file: str = DEFAULT_CONFIG_FILE):
        self.config = self._load_config(config_file)
        self.instances: List[InstanceInfo] = []
        self.pending_torrents: List[PendingTorrent] = []
        self.pending_torrents_lock = threading.Lock()
        self.instances_lock = threading.Lock()
        
        # 重新配置日志（支持文件输出）
        self._setup_logging()
        
        self._setup_environment()
        
    def _setup_logging(self) -> None:
        """根据配置设置日志"""
        global logger
        
        # 从配置中获取日志目录，默认为 /app/logs（Docker环境）或 ./logs（本地环境）
        log_dir = self.config.get('log_dir')
        if log_dir is None:
            # 自动检测环境
            if os.path.exists('/app'):  # Docker环境
                log_dir = '/app/logs'
            else:  # 本地环境
                log_dir = './logs'
        
        logger = setup_logging(log_dir)
        
    def _setup_environment(self) -> None:
        """设置运行环境"""
        # 验证配置
        self._validate_config()
        
        # 创建torrent监视目录
        os.makedirs(self.config['torrent_watch_dir'], exist_ok=True)
        
        # 初始化qBittorrent实例
        self._init_instances()
        
        # 启动文件监视器
        self._start_file_watcher()
        
    def _validate_config(self) -> None:
        """验证配置文件的有效性"""
        # 验证primary_sort_key配置
        primary_sort_key = self.config.get('primary_sort_key', DEFAULT_PRIMARY_SORT_KEY)
        if primary_sort_key not in SUPPORTED_SORT_KEYS:
            logger.warning(f"不支持的排序键：{primary_sort_key}，使用默认值：{DEFAULT_PRIMARY_SORT_KEY}")
            self.config['primary_sort_key'] = DEFAULT_PRIMARY_SORT_KEY
        else:
            logger.info(f"使用排序策略：主要因素={SUPPORTED_SORT_KEYS[primary_sort_key]}，次要因素=空闲空间")
            
    def _load_config(self, config_file: str) -> dict:
        """加载配置文件"""
        try:
            with open(config_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except FileNotFoundError:
            logger.error(f"配置文件未找到：{config_file}")
            raise
        except json.JSONDecodeError:
            logger.error(f"配置文件格式错误：{config_file}")
            raise
            
    def _init_instances(self) -> None:
        """初始化qBittorrent实例连接"""
        for instance_config in self.config['qbittorrent_instances']:
            instance = self._create_instance_from_config(instance_config)
            self._connect_instance(instance)
            self.instances.append(instance)
            
    def _create_instance_from_config(self, config: Dict[str, str]) -> InstanceInfo:
        """根据配置创建实例信息对象"""
        return InstanceInfo(
            name=config['name'],
            url=config['url'],
            username=config['username'],
            password=config['password']
        )
        
    def _connect_instance(self, instance: InstanceInfo) -> None:
        """连接到qBittorrent实例"""
        try:
            connection_timeout = self.config.get('connection_timeout', CONNECTION_TIMEOUT)
            client = qbittorrentapi.Client(
                host=instance.url,
                username=instance.username,
                password=instance.password,
                REQUESTS_ARGS={'timeout': connection_timeout}
            )
            client.auth_log_in()
            instance.client = client
            instance.is_connected = True
            logger.info(f"成功连接到实例：{instance.name}")
        except Exception as e:
            logger.error(f"连接实例失败：{instance.name}，错误：{e}")
            instance.is_connected = False
            # 记录连接失败的时间，用于后续重连判断
            instance.last_update = datetime.now()
            
    def _attempt_reconnect(self, instance: InstanceInfo) -> bool:
        """尝试重新连接到实例"""
        logger.info(f"尝试重新连接到实例：{instance.name}")
        
        max_attempts = self.config.get('max_reconnect_attempts', MAX_RECONNECT_ATTEMPTS)
        connection_timeout = self.config.get('connection_timeout', CONNECTION_TIMEOUT)
        
        for attempt in range(max_attempts):
            try:
                client = qbittorrentapi.Client(
                    host=instance.url,
                    username=instance.username,
                    password=instance.password,
                    REQUESTS_ARGS={'timeout': connection_timeout}
                )
                
                # 设置连接超时并尝试登录
                client.auth_log_in()
                
                # 更新实例状态需要在锁内进行
                with self.instances_lock:
                    instance.client = client
                    instance.is_connected = True
                    instance.is_reconnecting = False
                    
                logger.info(f"重新连接成功：{instance.name}（尝试 {attempt + 1}/{max_attempts}）")
                return True
                
            except Exception as e:
                logger.warning(f"重连尝试 {attempt + 1}/{max_attempts} 失败：{instance.name}，错误：{e}")
                if attempt < max_attempts - 1:
                    time.sleep(2)  # 每次重连尝试间等待2秒
                    
        logger.error(f"重连彻底失败：{instance.name}")
        
        # 更新失败时间需要在锁内进行
        with self.instances_lock:
            instance.last_update = datetime.now()
            instance.is_reconnecting = False
            
        return False
        
    def _async_reconnect_instance(self, instance: InstanceInfo) -> None:
        """异步重连单个实例（在独立线程中执行）"""
        try:
            self._attempt_reconnect(instance)
        except Exception as e:
            logger.error(f"异步重连过程中发生异常：{instance.name}，错误：{e}")
            with self.instances_lock:
                instance.last_update = datetime.now()
                instance.is_reconnecting = False
        
    def _check_and_schedule_reconnects(self) -> None:
        """检查断开的实例并调度重连（非阻塞）"""
        current_time = datetime.now()
        reconnect_interval = self.config.get('reconnect_interval', RECONNECT_INTERVAL)
        
        instances_to_reconnect = []
        
        with self.instances_lock:
            for instance in self.instances:
                # 只处理未连接且未在重连中的实例
                if not instance.is_connected and not instance.is_reconnecting:
                    # 检查是否到了重连时间
                    time_since_last_attempt = (current_time - instance.last_update).total_seconds()
                    if time_since_last_attempt >= reconnect_interval:
                        instances_to_reconnect.append(instance)
                        # 标记为正在重连，防止重复调度
                        instance.is_reconnecting = True
                        instance.last_update = current_time
                        
        # 在锁外启动重连线程，避免阻塞
        for instance in instances_to_reconnect:
            logger.info(f"开始重连任务：{instance.name}")
            threading.Thread(
                target=self._async_reconnect_instance,
                args=(instance,),
                daemon=True,
                name=f"reconnect-{instance.name}"
            ).start()
        
    def _start_file_watcher(self) -> None:
        """启动文件监视器"""
        event_handler = TorrentWatcher(self)
        observer = Observer()
        observer.schedule(event_handler, self.config['torrent_watch_dir'], recursive=False)
        observer.start()
        logger.info(f"开始监视目录：{self.config['torrent_watch_dir']}")
        
        # 扫描现有文件
        self._scan_existing_torrents()
        
    def _scan_existing_torrents(self) -> None:
        """扫描现有的torrent文件"""
        watch_dir = Path(self.config['torrent_watch_dir'])
        for torrent_file in watch_dir.glob("*.torrent"):
            self.add_pending_torrent(str(torrent_file))
            
    def add_pending_torrent(self, filepath: str) -> None:
        """添加待处理的torrent文件"""
        try:
            stat = os.stat(filepath)
            created_time = datetime.fromtimestamp(stat.st_ctime)
            current_time = datetime.now()
            
            # 检查文件创建时间是否在允许的时间窗内
            max_age = timedelta(minutes=self.config['torrent_max_age_minutes'])
            file_age = current_time - created_time
            
            if file_age > max_age:
                logger.warning(f"跳过过期文件：{os.path.basename(filepath)}，"
                             f"文件创建于 {file_age.total_seconds():.0f} 秒前，"
                             f"超过 {max_age.total_seconds():.0f} 秒的时间限制")
                return
            
            with self.pending_torrents_lock:
                # 检查是否已存在
                if not any(t.filepath == filepath for t in self.pending_torrents):
                    torrent = PendingTorrent(filepath=filepath, created_time=created_time)
                    self.pending_torrents.append(torrent)
                    logger.info(f"添加待处理种子文件：{os.path.basename(filepath)}，"
                               f"文件创建于 {file_age.total_seconds():.0f} 秒前")
                else:
                    logger.debug(f"文件已在待处理列表中：{os.path.basename(filepath)}")
        except Exception as e:
            logger.error(f"添加种子文件失败：{filepath}，错误：{e}")
            
    def _clean_old_torrents(self) -> None:
        """清理超过时间限制的torrent文件"""
        max_age = timedelta(minutes=self.config['torrent_max_age_minutes'])
        current_time = datetime.now()
        
        with self.pending_torrents_lock:
            to_remove = []
            for torrent in self.pending_torrents:
                if current_time - torrent.created_time > max_age:
                    try:
                        os.remove(torrent.filepath)
                        to_remove.append(torrent)
                        logger.info(f"删除过期种子文件：{os.path.basename(torrent.filepath)}")
                    except Exception as e:
                        logger.error(f"删除文件失败：{torrent.filepath}，错误：{e}")
                        
            for torrent in to_remove:
                self.pending_torrents.remove(torrent)
                
    def _update_instance_status(self) -> None:
        """更新所有实例的状态信息"""
        with self.instances_lock:
            for instance in self.instances:
                if not instance.is_connected:
                    continue
                    
                try:
                    # 单次API调用获取所有数据
                    maindata = instance.client.sync_maindata()
                    
                    self._update_single_instance_status(instance, maindata)
                    self._process_instance_announces(instance, maindata)
                               
                except Exception as e:
                    logger.error(f"更新实例状态失败：{instance.name}，错误：{e}")
                    instance.is_connected = False
                    instance.last_update = datetime.now()  # 记录连接断开时间
                    
    def _update_single_instance_status(self, instance: InstanceInfo, maindata: dict) -> None:
        """使用sync/maindata的结果更新单个实例的状态信息"""
        server_state = maindata.get('server_state', {})
        
        # 从server_state获取全局统计信息和硬盘空间
        instance.upload_speed = server_state.get('up_info_speed', 0) / BYTES_TO_KB
        instance.download_speed = server_state.get('dl_info_speed', 0) / BYTES_TO_KB
        instance.free_space = server_state.get('free_space_on_disk', 0)
        
        # 从torrents信息计算活跃下载数
        all_torrents = maindata.get('torrents', {}).values()
        instance.active_downloads = len([t for t in all_torrents if t.state == 'downloading'])
        
        instance.last_update = datetime.now()
        logger.debug(f"更新实例状态 {instance.name}：" 
                   f"上传={instance.upload_speed:.1f}KB/s，"
                   f"下载={instance.download_speed:.1f}KB/s，"
                   f"活跃下载={instance.active_downloads}，"
                   f"空闲空间={instance.free_space/BYTES_TO_GB:.1f}GB")
                   
    def _process_instance_announces(self, instance: InstanceInfo, maindata: dict) -> None:
        """使用sync/maindata的结果处理实例的种子自动announce"""
        current_time = datetime.now()
        announce_delay = self.config['announce_delay_seconds']
        announce_window_start = announce_delay - ANNOUNCE_WINDOW_TOLERANCE
        announce_window_end = announce_delay + ANNOUNCE_WINDOW_TOLERANCE
        
        try:
            all_torrents_items = maindata.get('torrents', {}).items()
            for torrent_hash, torrent in all_torrents_items:
                torrent_age = (current_time - datetime.fromtimestamp(torrent.added_on)).total_seconds()
                if announce_window_start <= torrent_age <= announce_window_end:
                    self._announce_torrent(instance, torrent, torrent_hash, torrent_age)
        except Exception as e:
            logger.warning(f"处理自动汇报时出错：{instance.name}，错误：{e}")
            
    def _announce_torrent(self, instance: InstanceInfo, torrent: any, torrent_hash: str, torrent_age: float) -> None:
        """对单个种子执行announce"""
        try:
            instance.client.torrents_reannounce(torrent_hashes=torrent_hash)
            logger.info(f"自动重新汇报种子：{torrent.name}（添加于 {torrent_age:.0f} 秒前），实例：{instance.name}")
        except Exception as e:
            logger.warning(f"重新汇报失败：{torrent.name}，错误：{e}")
                    
    def _extract_category_from_filename(self, filepath: str) -> Optional[str]:
        """从文件名中提取分类信息
        
        Args:
            filepath: 文件路径
            
        Returns:
            分类字符串或None（如果没有找到分类）
            
        Examples:
            "[Movies]example.torrent" -> "Movies"
            "[TV]show.torrent" -> "TV"
            "normal.torrent" -> None
        """
        filename = os.path.basename(filepath)
        
        # 使用正则表达式匹配 [分类] 格式
        match = re.match(r'^\[([^\]]+)\]', filename)
        if match:
            category = match.group(1)
            logger.debug(f"从文件名提取分类：{filename} -> {category}")
            return category
        
        return None
        
    def _get_primary_sort_value(self, instance: InstanceInfo) -> float:
        """获取主要排序因素的值"""
        primary_sort_key = self.config.get('primary_sort_key', DEFAULT_PRIMARY_SORT_KEY)
        
        if primary_sort_key == 'upload_speed':
            return instance.upload_speed
        elif primary_sort_key == 'download_speed':
            return instance.download_speed
        elif primary_sort_key == 'active_downloads':
            return float(instance.active_downloads)
        else:
            # 默认使用上传速度
            return instance.upload_speed
        
    def _select_best_instance(self) -> Optional[InstanceInfo]:
        """选择最佳的实例来分配新任务"""
        with self.instances_lock:
            available_instances = [
                instance for instance in self.instances 
                if instance.is_connected and 
                instance.new_tasks_count < self.config['max_new_tasks_per_instance']
            ]
            
            if not available_instances:
                return None
                
            # 按可配置算法排序：主要因素（小值优先），次要因素是硬盘空间（大值优先）
            available_instances.sort(key=lambda x: (
                self._get_primary_sort_value(x),  # 主要因素：小值优先
                -x.free_space  # 次要因素：大值优先（使用负号）
            ))
            
            selected = available_instances[0]
            primary_sort_key = self.config.get('primary_sort_key', DEFAULT_PRIMARY_SORT_KEY)
            primary_value = self._get_primary_sort_value(selected)
            
            logger.debug(f"选择实例 {selected.name}：" 
                        f"{SUPPORTED_SORT_KEYS[primary_sort_key]}={primary_value:.1f}，"
                        f"空闲空间={selected.free_space/BYTES_TO_GB:.1f}GB")
            
            return selected
            
    def _add_torrent_to_instance(self, instance: InstanceInfo, torrent_path: str) -> bool:
        """将torrent添加到指定实例"""
        try:
            with open(torrent_path, 'rb') as f:
                torrent_data = f.read()
            
            # 从文件名中提取分类信息
            category = self._extract_category_from_filename(torrent_path)
            
            # 构建添加种子的参数
            add_params = {'torrent_files': torrent_data}
            
            # 如果提取到分类，则添加分类参数
            if category:
                add_params['category'] = category
                logger.info(f"为种子设置分类：{os.path.basename(torrent_path)} -> {category}")
                
            # 根据配置决定是否将种子添加为暂停状态（用于调试）
            if self.config.get('debug_add_stopped', False):
                add_params['is_stopped'] = True
                logger.info(f"调试模式开启，种子将以暂停状态添加：{os.path.basename(torrent_path)}")

            result = instance.client.torrents_add(**add_params)
            
            if result and result.startswith('Ok'):
                instance.new_tasks_count += 1
                log_msg = f"成功添加种子到实例 {instance.name}：{os.path.basename(torrent_path)}"
                if category:
                    log_msg += f"（分类：{category}）"
                logger.info(log_msg)
                return True
            else:
                logger.error(f"添加种子失败：{instance.name}，结果：{result}")
                return False
                
        except Exception as e:
            logger.error(f"添加种子到实例失败：{instance.name}，错误：{e}")
            return False
            
    def _process_torrents(self) -> None:
        """处理待分配的torrent文件"""
        with self.pending_torrents_lock:
            if not self.pending_torrents:
                return
                
            # 直接处理所有待处理的torrents，无需额外过滤
            # 因为过期的文件已经在_clean_old_torrents中被删除了
            for torrent in self.pending_torrents[:]:  # 使用切片避免修改列表时的问题
                instance = self._select_best_instance()
                if instance:
                    if self._add_torrent_to_instance(instance, torrent.filepath):
                        self.pending_torrents.remove(torrent)
                        self._move_processed_torrent(torrent.filepath)
                else:
                    logger.warning("没有可用的实例来分配新任务")
                    break
                    

        
    def _move_processed_torrent(self, torrent_filepath: str) -> None:
        """将处理完成的torrent文件移动到processed目录"""
        try:
            processed_dir = os.path.join(os.path.dirname(torrent_filepath), "processed")
            os.makedirs(processed_dir, exist_ok=True)
            destination = os.path.join(processed_dir, os.path.basename(torrent_filepath))
            shutil.move(torrent_filepath, destination)
            logger.debug(f"已移动处理完成的文件：{os.path.basename(torrent_filepath)}")
        except Exception as e:
            logger.error(f"移动处理完成的文件失败：{torrent_filepath}，错误：{e}")
                    

    def _reset_task_counters(self) -> None:
        """重置任务计数器（每轮处理完成后）"""
        with self.instances_lock:
            for instance in self.instances:
                instance.new_tasks_count = 0
                
    def _log_status_summary(self) -> None:
        """记录状态摘要信息"""
        with self.instances_lock:
            total_instances = len(self.instances)
            connected_count = sum(1 for i in self.instances if i.is_connected)
            disconnected_instances = [i.name for i in self.instances if not i.is_connected]
            
            status_msg = f"实例状态: {connected_count}/{total_instances} 连接正常"
            if disconnected_instances:
                status_msg += f", 断开连接: {', '.join(disconnected_instances)}"
            # 移除待处理torrent数量，因为该信息10s更新一次时效性太差
            
            logger.debug(status_msg)
                
    def status_update_thread(self) -> None:
        """状态更新线程"""
        logger.info("状态更新线程启动")
        
        while True:
            try:
                self._update_instance_status()
                self._clean_old_torrents()
                self._log_status_summary()
                self._check_and_schedule_reconnects()
                              
                time.sleep(self.config['status_update_interval'])
                
            except Exception as e:
                logger.error(f"状态更新线程错误：{e}")
                time.sleep(ERROR_RETRY_SLEEP)
                
    def task_processor_thread(self) -> None:
        """任务处理线程"""
        logger.info("任务处理线程启动")
        
        while True:
            try:
                # 记录当前待处理的种子数量（更及时的信息）
                with self.pending_torrents_lock:
                    pending_count = len(self.pending_torrents)
                
                if pending_count > 0:
                    logger.debug(f"处理 {pending_count} 个待分配的种子文件")
                
                self._process_torrents()
                self._reset_task_counters()
                time.sleep(TASK_PROCESSOR_SLEEP)
                
            except Exception as e:
                logger.error(f"任务处理线程错误：{e}")
                time.sleep(ERROR_RETRY_SLEEP)
                
    def run(self) -> None:
        """运行负载均衡器"""
        logger.info("qBittorrent负载均衡器启动")
        
        # 启动状态更新线程
        status_thread = threading.Thread(target=self.status_update_thread, daemon=True)
        status_thread.start()
        
        # 启动任务处理线程
        task_thread = threading.Thread(target=self.task_processor_thread, daemon=True)
        task_thread.start()
        
        try:
            # 主线程保持运行
            while True:
                time.sleep(DEFAULT_SLEEP_TIME)
        except KeyboardInterrupt:
            logger.info("收到停止信号，正在关闭...")


def main() -> int:
    """主函数"""
    try:
        balancer = QBittorrentLoadBalancer()
        balancer.run()
        return 0
    except Exception as e:
        logger.error(f"程序启动失败：{e}")
        return 1


if __name__ == "__main__":
    exit(main()) 