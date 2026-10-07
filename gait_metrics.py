"""
PoseRun Analyzer - 步态指标 + 视频质量检查（应用层封装）
- compute_gait(video_path)：跑一次姿态分析并计算步态指标（带内存缓存，避免重复跑视频）
- check_video_quality(video_path)：时长/帧数/有效帧比例/视角提示（借鉴同类项目）
"""

import os
import threading

from gait_analyzer import GaitAnalyzer

# 质量检查阈值
MIN_DURATION_SEC = 3.0
MAX_DURATION_SEC = 120.0
MIN_FRAMES = 20
MIN_VALID_FRAME_RATIO = 0.5

# 步态计算缓存：video_path -> {'fps':..., 'gait':...}（同一次会话内复用）
_gait_cache = {}
_cache_lock = threading.Lock()


def compute_gait(video_path, use_cache=True):
    """对视频计算步态指标。返回 {'gait': dict|None, 'fps': float, 'frames': int, 'duration': float}
    姿态识别失败或数据不足时 gait 为 None。"""
    if use_cache:
        with _cache_lock:
            hit = _gait_cache.get(video_path)
        if hit:
            return hit

    from pose_analyzer import PoseAnalyzer
    import cv2

    cap = cv2.VideoCapture(video_path)
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    cap.release()
    duration = total_frames / fps if fps > 0 else 0.0

    gait = None
    frames_used = 0
    try:
        analyzer = PoseAnalyzer()
        try:
            results = analyzer.process_video(video_path)
        finally:
            analyzer.close()
        frames_used = len(results)
        if results:
            gait = GaitAnalyzer(fps=fps).analyze(results)
    except Exception as e:
        print(f'[gait] 步态计算失败: {e}')
        gait = None

    out = {'gait': gait, 'fps': round(fps, 1),
           'frames': frames_used, 'duration': round(duration, 1)}
    with _cache_lock:
        _gait_cache[video_path] = out
        # 限制缓存大小
        if len(_gait_cache) > 20:
            _gait_cache.pop(next(iter(_gait_cache)))
    return out


def check_video_quality(video_path):
    """视频质量检查。返回 {'ok': bool, 'warnings': [str], 'reasons': [str], 'retake_tips': [str]}"""
    import cv2
    warnings, reasons, retake_tips = [], [], []
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        return {'ok': False, 'warnings': ['无法打开视频'], 'reasons': ['文件损坏或格式不支持'],
                'retake_tips': ['请重新录制或换一个视频文件']}
    fps = cap.get(cv2.CAP_PROP_FPS) or 0
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    duration = total_frames / fps if fps > 0 else 0.0

    if duration < MIN_DURATION_SEC:
        reasons.append(f'视频时长过短（{duration:.1f}s < {MIN_DURATION_SEC}s）')
        retake_tips.append('请拍摄至少 3 秒的连续跑步视频')
    elif duration > MAX_DURATION_SEC:
        warnings.append(f'视频较长（{duration:.0f}s），分析将取前 {MAX_DURATION_SEC:.0f}s')
        retake_tips.append('建议截取 10-30 秒的稳定跑步片段，分析更准')

    if total_frames < MIN_FRAMES:
        reasons.append(f'帧数过少（{total_frames} < {MIN_FRAMES}）')
        retake_tips.append('提高拍摄帧率或延长视频')

    if fps and fps < 15:
        warnings.append(f'帧率偏低（{fps:.0f}fps），步频/振荡指标可能不准')
        retake_tips.append('建议 30fps 以上拍摄')

    # 有效帧比例（抽样检测人体可见性）
    valid_ratio = None
    sample = min(total_frames, 60)
    if sample > 0 and fps > 0:
        from pose_analyzer import PoseAnalyzer
        try:
            analyzer = PoseAnalyzer()
            valid = 0
            step = max(1, total_frames // sample)
            for i in range(0, total_frames, step):
                cap.set(cv2.CAP_PROP_POS_FRAMES, i)
                ret, frame = cap.read()
                if not ret:
                    continue
                if analyzer.analyze_frame(frame):
                    valid += 1
            analyzer.close()
            checked = min(sample, total_frames // step)
            valid_ratio = valid / checked if checked else 0.0
        except Exception as e:
            print(f'[quality] 有效帧检测失败: {e}')
        finally:
            cap.release()
    else:
        cap.release()

    if valid_ratio is not None and valid_ratio < MIN_VALID_FRAME_RATIO:
        reasons.append(f'人体可见比例过低（{valid_ratio:.0%}）')
        retake_tips.append('确保全程拍到跑者全身（侧面拍摄最佳）')
    elif valid_ratio is not None:
        warnings.append(f'有效帧比例 {valid_ratio:.0%}')

    return {
        'ok': not reasons,
        'duration': round(duration, 1),
        'fps': round(fps, 1),
        'total_frames': total_frames,
        'valid_frame_ratio': round(valid_ratio, 2) if valid_ratio is not None else None,
        'warnings': warnings,
        'reasons': reasons,
        'retake_tips': retake_tips,
    }
