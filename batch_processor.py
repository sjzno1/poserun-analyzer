"""
PoseRun 批量处理模块
支持多视频文件批量分析处理
"""

import os
import glob
from pathlib import Path
from typing import List, Dict, Optional
from dataclasses import dataclass, field
from concurrent.futures import ThreadPoolExecutor, as_completed
import time
from datetime import datetime

from pose_analyzer import PoseAnalyzer, KeyFrameAnalyzer
from report_generator import ReportGenerator


@dataclass
class BatchResult:
    """批量处理单个结果"""
    filename: str
    success: bool
    report_path: str = ""
    keyframes_dir: str = ""
    error: str = ""
    processing_time: float = 0
    metrics: Dict = field(default_factory=dict)


class BatchProcessor:
    """批量视频处理器"""
    
    def __init__(self, max_workers: int = 3):
        self.max_workers = max_workers
        self.upload_folder = 'uploads'
        self.reports_folder = 'reports'
        self.keyframes_folder = 'keyframes'
        
        # 确保目录存在
        for folder in [self.upload_folder, self.reports_folder, self.keyframes_folder]:
            os.makedirs(folder, exist_ok=True)
    
    def process_batch(self, video_files: List[str], user_info: dict = None, 
                     num_keyframes: int = 5) -> Dict:
        """
        批量处理视频文件
        
        Args:
            video_files: 视频文件路径列表
            user_info: 用户信息
            num_keyframes: 关键帧数量
        
        Returns:
            批量处理结果
        """
        if user_info is None:
            user_info = {}
        
        # 搜索视频文件
        if len(video_files) == 1 and os.path.isdir(video_files[0]):
            # 如果是目录，搜索其中的视频文件
            extensions = ['*.mp4', '*.avi', '*.mov', '*.mkv', '*.webm']
            video_files = []
            for ext in extensions:
                video_files.extend(glob.glob(os.path.join(video_files[0], ext)))
                video_files.extend(glob.glob(os.path.join(video_files[0], ext.upper())))
        
        print(f"开始批量处理 {len(video_files)} 个视频文件...")
        
        start_time = time.time()
        results = []
        success_count = 0
        fail_count = 0
        
        # 线程池处理
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            futures = {}
            for i, video_file in enumerate(video_files):
                future = executor.submit(
                    self._process_single_video,
                    video_file, user_info, num_keyframes
                )
                futures[future] = video_file
            
            for future in as_completed(futures):
                video_file = futures[future]
                try:
                    result = future.result()
                    results.append(result)
                    if result.success:
                        success_count += 1
                    else:
                        fail_count += 1
                except Exception as e:
                    print(f"处理 {os.path.basename(video_file)} 失败: {str(e)}")
                    fail_count += 1
                    results.append(BatchResult(
                        filename=os.path.basename(video_file),
                        success=False,
                        error=str(e)
                    ))
        
        elapsed_time = time.time() - start_time
        
        return {
            'total': len(video_files),
            'successful': success_count,
            'failed': fail_count,
            'processing_time': round(elapsed_time, 2),
            'results': results,
            'average_time_per_video': round(elapsed_time / len(video_files), 2) if video_files else 0
        }
    
    def _process_single_video(self, video_file: str, user_info: dict, 
                            num_keyframes: int = 5) -> BatchResult:
        """处理单个视频文件"""
        start_time = time.time()
        filename = os.path.basename(video_file)
        
        try:
            # 读取文件到uploads目录
            import shutil
            dest_path = os.path.join(self.upload_folder, 
                                   f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{filename}")
            
            if not os.path.exists(video_file):
                return BatchResult(
                    filename=filename,
                    success=False,
                    error=f"文件不存在: {video_file}"
                )
            
            shutil.copy2(video_file, dest_path)
            
            # 姿态分析
            analyzer = PoseAnalyzer()
            try:
                analysis_results = analyzer.process_video(dest_path)
            finally:
                analyzer.close()
            
            if not analysis_results:
                return BatchResult(
                    filename=filename,
                    success=False,
                    error="未能识别到人体姿态"
                )
            
            # 先提取并保存关键帧标注截图（报告需要嵌入这些截图）
            keyframe_analyzer = KeyFrameAnalyzer()
            key_frames = keyframe_analyzer.extract_key_frames(dest_path, num_keyframes)

            unique_id = f"kf_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{filename[:20]}"
            keyframes_dir = os.path.join(self.keyframes_folder, unique_id)

            if key_frames:
                keyframe_analyzer.save_annotations(
                    keyframes_dir, key_frames, analysis_results)

            # 生成报告（Word格式，嵌入关键帧标注截图）
            generator = ReportGenerator()
            report_path = generator.generate_report(
                dest_path,
                user_info,
                analysis_results,
                key_frames_dir=keyframes_dir
            )

            # 清理临时文件
            if os.path.exists(dest_path):
                os.remove(dest_path)
            
            elapsed = time.time() - start_time
            
            return BatchResult(
                filename=filename,
                success=True,
                report_path=report_path,
                keyframes_dir=unique_id,
                processing_time=round(elapsed, 2),
                metrics={
                    'total_frames': len(analysis_results),
                    'metrics': analysis_results[-1] if analysis_results else {}
                }
            )
        
        except Exception as e:
            elapsed = time.time() - start_time
            return BatchResult(
                filename=filename,
                success=False,
                error=str(e),
                processing_time=round(elapsed, 2)
            )
    
    def process_directory(self, directory: str, user_info: dict = None, 
                        num_keyframes: int = 5) -> Dict:
        """处理整个目录下的视频文件"""
        return self.process_batch([directory], user_info, num_keyframes)


if __name__ == '__main__':
    import sys
    
    if len(sys.argv) < 2:
        print("用法: python batch_processor.py <视频目录或文件列表>")
        print("示例: python batch_processor.py /path/to/videos/")
        sys.exit(1)
    
    processor = BatchProcessor()
    
    video_paths = sys.argv[1:]
    user_info = {'name': '批量处理用户'}
    
    result = processor.process_batch(video_paths, user_info)
    
    print("\n=== 批量处理结果 ===")
    print(f"总数: {result['total']}")
    print(f"成功: {result['successful']}")
    print(f"失败: {result['failed']}")
    print(f"总耗时: {result['processing_time']}秒")
    print(f"平均每视频: {result['average_time_per_video']}秒")
    
    for r in result['results']:
        status = "✅" if r.success else "❌"
        print(f"{status} {r.filename}: {r.processing_time}秒")
        if not r.success:
            print(f"   错误: {r.error}")