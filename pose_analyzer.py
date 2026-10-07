"""
PoseRun Analyzer - 跑姿分析系统核心模块
基于MediaPipe的姿态识别与分析报告生成
支持关键帧标注、历史对比等高级功能
"""

import cv2
import numpy as np
import mediapipe as mp
from dataclasses import dataclass, field
from typing import List, Dict, Tuple, Optional
from pathlib import Path
import os
import math
import json
from datetime import datetime

try:
    import cn_font
except ImportError:
    cn_font = None

# 最近一次 PoseAnalyzer 使用的后端（供 /api/health 展示）
LAST_POSE_BACKEND = 'not_loaded'

# MediaPipe 33 点姿态 landmark 名称表（BlazePose 标准布局，
# Tasks PoseLandmarker 与 legacy solutions 输出顺序完全一致，
# 不依赖 mp.solutions —— mediapipe 0.10.20+/1.x 已移除该旧 API）
POSE_LANDMARK_NAMES = [
    'NOSE', 'LEFT_EYE_INNER', 'LEFT_EYE', 'LEFT_EYE_OUTER', 'RIGHT_EYE_INNER',
    'RIGHT_EYE', 'RIGHT_EYE_OUTER', 'LEFT_EAR', 'RIGHT_EAR', 'MOUTH_LEFT',
    'MOUTH_RIGHT', 'LEFT_SHOULDER', 'RIGHT_SHOULDER', 'LEFT_ELBOW', 'RIGHT_ELBOW',
    'LEFT_WRIST', 'RIGHT_WRIST', 'LEFT_PINKY', 'RIGHT_PINKY', 'LEFT_INDEX',
    'RIGHT_INDEX', 'LEFT_THUMB', 'RIGHT_THUMB', 'LEFT_HIP', 'RIGHT_HIP',
    'LEFT_KNEE', 'RIGHT_KNEE', 'LEFT_ANKLE', 'RIGHT_ANKLE', 'LEFT_HEEL',
    'RIGHT_HEEL', 'LEFT_FOOT_INDEX', 'RIGHT_FOOT_INDEX',
]


@dataclass
class Landmark:
    """33个姿态关键点"""
    name: str
    x: float  # 归一化坐标 [0, 1]
    y: float  # 归一化坐标 [0, 1]
    z: float  # 深度信息
    visibility: float = 1.0  # 可见度置信度 [0, 1]，遮挡/低置信关键点较低
    
    def to_pixel(self, width: int, height: int) -> Tuple[int, int]:
        """转换为像素坐标

        MediaPipe 归一化坐标与图像像素坐标同为 y 向下（y=0 在图像顶部），
        直接乘宽高即可，不能再翻转 y，否则骨骼会被垂直镜像画到错误位置。
        """
        return (int(self.x * width), int(self.y * height))


class KeyFrameAnalyzer:
    """关键帧提取与标注分析器"""
    
    # 关键跑步阶段定义
    STANCE_PHASES = ['heel_strike', 'mid_stance', 'toe_off']
    SWING_PHASES = ['initial_swinger', 'mid_swing', 'terminal_swing']
    
    def __init__(self, frame_width: int = 1920, frame_height: int = 1080):
        self.frame_width = frame_width
        self.frame_height = frame_height
        
    def extract_key_frames(self, video_path: str, num_frames: int = 5) -> List[Dict]:
        """提取关键帧（均匀分布 + 动作变化最大帧）"""
        cap = cv2.VideoCapture(video_path)
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        
        if total_frames == 0:
            return []
        
        # 策略1: 均匀分布提取
        uniform_indices = [
            int(i * total_frames / num_frames) 
            for i in range(num_frames)
        ]
        
        # 策略2: 基于姿态变化的关键帧
        change_indices = self._find_action_change_frames(cap, total_frames, num_frames)
        
        # 合并并去重
        selected_indices = sorted(set(uniform_indices) | set(change_indices))
        selected_indices = selected_indices[:num_frames]  # 限制数量
        
        key_frames = []
        for idx in selected_indices:
            cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
            ret, frame = cap.read()
            if ret:
                key_frames.append({
                    'frame_index': idx,
                    'frame_number': len(key_frames),
                    'frame_data': frame.copy(),
                    'timestamp': self._frame_to_timestamp(cap, idx)
                })
        
        cap.release()
        return key_frames
    
    def _find_action_change_frames(self, cap, total_frames: int, num_frames: int) -> List[int]:
        """基于姿态变化查找关键帧"""
        change_indices = []
        prev_landmarks = None
        last_check = 0
        check_interval = max(1, total_frames // 50)  # 最多检查50次
        
        for frame_idx in range(total_frames):
            ret, frame = cap.read()
            if not ret:
                continue
            
            if frame_idx - last_check < check_interval:
                continue
            
            # 简单运动检测（通过像素差异）
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            if prev_landmarks is not None:
                prev_gray = prev_landmarks['gray']
                diff = cv2.absdiff(gray, prev_gray)
                if np.mean(diff) > 30:  # 运动阈值
                    change_indices.append(frame_idx)
            
            prev_landmarks = {'gray': gray}
            last_check = frame_idx
            
            if len(change_indices) >= num_frames:
                break
        
        return change_indices
    
    def _frame_to_timestamp(self, cap, frame_idx: int) -> str:
        """帧索引转时间戳"""
        fps = cap.get(cv2.CAP_PROP_FPS)
        if fps > 0:
            seconds = frame_idx / fps
            return f"{int(seconds // 60):02d}:{int(seconds % 60):02d}.{int((seconds % 1) * 100):02d}"
        return "00:00.00"
    
    # 跑步阶段中文映射（用于截图标注）
    PHASE_LABELS = {
        'heel_strike': '脚跟触地',
        'mid_stance': '承重中期',
        'toe_off': '蹬离前',
        'swing': '摆动期',
    }

    def draw_pose_annotations(self, frame: np.ndarray, landmarks: Optional[List[Landmark]] = None,
                              metrics: Optional[Dict] = None) -> np.ndarray:
        """在帧上绘制姿态标注（骨骼 + 关键点 + 指标面板）"""
        annotated = frame.copy()

        if landmarks is None:
            return annotated

        points = {lm.name: lm for lm in landmarks}
        height, width = annotated.shape[:2]

        # 定义骨骼连接
        connections = [
            ('LEFT_SHOULDER', 'RIGHT_SHOULDER'),
            ('LEFT_SHOULDER', 'LEFT_ELBOW'),
            ('LEFT_ELBOW', 'LEFT_WRIST'),
            ('RIGHT_SHOULDER', 'RIGHT_ELBOW'),
            ('RIGHT_ELBOW', 'RIGHT_WRIST'),
            ('LEFT_HIP', 'RIGHT_HIP'),
            ('LEFT_SHOULDER', 'LEFT_HIP'),
            ('RIGHT_SHOULDER', 'RIGHT_HIP'),
            ('LEFT_HIP', 'LEFT_KNEE'),
            ('LEFT_KNEE', 'LEFT_ANKLE'),
            ('RIGHT_HIP', 'RIGHT_KNEE'),
            ('RIGHT_KNEE', 'RIGHT_ANKLE'),
        ]

        # 绘制骨骼连线
        for start, end in connections:
            if start in points and end in points:
                p1 = points[start].to_pixel(width, height)
                p2 = points[end].to_pixel(width, height)
                cv2.line(annotated, p1, p2, (0, 255, 0), 2)

        # 绘制关键点
        for lm in landmarks:
            px, py = lm.to_pixel(width, height)
            cv2.circle(annotated, (px, py), 4, (255, 0, 0), -1)

        # 指标面板（右上角半透明背景 + 中文指标）
        if metrics:
            phase = metrics.get('running_phase', '')
            phase_cn = self.PHASE_LABELS.get(phase, phase)
            stride_cn = metrics.get('stride_analysis', {}).get('stride_level', '') or 'N/A'
            lines = [
                f"阶段: {phase_cn}",
                f"膝盖: {metrics.get('knee_flexion', 0):.0f}°",
                f"小腿: {metrics.get('shin_angle', 0):.0f}°",
                f"跨步: {stride_cn}",
            ]
            use_cn = cn_font is not None and cn_font.has_cn_font()
            if use_cn:
                font_size = 22
                panel_w = max(cn_font.text_size_cn(t, font_size)[0] for t in lines) + 24
                line_h = font_size + 8
            else:
                # 无中文字体时退回英文 + cv2.putText
                lines = [
                    f"Phase: {phase}",
                    f"Knee: {metrics.get('knee_flexion', 0):.0f} deg",
                    f"Shin: {metrics.get('shin_angle', 0):.0f} deg",
                    f"Stride: {stride_cn}",
                ]
                font_size, panel_w, line_h = 20, 220, 26
            panel_h = line_h * (len(lines) + 1) + 10
            x0, y0 = width - panel_w - 12, 12
            cv2.rectangle(annotated, (x0, y0), (x0 + panel_w, y0 + panel_h),
                          (0, 0, 0), -1)
            cv2.rectangle(annotated, (x0, y0), (x0 + panel_w, y0 + panel_h),
                          (0, 255, 255), 2)
            for i, line in enumerate(lines):
                ty = y0 + 22 + i * line_h
                if use_cn:
                    cn_font.put_text_cn(annotated, line, (x0 + 10, ty - font_size + 4),
                                         (0, 255, 255), font_size)
                else:
                    cv2.putText(annotated, line, (x0 + 8, ty),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 2)

        # 左上角帧信息
        if cn_font is not None and cn_font.has_cn_font():
            cn_font.put_text_cn(annotated, f"关键点: {len(landmarks)}", (10, 12),
                                (0, 255, 255), 22)
        else:
            cv2.putText(annotated, f"Points: {len(landmarks)}",
                        (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 255), 2)

        return annotated

    def save_annotations(self, output_dir: str, key_frames: List[Dict],
                        analysis_results: List[Dict]):
        """保存标注后的关键帧图片（含指标面板）"""
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)

        for i, kf in enumerate(key_frames):
            frame_data = kf['frame_data']
            # 获取对应的分析结果（按真实视频帧号匹配，见 _find_related_result）
            related_result = self._find_related_result(analysis_results, kf['frame_index'])

            # 标注姿态 + 指标
            landmarks = related_result.get('landmarks', []) if related_result else []
            metrics = related_result.get('metrics', related_result) if related_result else None
            annotated = self.draw_pose_annotations(frame_data, landmarks, metrics)

            # 保存文件
            output_file = output_path / f"keyframe_{i+1:02d}.jpg"
            cv2.imwrite(str(output_file), annotated)

            # 保存标注信息JSON（供报告图注使用）
            info_file = output_path / f"keyframe_{i+1:02d}_info.json"
            info = {
                'frame_index': kf['frame_index'],
                'timestamp': kf['timestamp'],
                'frame_number': kf['frame_number'],
                'metrics': related_result if related_result else {}
            }
            with open(info_file, 'w', encoding='utf-8') as f:
                json.dump(info, f, ensure_ascii=False, indent=2, default=str)

        return str(output_path)
    
    def _find_related_result(self, analysis_results: List[Dict], frame_index: int) -> Optional[Dict]:
        """查找与关键帧最接近的分析结果（按真实视频帧号匹配）

        必须用关键帧的 frame_index（视频中的真实帧号）去匹配
        analysis_results 里带 frame_index 的逐帧结果。若误用关键帧的
        序号（0,1,2...），会把所有标注都指到视频开头的几帧，导致截图上
        的骨骼/指标与实际画面错位。
        """
        if not analysis_results:
            return None

        if frame_index <= 0:
            return analysis_results[0]

        # 取帧号 <= frame_index 的最后一个（最接近且不超过该帧的分析结果）
        for result in reversed(analysis_results):
            if result.get('frame_index', result.get('frame_number', 0)) <= frame_index:
                return result

        return analysis_results[-1]


class PoseAnalyzer:
    """姿态分析器 - 提取关键跑步指标

    后端：优先 MediaPipe Tasks PoseLandmarker（heavy 模型，精度更高），
    模型文件缺失/初始化失败时自动回退 legacy solutions（model_complexity=1）。
    """

    MODEL_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              'data', 'models', 'pose_landmarker_heavy.task')

    def __init__(self):
        self.frame_analyzer = KeyFrameAnalyzer()
        self._landmarker = None
        self._video_ts_ms = 0
        self._frame_interval_ms = 33  # VIDEO 模式时间戳步长（process_video 按实际 fps 重置）
        self.mp_pose = None  # legacy 后端句柄（仅 mediapipe<0.10.20 可用时设置）
        self.backend = self._init_backend()

    # ---------- 后端初始化 ----------

    def _init_backend(self):
        """优先 Tasks PoseLandmarker（heavy）；失败回退 legacy solutions。

        mediapipe 0.10.20+/1.x 已移除 mp.solutions，legacy 回退不再可用；
        此时若 heavy 模型也缺失，标记为 unavailable（服务保持可启动，
        /api/health 会暴露问题，分析接口返回明确错误）。
        """
        global LAST_POSE_BACKEND
        try:
            if not os.path.exists(self.MODEL_PATH):
                raise FileNotFoundError(self.MODEL_PATH)
            from mediapipe.tasks.python import BaseOptions
            from mediapipe.tasks.python.vision import PoseLandmarker, PoseLandmarkerOptions, RunningMode
            options = PoseLandmarkerOptions(
                base_options=BaseOptions(model_asset_path=self.MODEL_PATH),
                running_mode=RunningMode.VIDEO,
                num_poses=1,
                min_pose_detection_confidence=0.5,
                min_tracking_confidence=0.5,
            )
            self._landmarker = PoseLandmarker.create_from_options(options)
            LAST_POSE_BACKEND = 'tasks_heavy'
            return 'tasks_heavy'
        except Exception as e:
            print(f'[pose] Tasks PoseLandmarker 不可用（{e}）')
            if hasattr(mp, 'solutions'):
                print('[pose] 回退 legacy solutions')
                self.mp_pose = mp.solutions.pose.Pose(
                    static_image_mode=False,
                    model_complexity=1,
                    min_detection_confidence=0.5,
                    min_tracking_confidence=0.5
                )
                LAST_POSE_BACKEND = 'legacy'
                return 'legacy'
            print('[pose] legacy solutions 不可用（mediapipe>=0.10.20），后端标记为 unavailable')
            self._landmarker = None
            self.mp_pose = None
            LAST_POSE_BACKEND = 'unavailable'
            return 'unavailable'

    def _detect_landmarks(self, rgb_frame: np.ndarray):
        """统一关键点检测入口。
        返回 (landmarks: List[Landmark], frame_index: int)；未检测到人体返回 (None, frame_index)。
        """
        if self._landmarker is None and self.mp_pose is None:
            return None, 0
        if self._landmarker is not None:
            import mediapipe as mp2
            self._video_ts_ms += self._frame_interval_ms  # 时间戳递增（VIDEO 模式要求单调递增）
            mp_image = mp2.Image(image_format=mp2.ImageFormat.SRGB, data=rgb_frame)
            result = self._landmarker.detect_for_video(mp_image, self._video_ts_ms)
            if not result.pose_landmarks:
                return None, self._video_ts_ms
            lms = []
            for idx, lm in enumerate(result.pose_landmarks[0]):
                lms.append(Landmark(
                    name=POSE_LANDMARK_NAMES[idx],
                    x=float(lm.x), y=float(lm.y), z=float(lm.z),
                    visibility=float(lm.visibility) if lm.visibility is not None else 1.0,
                ))
            return lms, self._video_ts_ms
        # legacy 后端（仅 mediapipe<0.10.20）
        results = self.mp_pose.process(rgb_frame)
        if not results.pose_landmarks:
            return None, 0
        lms = []
        for idx, landmark in enumerate(results.pose_landmarks.landmark):
            lms.append(Landmark(
                name=POSE_LANDMARK_NAMES[idx],
                x=landmark.x,
                y=landmark.y,
                z=landmark.z,
                visibility=float(landmark.visibility) if landmark.visibility is not None else 1.0
            ))
        return lms, 0

    def analyze_frame(self, frame: np.ndarray) -> Optional[Dict]:
        """分析单帧图像，提取姿态关键点"""
        rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        landmarks, _ = self._detect_landmarks(rgb_frame)
        if landmarks is None:
            return None
        
        analysis = self._calculate_metrics(landmarks, frame.shape)
        analysis['landmarks'] = landmarks  # 保留关键点供标注使用
        return analysis
    
    def _calculate_metrics(self, landmarks: List[Landmark], frame_shape: Tuple) -> Dict:
        """计算关键跑步指标"""
        width, height = frame_shape[1], frame_shape[0]
        
        # 提取关键骨骼点
        points = {lm.name: lm for lm in landmarks}
        
        metrics = {}
        
        # 1. 膝盖弯曲角度
        left_knee_angle = self._calculate_angle(
            points['LEFT_HIP'], points['LEFT_KNEE'], points['LEFT_ANKLE']
        )
        right_knee_angle = self._calculate_angle(
            points['RIGHT_HIP'], points['RIGHT_KNEE'], points['RIGHT_ANKLE']
        )
        metrics['left_knee_angle'] = left_knee_angle
        metrics['right_knee_angle'] = right_knee_angle
        metrics['knee_flexion'] = (left_knee_angle + right_knee_angle) / 2
        
        # 2. 小腿角度 (地面夹角)
        left_shin_angle = self._calculate_angle_with_ground(
            points['LEFT_KNEE'], points['LEFT_ANKLE'], width, height
        )
        right_shin_angle = self._calculate_angle_with_ground(
            points['RIGHT_KNEE'], points['RIGHT_ANKLE'], width, height
        )
        metrics['left_shin_angle'] = left_shin_angle
        metrics['right_shin_angle'] = right_shin_angle
        metrics['shin_angle'] = (left_shin_angle + right_shin_angle) / 2
        # 带符号小腿角（区分前倾/后倾，供跨步/刹车步判定）
        metrics['left_shin_signed'] = self._calculate_shin_angle_signed(
            points['LEFT_KNEE'], points['LEFT_ANKLE'], width, height
        )
        metrics['right_shin_signed'] = self._calculate_shin_angle_signed(
            points['RIGHT_KNEE'], points['RIGHT_ANKLE'], width, height
        )
        
        # 3. 跨步分析 (落脚点相对于重心位置)
        metrics['stride_analysis'] = self._analyze_stride(points, width)
        
        # 4. 身体前倾角度
        metrics['body_leang'] = self._calculate_body_leang(points)
        
        # 5. 身体框架对称性
        metrics['symmetry'] = self._calculate_symmetry(points)

        # 6. 跑步阶段识别（用于关键帧截图标注）
        metrics['running_phase'] = self._detect_running_phase(points, metrics)

        return metrics

    def _detect_running_phase(self, points: Dict, metrics: Dict) -> str:
        """基于小腿角度与膝盖弯曲粗略识别跑步阶段

        返回: heel_strike / mid_stance / toe_off / swing
        """
        shin = metrics.get('shin_angle', 90)
        knee = metrics.get('knee_flexion', 180)

        # 支撑期：小腿较竖直（触地/承重），膝盖弯曲较大
        if shin >= 70 and knee <= 165:
            if shin < 78:
                return 'heel_strike'   # 脚跟刚触地
            if knee < 150:
                return 'mid_stance'    # 承重中期
            return 'toe_off'           # 蹬离前
        # 摆动期：小腿后摆/前摆，膝盖大幅弯曲
        return 'swing'
    
    def _calculate_angle(self, a: Landmark, b: Landmark, c: Landmark) -> float:
        """计算三点角度 (a-b-c)"""
        va = np.array([a.x - b.x, a.y - b.y])
        vc = np.array([c.x - b.x, c.y - b.y])
        
        dot_product = np.dot(va, vc)
        norm_a = np.linalg.norm(va)
        norm_c = np.linalg.norm(vc)
        
        if norm_a == 0 or norm_c == 0:
            return 0.0
        
        cos_angle = dot_product / (norm_a * norm_c)
        cos_angle = np.clip(cos_angle, -1.0, 1.0)
        
        return math.degrees(math.acos(cos_angle))
    
    def _calculate_angle_with_ground(self, knee: Landmark, ankle: Landmark, width: int, height: int) -> float:
        """计算小腿与地面夹角（小腿段 knee->ankle 与水平地面的夹角）

        小腿竖直时约 90°，向前倾斜（脚跟落地/跨步）时角度变小。
        直接用膝-踝向量计算，不依赖图像底部是否为地面，更稳健。
        """
        knee_px = knee.to_pixel(width, height)
        ankle_px = ankle.to_pixel(width, height)
        dx = ankle_px[0] - knee_px[0]
        dy = ankle_px[1] - knee_px[1]  # 像素 y 向下

        if dx == 0 and dy == 0:
            return 0.0

        # 与水平地面的夹角（取绝对值，竖直=90°，水平=0°）
        return math.degrees(math.atan2(abs(dy), abs(dx)))

    def _calculate_shin_angle_signed(self, knee: Landmark, ankle: Landmark, width: int, height: int) -> float:
        """带符号的小腿与地面夹角。

        约定：x 越小越靠前（跑者朝画面左侧跑）。
        返回角度 ∈ (-180, 180]：
          正值 = 踝在膝前方（小腿前倾，脚跟触地/正常承重）
          负值 = 踝在膝后方（小腿后倾，蹬离/刹车步）
          绝对值 = 与地面夹角（竖直≈90°）
        """
        knee_px = knee.to_pixel(width, height)
        ankle_px = ankle.to_pixel(width, height)
        dx = ankle_px[0] - knee_px[0]   # >0 踝在膝右（后），<0 踝在膝左（前）
        dy = ankle_px[1] - knee_px[1]   # 像素 y 向下，踝在膝下 dy>0
        if dx == 0 and dy == 0:
            return 0.0
        mag = math.degrees(math.atan2(abs(dy), abs(dx)))
        sign = 1.0 if dx < 0 else -1.0  # 踝在前(左)为正
        return sign * mag
    
    def _analyze_stride(self, points: Dict, width: int) -> Dict:
        """分析跨步情况"""
        # 估算身体重心 (双脚髋部中点)
        center_x = (points['LEFT_HIP'].x + points['RIGHT_HIP'].x) / 2
        
        # 估算落地脚位置 (取更靠前的脚)
        left_ankle_x = points['LEFT_ANKLE'].x
        right_ankle_x = points['RIGHT_ANKLE'].x
        
        forward_ankle_x = min(left_ankle_x, right_ankle_x)  # x越小越靠前
        
        # 跨步程度 (落脚点相对于重心)
        stride_distance = abs(forward_ankle_x - center_x)
        stride_level = '轻度跨步' if stride_distance < 0.05 else '中度跨步' if stride_distance < 0.1 else '严重跨步'
        
        return {
            'stride_distance': stride_distance,
            'stride_level': stride_level,
            'center_of_mass_x': center_x,
            'landing_foot_x': forward_ankle_x
        }
    
    def _calculate_body_leang(self, points: Dict) -> float:
        """计算身体前倾角度"""
        hip_x = (points['LEFT_HIP'].x + points['RIGHT_HIP'].x) / 2
        shoulder_x = (points['LEFT_SHOULDER'].x + points['RIGHT_SHOULDER'].x) / 2
        
        # 前倾角度 (身体垂直线与重力线夹角)
        vertical_line_x = (hip_x + shoulder_x) / 2
        horizontal_offset = abs(shoulder_x - hip_x)
        
        # 估算前倾角度
        leang_angle = math.degrees(math.atan2(horizontal_offset, 0.5))  # 假设身体高度为0.5
        
        return leang_angle
    
    def _calculate_symmetry(self, points: Dict) -> Dict:
        """计算身体框架对称性"""
        # 膝盖重合度 (双膝间距)
        knee_distance = abs(points['LEFT_KNEE'].x - points['RIGHT_KNEE'].x)
        
        # 肩膀对齐度
        shoulder_distance = abs(points['LEFT_SHOULDER'].x - points['RIGHT_SHOULDER'].x)
        
        symmetry_score = 100 - (knee_distance * 100)  # 简单评分
        
        return {
            'knee_distance': knee_distance,
            'shoulder_distance': shoulder_distance,
            'symmetry_score': max(0, min(100, symmetry_score)),
            'symmetry_level': '良好' if symmetry_score > 80 else '一般' if symmetry_score > 60 else '较差'
        }
    
    def process_video(self, video_path: str) -> List[Dict]:
        """处理整个视频，返回每一帧的分析结果"""
        cap = cv2.VideoCapture(video_path)
        
        if not cap.isOpened():
            raise ValueError(f"无法打开视频文件: {video_path}")

        # Tasks VIDEO 模式：按实际帧率设置时间戳步长，并重置时间戳
        if self._landmarker is not None:
            fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
            self._frame_interval_ms = max(1, int(1000.0 / fps)) if fps > 0 else 33
            self._video_ts_ms = 0

        results = []
        frame_count = 0
        
        while True:
            ret, frame = cap.read()
            if not ret:
                break
            
            analysis = self.analyze_frame(frame)
            if analysis:
                analysis['frame_number'] = frame_count
                analysis['frame_index'] = frame_count
                results.append(analysis)
            
            frame_count += 1
            
            # 每10帧处理一次，提高效率
            if frame_count % 10 != 0:
                continue
        
        cap.release()
        return results
    
    def process_video_with_keyframes(self, video_path: str, 
                                     num_keyframes: int = 5) -> Dict:
        """处理视频并提取关键帧（增强版）"""
        # 基础分析
        results = self.process_video(video_path)
        
        # 关键帧分析
        key_frames = self.frame_analyzer.extract_key_frames(video_path, num_keyframes)
        
        # 标注关键帧
        annotations_dir = os.path.join(os.path.dirname(video_path), 'annotations')
        self.frame_analyzer.save_annotations(annotations_dir, key_frames, results)
        
        return {
            'analysis_results': results,
            'key_frames': key_frames,
            'annotations_dir': annotations_dir
        }
    
    def compare_historical_data(self, video_path: str, 
                               history_db: str = 'history.json') -> Dict:
        """对比历史分析数据"""
        # 当前分析
        current_results = self.process_video(video_path)
        
        # 加载历史记录
        history_file = Path(history_db)
        history = {}
        if history_file.exists():
            with open(history_file, 'r', encoding='utf-8') as f:
                history = json.load(f)
        
        # 添加新记录
        record_id = datetime.now().strftime('%Y%m%d_%H%M%S')
        current_metrics = self._calculate_comprehensive_metrics(current_results)
        
        history[record_id] = {
            'timestamp': datetime.now().isoformat(),
            'metrics': current_metrics
        }
        
        # 保存历史
        with open(history_file, 'w', encoding='utf-8') as f:
            json.dump(history, f, ensure_ascii=False, indent=2, default=str)
        
        return {
            'current': current_metrics,
            'history': history,
            'record_id': record_id
        }
    
    def _calculate_comprehensive_metrics(self, analysis_results: list) -> dict:
        """计算综合指标"""
        if not analysis_results:
            return {}
        
        # 计算平均值
        avg_knee = np.mean([r.get('knee_flexion', 0) for r in analysis_results])
        avg_shin = np.mean([r.get('shin_angle', 0) for r in analysis_results])
        
        # 获取最新的跨步分析
        latest_stride = analysis_results[-1].get('stride_analysis', {})
        latest_symmetry = analysis_results[-1].get('symmetry', {})
        
        return {
            'avg_knee_flexion': avg_knee,
            'avg_shin_angle': avg_shin,
            'stride_analysis': latest_stride,
            'symmetry': latest_symmetry
        }
    
    def close(self):
        """释放资源"""
        if self._landmarker is not None:
            try:
                self._landmarker.close()
            except Exception:
                pass
            self._landmarker = None
        if getattr(self, 'mp_pose', None) is not None:
            self.mp_pose.close()


if __name__ == '__main__':
    import sys
    
    if len(sys.argv) < 2:
        print("用法: python pose_analyzer.py <视频文件路径>")
        print("可选: --keyframes <关键帧数> --history <历史记录文件>")
        sys.exit(1)
    
    video_path = sys.argv[1]
    keyframes = 5
    history_db = 'history.json'
    
    # 解析可选参数
    for arg in sys.argv[2:]:
        if arg == '--keyframes' and len(sys.argv) > sys.argv.index(arg) + 1:
            keyframes = int(sys.argv[sys.argv.index(arg) + 1])
        elif arg == '--history' and len(sys.argv) > sys.argv.index(arg) + 1:
            history_db = sys.argv[sys.argv.index(arg) + 1]
    
    analyzer = PoseAnalyzer()
    
    try:
        results = analyzer.process_video(video_path)
        print(f"处理完成! 共分析 {len(results)} 帧")
        
        # 输出关键指标
        if results:
            avg_knee = np.mean([r['knee_flexion'] for r in results])
            avg_shin = np.mean([r['shin_angle'] for r in results])
            print(f"平均膝盖弯曲角度: {avg_knee:.1f}°")
            print(f"平均小腿角度: {avg_shin:.1f}°")
            print(f"跨步情况: {results[-1]['stride_analysis']['stride_level']}")
            print(f"身体对称性: {results[-1]['symmetry']['symmetry_level']}")
        
        # 保存历史
        analyzer.compare_historical_data(video_path, history_db)
        print(f"历史数据已保存到: {history_db}")
    
    finally:
        analyzer.close()