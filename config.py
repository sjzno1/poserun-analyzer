"""
PoseRun Analyzer 配置文件
"""

import os
from pathlib import Path


class Config:
    """基础配置"""
    
    # 基础路径
    BASE_DIR = Path(__file__).parent.parent
    UPLOAD_FOLDER = BASE_DIR / 'uploads'
    REPORTS_FOLDER = BASE_DIR / 'reports'
    
    # Flask 配置
    SECRET_KEY = os.environ.get('SECRET_KEY', 'change-me-to-a-random-secret-key')
    MAX_CONTENT_LENGTH = int(os.environ.get('MAX_CONTENT_LENGTH', 52428800))  # 50MB
    
    # 服务器配置
    HOST = os.environ.get('HOST', '0.0.0.0')
    PORT = int(os.environ.get('PORT', '5000'))
    
    # 视频处理配置
    FRAME_SKIP = int(os.environ.get('FRAME_SKIP', '10'))
    MIN_DETECTION_CONFIDENCE = float(os.environ.get('MIN_DETECTION_CONFIDENCE', '0.5'))
    MIN_TRACKING_CONFIDENCE = float(os.environ.get('MIN_TRACKING_CONFIDENCE', '0.5'))
    
    # 姿态模型配置
    POSE_MODEL_SELECTION = int(os.environ.get('POSE_MODEL_SELECTION', '1'))  # 0=轻量, 1=高精度
    
    # 支持的文件格式
    ALLOWED_EXTENSIONS = {'mp4', 'avi', 'mov', 'mkv', 'webm'}
    
    @classmethod
    def initialize(cls):
        """初始化配置（创建必要的目录）"""
        cls.UPLOAD_FOLDER.mkdir(parents=True, exist_ok=True)
        cls.REPORTS_FOLDER.mkdir(parents=True, exist_ok=True)


class DevelopmentConfig(Config):
    """开发环境配置"""
    DEBUG = True


class ProductionConfig(Config):
    """生产环境配置"""
    DEBUG = False


class TestingConfig(Config):
    """测试环境配置"""
    TESTING = True
    UPLOAD_FOLDER = Path('/tmp/poseRun_test_uploads')
    REPORTS_FOLDER = Path('/tmp/poseRun_test_reports')


# 配置映射
config_map = {
    'development': DevelopmentConfig,
    'production': ProductionConfig,
    'testing': TestingConfig,
}


def get_config(env=None):
    """获取配置对象"""
    if env is None:
        env = os.environ.get('FLASK_ENV', 'development')
    
    return config_map.get(env, DevelopmentConfig)