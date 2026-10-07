"""
使用示例 - 演示如何使用PoseRun Analyzer进行跑姿分析
"""

import os
import sys
from pathlib import Path

# 添加项目根目录到路径
sys.path.insert(0, str(Path(__file__).parent))

from pose_analyzer import PoseAnalyzer
from report_generator import ReportGenerator


def analyze_video_file(video_path: str, user_name: str = "用户", coach_name: str = "系统"):
    """
    完整的视频分析流程示例
    
    Args:
        video_path: 视频文件路径
        user_name: 用户姓名
        coach_name: 评估教练名称
    """
    print("=" * 50)
    print("PoseRun 跑姿分析系统")
    print("=" * 50)
    
    # 检查视频文件是否存在
    if not os.path.exists(video_path):
        print(f"错误: 视频文件不存在: {video_path}")
        return
    
    # 创建姿态分析器
    print("\n[1/3] 初始化姿态分析器...")
    analyzer = PoseAnalyzer()
    
    try:
        # 处理视频
        print(f"[2/3] 处理视频: {os.path.basename(video_path)}")
        analysis_results = analyzer.process_video(video_path)
        
        if not analysis_results:
            print("错误: 未能识别到人体姿态")
            return
        
        print(f"成功分析 {len(analysis_results)} 帧")
        
        # 计算综合指标
        latest = analysis_results[-1]
        print("\n[3/3] 分析结果:")
        print("-" * 30)
        print(f"平均膝盖弯曲角度: {latest.get('knee_flexion', 0):.1f}°")
        print(f"小腿角度: {latest.get('shin_angle', 0):.1f}°")
        print(f"跨步程度: {latest.get('stride_analysis', {}).get('stride_level', '未知')}")
        print(f"身体对称性: {latest.get('symmetry', {}).get('symmetry_level', '未知')}")
        print(f"身体前倾角度: {latest.get('body_leang', 0):.1f}°")
        
        # 生成报告
        print("\n" + "=" * 50)
        print("生成分析报告...")
        
        generator = ReportGenerator()
        user_info = {
            'name': user_name,
            'coach': coach_name,
            'score': 75  # 可以根据实际指标计算
        }
        
        output_file = generator.generate_report(
            video_path,
            user_info,
            analysis_results
        )
        
        print(f"报告已生成: {output_file}")
        
        # 如果有pdf转换工具，可以生成PDF
        # generator.export_to_pdf(output_file)
        
    finally:
        analyzer.close()
    
    print("\n分析完成!")


def demo_with_sample_data():
    """使用示例数据演示报告生成"""
    print("=" * 50)
    print("PoseRun 跑姿分析系统 (示例模式)")
    print("=" * 50)
    
    # 创建示例分析结果
    sample_results = [
        {
            'knee_flexion': 135.5,
            'left_knee_angle': 140.2,
            'right_knee_angle': 130.8,
            'shin_angle': 68.2,
            'left_shin_angle': 70.1,
            'right_shin_angle': 66.3,
            'stride_analysis': {
                'stride_distance': 0.08,
                'stride_level': '中度跨步',
                'center_of_mass_x': 0.5,
                'landing_foot_x': 0.42
            },
            'symmetry': {
                'knee_distance': 0.05,
                'shoulder_distance': 0.1,
                'symmetry_score': 95,
                'symmetry_level': '良好'
            },
            'body_leang': 8.5,
            'problems': [
                '存在中度跨步，脚落实在身体重心前',
                '离地有推蹬，影响拉起速度'
            ],
            'suggestions': [
                '注意落地收紧身体框架',
                '加快上拉，落地脚踝放松',
                '避免主动跨步和推蹬',
                '跑动中体会重心带动身体向前的知觉'
            ]
        }
    ]
    
    user_info = {
        'name': '测试用户',
        'coach': '系统自动评估',
        'score': 75
    }
    
    # 生成报告
    generator = ReportGenerator()
    output_file = generator.generate_report(
        'demo_video.mp4',
        user_info,
        sample_results,
        '测试报告.docx'
    )
    
    print(f"\n报告已生成: {output_file}")
    print("\n报告内容预览:")
    print("-" * 30)
    
    # 读取并显示报告部分
    from docx import Document
    doc = Document(output_file)
    
    print("\n【标题】")
    for para in doc.paragraphs[:3]:
        if para.text.strip():
            print(f"  {para.text}")
    
    print("\n【基本信息】")
    for table in doc.tables[:1]:
        for row in table.rows:
            cells = [cell.text for cell in row.cells]
            if cells[0]:
                print(f"  {cells[0]}: {cells[1]}")
    
    print("\n【技术评分】")
    if len(doc.tables) > 1:
        tech_table = doc.tables[1]
        for i, row in enumerate(tech_table.rows):
            cells = [cell.text for cell in row.cells]
            if i > 0 and cells[0]:  # 跳过表头
                print(f"  {cells[0]} - {cells[1]}: {cells[2]}")


if __name__ == '__main__':
    import argparse
    
    parser = argparse.ArgumentParser(description='PoseRun 跑姿分析系统')
    parser.add_argument('video', nargs='?', help='视频文件路径（可选，不填则使用示例数据）')
    parser.add_argument('--name', default='用户', help='用户姓名')
    parser.add_argument('--coach', default='系统', help='评估教练名称')
    parser.add_argument('--demo', action='store_true', help='使用示例数据')
    
    args = parser.parse_args()
    
    if args.demo or not args.video:
        demo_with_sample_data()
    else:
        analyze_video_file(args.video, args.name, args.coach)