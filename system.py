"""
PoseRun 系统集成器和启动脚本
提供统一的启动入口和系统管理功能
"""

import os
import sys
import subprocess
from pathlib import Path
import argparse
import time


def install_dependencies():
    """安装必要的Python依赖"""
    print("检查并安装Python依赖...")
    
    try:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "-r", "requirements.txt"])
        print("✅ Python依赖安装完成")
        return True
    except subprocess.CalledProcessError as e:
        print(f"❌ 依赖安装失败: {e}")
        return False


def check_environment():
    """检查运行环境"""
    print("\n=== 环境检查 ===")
    
    # 检查Python版本
    python_version = sys.version_info
    if python_version.major >= 3 and python_version.minor >= 8:
        print(f"✅ Python版本: {python_version.major}.{python_version.minor}.{python_version.micro}")
    else:
        print(f"⚠️ 建议Python 3.8+, 当前: {python_version.major}.{python_version.minor}.{python_version.micro}")
    
    # 检查关键模块
    modules = ['cv2', 'mediapipe', 'numpy', 'flask']
    missing_modules = []
    
    for module in modules:
        try:
            __import__(module)
            print(f"✅ {module}")
        except ImportError:
            missing_modules.append(module)
            print(f"❌ {module} 未安装")
    
    return len(missing_modules) == 0


def create_directories():
    """创建必要的目录"""
    dirs = ['uploads', 'reports', 'keyframes']
    for dir_name in dirs:
        os.makedirs(dir_name, exist_ok=True)
        print(f"✅ {dir_name}/ 目录已创建")


def start_web_server(port: int = 5000, debug: bool = False):
    """启动Web服务器"""
    print(f"\n启动PoseRun分析系统Web服务...")
    print(f"访问地址: http://localhost:{port}")
    
    try:
        import app
        app.app.run(host='0.0.0.0', port=port, debug=debug)
    except Exception as e:
        print(f"❌ 启动失败: {e}")
        sys.exit(1)


def run_realtime_demo():
    """运行实时演示"""
    print("\n=== 实时跑姿分析演示 ===")
    print("这将使用摄像头进行实时姿态识别")
    print("按 'q' 键退出实时分析")
    
    try:
        from pose_analyzer import PoseAnalyzer
        from realtime_analyzer import RealTimeAnalyzer
        
        analyzer = PoseAnalyzer()
        realtime = RealTimeAnalyzer(analyzer)
        realtime.start()
        
    except Exception as e:
        print(f"❌ 实时分析启动失败: {e}")
        print("请确保已正确安装相关依赖")


def run_batch_process(directory: str = None, video_files: list = None):
    """运行批量处理"""
    from batch_processor import BatchProcessor
    
    processor = BatchProcessor()
    
    if directory:
        return processor.process_directory(directory)
    elif video_files:
        return processor.process_batch(video_files)
    else:
        print("❌ 请提供视频目录或文件列表")
        return None


def print_help():
    """打印帮助信息"""
    print("""
PoseRun 跑姿分析系统 v2.0.0

用法:
    python system.py [命令] [选项]

命令:
    install       安装依赖
    check         检查环境
    server        启动Web服务
    realtime      启动实时分析
    batch         批量处理视频
    full          完整系统设置

选项:
    -p, --port PORT      服务器端口 (默认: 5000)
    -d, --debug          开启调试模式
    -c, --camera ID      摄像头设备ID (默认: 0)
    -v, --videos FILES   视频文件路径
    -D, --dir DIRECTORY  视频目录路径

示例:
    python system.py install          安装依赖
    python system.py server -p 8080   启动Web服务
    python system.py realtime -c 0    启动实时分析
    python system.py batch -D ./videos 批量处理视频
    python system.py full            完整系统设置
    """)


def main():
    """主函数"""
    parser = argparse.ArgumentParser(description='PoseRun 跑姿分析系统', add_help=False)
    parser.add_argument('command', nargs='?', default='help',
                       choices=['install', 'check', 'server', 'realtime', 'batch', 'full', 'help'],
                       help='要执行的命令')
    parser.add_argument('-p', '--port', type=int, default=5000, help='服务器端口')
    parser.add_argument('-d', '--debug', action='store_true', help='调试模式')
    parser.add_argument('-c', '--camera', type=int, default=0, help='摄像头设备ID')
    parser.add_argument('-v', '--videos', nargs='+', help='视频文件路径')
    parser.add_argument('-D', '--dir', help='视频目录路径')
    parser.add_argument('-h', '--help', action='store_true', help='显示帮助信息')
    
    args = parser.parse_args()
    
    if args.help or args.command == 'help':
        print_help()
        return
    
    if args.command == 'install':
        success = install_dependencies()
        sys.exit(0 if success else 1)
    
    elif args.command == 'check':
        success = check_environment()
        sys.exit(0 if success else 1)
    
    elif args.command == 'server':
        create_directories()
        start_web_server(args.port, args.debug)
    
    elif args.command == 'realtime':
        create_directories()
        run_realtime_demo()
    
    elif args.command == 'batch':
        create_directories()
        result = run_batch_process(args.dir, args.videos)
        if result:
            print(f"总耗时: {result['processing_time']}秒")
            print(f"成功: {result['successful']}, 失败: {result['failed']}")
    
    elif args.command == 'full':
        print("=== 完整系统设置 ===")
        success = install_dependencies() and check_environment()
        if success:
            create_directories()
            print("✅ 系统设置完成!")
            print("\n可以使用以下命令:")
            print("  python system.py server -p 5000   启动Web服务")
            print("  python system.py realtime -c 0    启动实时分析")
            print("  python system.py batch -D ./videos 批量处理视频")
        else:
            print("❌ 系统设置失败，请检查错误信息")


if __name__ == '__main__':
    main()