"""
PoseRun Analyzer 测试用例
"""

import pytest
import os
import sys
import tempfile
import shutil
from pathlib import Path

# 添加项目根目录到路径
sys.path.insert(0, str(Path(__file__).parent.parent))

from pose_analyzer import PoseAnalyzer, Landmark
from report_generator import ReportGenerator
from config import get_config


class TestLandmark:
    """Landmark数据类测试"""
    
    def test_landmark_creation(self):
        """测试Landmark实例创建"""
        lm = Landmark(name="nose", x=0.5, y=0.6, z=0.1)
        assert lm.name == "nose"
        assert lm.x == 0.5
        assert lm.y == 0.6
        assert lm.z == 0.1
    
    def test_to_pixel(self):
        """测试像素坐标转换（MediaPipe 归一化 y 向下，直接乘宽高）"""
        lm = Landmark(name="test", x=0.5, y=0.5, z=0.0)
        px, py = lm.to_pixel(1920, 1080)
        assert px == 960  # 0.5 * 1920
        assert py == 540  # 0.5 * 1080
    
    def test_to_pixel_edge(self):
        """测试边界情况：y=1.0 在图像底部"""
        lm = Landmark(name="corner", x=0.0, y=1.0, z=0.0)
        px, py = lm.to_pixel(1920, 1080)
        assert px == 0  # 0.0 * 1920
        assert py == 1080  # 1.0 * 1080（图像底部）


class TestPoseAnalyzer:
    """姿态分析器测试"""
    
    def setup_method(self):
        """测试前准备"""
        self.analyzer = PoseAnalyzer()
        self.test_dir = tempfile.mkdtemp()
    
    def teardown_method(self):
        """测试后清理"""
        self.analyzer.close()
        shutil.rmtree(self.test_dir, ignore_errors=True)
    
    def test_analyzer_init(self):
        """测试分析器初始化"""
        assert self.analyzer is not None
    
    def test_calculate_angle(self):
        """测试角度计算"""
        a = Landmark("hip", 0.4, 0.6, 0.0)
        b = Landmark("knee", 0.45, 0.7, 0.0)
        c = Landmark("ankle", 0.5, 0.8, 0.0)
        
        angle = self.analyzer._calculate_angle(a, b, c)
        assert 0 < angle < 180
    
    def test_analyze_frame_no_landmarks(self):
        """测试无人体时的处理"""
        import numpy as np
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        result = self.analyzer.analyze_frame(frame)
        assert result is None
    
    def test_calculate_body_leang(self):
        """测试身体前倾角度计算"""
        points = {
            'LEFT_HIP': Landmark("L_HIP", 0.4, 0.5, 0.0),
            'RIGHT_HIP': Landmark("R_HIP", 0.6, 0.5, 0.0),
            'LEFT_SHOULDER': Landmark("L_SHOULDER", 0.38, 0.3, 0.0),
            'RIGHT_SHOULDER': Landmark("R_SHOULDER", 0.62, 0.3, 0.0),
        }
        
        angle = self.analyzer._calculate_body_leang(points)
        assert isinstance(angle, float)
        assert angle >= 0


class TestReportGenerator:
    """报告生成器测试"""
    
    def setup_method(self):
        """测试前准备"""
        self.generator = ReportGenerator()
        self.test_dir = tempfile.mkdtemp()
    
    def teardown_method(self):
        """测试后清理"""
        shutil.rmtree(self.test_dir, ignore_errors=True)
    
    def test_generate_report(self):
        """测试报告生成"""
        user_info = {
            'name': '测试用户',
            'coach': '系统自动评估',
            'score': 75
        }
        
        sample_results = [
            {
                'knee_flexion': 135.5,
                'shin_angle': 68.2,
                'stride_analysis': {'stride_level': '中度跨步'},
                'symmetry': {'symmetry_level': '良好'},
                'problems': ['存在中度跨步'],
                'suggestions': ['注意落地收紧身体框架']
            }
        ]
        
        output_file = os.path.join(self.test_dir, 'test_report.docx')
        result = self.generator.generate_report(
            'test.mp4',
            user_info,
            sample_results,
            output_file
        )
        
        assert os.path.exists(result)
        assert result.endswith('.docx')


class TestConfig:
    """配置测试"""
    
    def test_get_config(self):
        """测试配置获取"""
        config = get_config('development')
        assert config is not None
        assert hasattr(config, 'UPLOAD_FOLDER')
        assert hasattr(config, 'REPORTS_FOLDER')
    
    def test_config_paths(self):
        """测试配置路径"""
        config = get_config()
        assert config.UPLOAD_FOLDER.exists() or config.UPLOAD_FOLDER.parent.exists()