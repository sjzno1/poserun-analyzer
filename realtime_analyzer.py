"""
PoseRun 实时摄像头分析模块
支持实时视频流中的姿态识别和跑姿分析
"""

import cv2
import numpy as np
import time
from typing import Optional, List, Dict
from dataclasses import dataclass
from threading import Thread, Event
import queue

try:
    import cn_font
except ImportError:
    cn_font = None


def _put_cn(img, text, org, color=(0, 255, 255), size=20):
    """中文文字绘制（无中文字体时退回英文 putText）"""
    if cn_font is not None and cn_font.has_cn_font():
        cn_font.put_text_cn(img, text, org, color, size)
    else:
        cv2.putText(img, text, org, cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2)


@dataclass
class RealTimeResult:
    """实时分析结果"""
    frame_number: int
    timestamp: float
    knee_flexion: float
    shin_angle: float
    stride_level: str
    symmetry_level: str
    problems: List[str]
    suggestions: List[str]
    annotated_frame: np.ndarray


class RealTimeAnalyzer:
    """实时摄像头分析器"""
    
    def __init__(self, pose_analyzer):
        self.pose_analyzer = pose_analyzer
        self.camera = None
        self.is_running = False
        self.stop_event = Event()
        self.result_queue = queue.Queue(maxsize=10)
        self.fps = 0
        self.processed_frames = 0
        self.start_time = 0
        self.analysis_interval = 3  # 每3帧分析一次，保证流畅性
        
        # 结果显示参数
        self.display_metrics = True
        self.draw_skeleton = True
        self.thresholds = {
            'knee_angle_min': 130,
            'knee_angle_max': 165,
            'shin_angle_min': 70,
            'stride_distance_max': 0.05
        }
    
    def start(self, camera_id: int = 0):
        """启动摄像头分析"""
        self.camera = cv2.VideoCapture(camera_id)
        
        if not self.camera.isOpened():
            raise ValueError(f"无法打开摄像头: {camera_id}")
        
        # 设置摄像头参数
        self.camera.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
        self.camera.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
        self.camera.set(cv2.CAP_PROP_FPS, 30)
        
        self.is_running = True
        self.start_time = time.time()
        self.processed_frames = 0
        
        print("=== 实时跑姿分析已启动 ===")
        print("按 'q' 键退出分析")
        print("按 's' 键截图保存")
        print("按 'r' 键重置计数器")
        print("=" * 35)
        
        try:
            self._run_loop()
        finally:
            self.stop()
    
    def _run_loop(self):
        """主分析循环"""
        frame_count = 0
        
        while self.is_running and not self.stop_event.is_set():
            ret, frame = self.camera.read()
            
            if not ret:
                print("警告: 无法读取摄像头帧，尝试重新连接...")
                time.sleep(0.1)
                continue
            
            frame_count += 1
            
            # 定期执行姿态分析
            if frame_count % self.analysis_interval == 0:
                result = self._analyze_frame(frame)
                if result:
                    self.result_queue.put_nowait(result)
                    self.processed_frames += 1
            
            # 显示结果
            self._display_frame(frame, result if frame_count % self.analysis_interval == 0 else None)
            
            # 按键处理
            key = cv2.waitKey(1) & 0xFF
            if key == ord('q'):  # 退出
                break
            elif key == ord('s'):  # 截图
                self._save_screenshot(frame)
            elif key == ord('r'):  # 重置
                self.processed_frames = 0
                self.start_time = time.time()
                print("计数器已重置")
        
        print(f"\n分析结束! 共处理 {self.processed_frames} 帧有效数据")
        if self.processed_frames > 0:
            elapsed = time.time() - self.start_time
            print(f"分析效率: {self.processed_frames / elapsed:.1f} 帧/秒")
    
    def _analyze_frame(self, frame: np.ndarray) -> Optional[RealTimeResult]:
        """分析单帧"""
        analysis = self.pose_analyzer.analyze_frame(frame)
        
        if analysis is None:
            return None
        
        # 创建标注帧
        annotated = frame.copy()
        landmarks = [
            type('obj', (object,), {
                'name': lm.name,
                'x': lm.x,
                'y': lm.y,
                'z': lm.z,
                'to_pixel': lambda w, h, n=lm.name: (
                    int(next(l for l in self.pose_analyzer._landmarks 
                            if l.name == n).x * annotated.shape[1]),
                    int(next(l for l in self.pose_analyzer._landmarks 
                               if l.name == n).y * annotated.shape[0])
                )
            })()
            for lm in self.pose_analyzer._landmarks
        ] if hasattr(self.pose_analyzer, '_landmarks') else []
        
        # 获取最新结果
        width, height = annotated.shape[1], annotated.shape[0]
        
        # 提取关键指标
        metrics = {
            'avg_knee_flexion': analysis.get('knee_flexion', 0),
            'avg_shin_angle': analysis.get('shin_angle', 0),
            'stride_analysis': analysis.get('stride_analysis', {}),
            'symmetry': analysis.get('symmetry', {}),
            'body_leang': analysis.get('body_leang', 0)
        }
        
        # 生成文字标注
        text_y = 24
        _put_cn(annotated, f"膝盖角度: {metrics['avg_knee_flexion']:.1f}°",
                (10, text_y))
        text_y += 28
        _put_cn(annotated, f"小腿角度: {metrics['avg_shin_angle']:.1f}°",
                (10, text_y))
        text_y += 28
        _put_cn(annotated, f"跨步: {metrics['stride_analysis'].get('stride_level', 'N/A')}",
                (10, text_y))
        text_y += 28
        _put_cn(annotated, f"对称性: {metrics['symmetry'].get('symmetry_level', 'N/A')}",
                (10, text_y))
        text_y += 28
        _put_cn(annotated, f"帧数: {self.processed_frames}",
                (10, text_y))
        
        return RealTimeResult(
            frame_number=self.processed_frames,
            timestamp=time.time() - self.start_time,
            knee_flexion=metrics['avg_knee_flexion'],
            shin_angle=metrics['avg_shin_angle'],
            stride_level=metrics['stride_analysis'].get('stride_level', '未知'),
            symmetry_level=metrics['symmetry'].get('symmetry_level', '未知'),
            problems=[],
            suggestions=[],
            annotated_frame=annotated
        )
    
    def _display_frame(self, frame: np.ndarray, result: Optional[RealTimeResult]):
        """显示帧和结果"""
        if self.draw_skeleton and result:
            annotated = result.annotated_frame
        else:
            annotated = frame.copy()
        
        # 显示帧率
        elapsed = time.time() - self.start_time
        if elapsed > 0:
            self.fps = self.processed_frames / elapsed
        
        cv2.putText(annotated, f"FPS: {self.fps:.1f}", (annotated.shape[1] - 150, 30),
                   cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)
        
        cv2.imshow('PoseRun 实时分析', annotated)
    
    def _save_screenshot(self, frame: np.ndarray):
        """保存截图"""
        filename = f"screenshot_{int(time.time())}.jpg"
        cv2.imwrite(filename, frame)
        print(f"截图已保存: {filename}")
    
    def stop(self):
        """停止分析"""
        self.is_running = False
        self.stop_event.set()
        if self.camera:
            self.camera.release()
        cv2.destroyAllWindows()
    
    def get_current_result(self) -> Optional[RealTimeResult]:
        """获取最新分析结果"""
        try:
            return self.result_queue.get_nowait()
        except queue.Empty:
            return None
