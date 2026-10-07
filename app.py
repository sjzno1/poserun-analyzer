"""
PoseRun Analyzer API - Flask API接口
提供视频上传、姿态分析和报告生成的Web服务
支持关键帧标注、历史对比、批量处理等高级功能
"""

import os
import uuid
import json
from pathlib import Path
from flask import Flask, request, jsonify, send_file, send_from_directory, g
from flask_cors import CORS
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from werkzeug.utils import secure_filename
import shutil
import zipfile
import io
from datetime import datetime

from pose_analyzer import PoseAnalyzer, KeyFrameAnalyzer
import pose_analyzer as _pose_analyzer_mod
from report_generator import ReportGenerator
from auth import create_auth_manager
from history import HistoryStore

# 创建Flask应用
app = Flask(__name__, static_folder='static', static_url_path='')
CORS(app)  # 启用跨域支持

# 请求限流：按登录用户限（未登录回退到 IP），保护 CPU 密集的分析接口
def _rate_limit_key():
    try:
        return f"user:{g.current_user['id']}"
    except (RuntimeError, KeyError, TypeError):
        return f"ip:{get_remote_address()}"


# 认证与人员管理（SQLite 用户库 + 令牌鉴权）
auth_manager = create_auth_manager()
auth_manager.attach(app)

# limiter 在 auth 之后注册，限流检查时 g.current_user 已就绪
limiter = Limiter(
    key_func=_rate_limit_key,
    app=app,
    storage_uri="memory://",
    default_limits=[],
)

# 跑姿历史记录（SQLite 历史库 data/history.db，与 auth.db 同卷持久化）
history_store = HistoryStore(os.path.join('data', 'history.db'))


# 级联：删除用户时同时清理其历史记录
_orig_delete_user = auth_manager.delete_user


def _delete_user_with_history(uid):
    err = _orig_delete_user(uid)
    if err is None:
        try:
            n = history_store.delete_by_user(uid)
            if n:
                print(f'[history] 用户 {uid} 已删除，级联清理 {n} 条历史记录')
        except Exception as e:
            print(f'[history] 级联清理用户 {uid} 历史记录失败: {e}')
    return err


auth_manager.delete_user = _delete_user_with_history

# 配置
app.config['MAX_CONTENT_LENGTH'] = 100 * 1024 * 1024  # 最大100MB
ALLOWED_EXTENSIONS = {'mp4', 'avi', 'mov', 'mkv', 'webm'}
UPLOAD_FOLDER = 'uploads'
REPORTS_FOLDER = 'reports'
KEYFRAMES_FOLDER = 'keyframes'
HISTORY_FILE = 'history.json'

# 确保文件夹存在
for folder in [UPLOAD_FOLDER, REPORTS_FOLDER, KEYFRAMES_FOLDER]:
    os.makedirs(folder, exist_ok=True)


def allowed_file(filename):
    """检查文件扩展名是否允许"""
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def current_display_name():
    """当前登录用户的显示名（姓名优先，其次用户名），未登录返回 None"""
    user = getattr(g, 'current_user', None)
    if not user:
        return None
    return user.get('name') or user.get('username') or ''


def get_video_path(filename):
    """获取文件完整路径"""
    return os.path.join(UPLOAD_FOLDER, filename)


def get_report_path(filename):
    """获取报告文件完整路径"""
    return os.path.join(REPORTS_FOLDER, filename)


def get_keyframes_path(filename):
    """获取关键帧目录完整路径"""
    return os.path.join(KEYFRAMES_FOLDER, filename)


def safe_resolve(base_folder: str, filename: str):
    """将 filename 解析到 base_folder 内，防止路径穿越（../ 逃逸）。
    返回 (解析后的绝对路径, 是否合法)。"""
    base = os.path.realpath(base_folder)
    target = os.path.realpath(os.path.join(base, filename))
    if target == base or target.startswith(base + os.sep):
        return target, True
    return target, False


def save_keyframes(key_frames_dir: str, key_frames: list, analysis_results: list):
    """保存关键帧标注截图（含指标面板）"""
    analyzer = KeyFrameAnalyzer()
    return analyzer.save_annotations(key_frames_dir, key_frames, analysis_results)


def build_history_metrics(analysis_results: list, score: int, grade: str,
                          problems: list, suggestions: list, video_path: str = None):
    """从分析结果汇总历史记录指标（三条分析路径共用）。
    若提供 video_path，则计算步态指标（步频/垂直振荡/步幅/左右对称）。"""
    latest = analysis_results[-1]
    m = {
        'avg_knee': round(latest.get('knee_flexion', 0), 1),
        'avg_shin': round(latest.get('shin_angle', 0), 1),
        'stride_level': latest.get('stride_analysis', {}).get('stride_level', '未知'),
        'symmetry_level': latest.get('symmetry', {}).get('symmetry_level', '未知'),
        'body_lean': round(latest.get('body_leang', 0), 1),
        'running_phase': latest.get('running_phase', '未知'),
        'score': score,
        'grade': grade,
        'problems': problems,
        'suggestions': suggestions,
    }
    # 步态指标（步频/垂直振荡/步幅/左右对称）
    if video_path:
        try:
            from gait_metrics import compute_gait
            gait_info = compute_gait(video_path)
            gait = gait_info.get('gait')
            if gait:
                m['gait'] = gait
                m['cadence'] = gait.get('cadence')
        except Exception as e:
            print(f'[gait] 步态指标计算失败: {e}')
    return m


# 视频质量检查结果缓存（避免同一次分析重复跑姿态检测）
_quality_cache = {}


def _video_quality(video_path):
    """视频质量检查（带内存缓存）。失败时返回 None，不影响分析主流程。"""
    try:
        if video_path in _quality_cache:
            return _quality_cache[video_path]
        from gait_metrics import check_video_quality
        q = check_video_quality(video_path)
        _quality_cache[video_path] = q
        if len(_quality_cache) > 20:
            _quality_cache.pop(next(iter(_quality_cache)))
        return q
    except Exception as e:
        print(f'[quality] 视频质量检查失败: {e}')
        return None


def save_history_record(user, video_file, report_file, keyframes_dir,
                        analysis_results, score, grade, problems, suggestions,
                        coach='', video_path=None):
    """把一次成功分析写入历史库（失败不影响分析主流程）"""
    try:
        m = build_history_metrics(analysis_results, score, grade, problems,
                                  suggestions, video_path)
        rec = history_store.add_record(
            user=user,
            video_file=video_file,
            report_file=report_file,
            keyframes_dir=keyframes_dir,
            score=score,
            grade=grade,
            metrics=m,
            problems=problems,
            suggestions=suggestions,
            total_frames=len(analysis_results),
            coach=coach,
        )
        print(f"[history] 已保存记录 {rec['id']} 用户={user.get('username') if user else '匿名'} 得分={score}")
        return rec
    except Exception as e:
        print(f'[history] 保存历史记录失败: {e}')
        return None


def load_history() -> dict:
    """加载历史数据"""
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except:
            return {}
    return {}


def save_history(record_id: str, current_metrics: dict):
    """保存历史数据"""
    history = load_history()
    
    # 添加新记录
    if 'history' not in history:
        history['history'] = []
    
    history['history'].append({
        'record_id': record_id,
        'date': datetime.now().strftime('%Y-%m-%d'),
        'metrics': current_metrics
    })
    
    # 按日期排序，只保留最近20条
    history['history'].sort(key=lambda x: x.get('date', ''), reverse=True)
    history['history'] = history['history'][:20]
    
    # 保存
    with open(HISTORY_FILE, 'w', encoding='utf-8') as f:
        json.dump(history, f, ensure_ascii=False, indent=2, default=str)


@app.route('/', methods=['GET'])
def index():
    """Web 前端 dashboard"""
    return send_from_directory(app.static_folder, 'index.html')


@app.route('/api/health', methods=['GET'])
def health_check():
    """健康检查接口"""
    return jsonify({
        'status': 'healthy',
        'service': 'PoseRun Analyzer',
        'version': '2.5.0',
        'pose_backend': _pose_analyzer_mod.LAST_POSE_BACKEND,
        'features': ['关键帧标注', '历史对比', '批量处理', '跑姿历史记录']
    })


@app.route('/api/upload', methods=['POST'])
@limiter.limit("20 per hour")
def upload_video():
    """
    上传视频文件接口
    
    Returns:
        JSON: 视频信息（文件名、大小、路径）
    """
    if 'video' not in request.files:
        return jsonify({'error': '未找到视频文件'}), 400
    
    file = request.files['video']
    if file.filename == '':
        return jsonify({'error': '未选择视频文件'}), 400
    
    if not allowed_file(file.filename):
        return jsonify({'error': f'不支持的文件格式。支持: {", ".join(ALLOWED_EXTENSIONS)}'}), 400
    
    # 保存文件
    filename = secure_filename(file.filename)
    unique_filename = f"{uuid.uuid4().hex}_{filename}"
    file_path = get_video_path(unique_filename)
    file.save(file_path)
    
    file_size = os.path.getsize(file_path)
    
    return jsonify({
        'success': True,
        'filename': unique_filename,
        'original_name': filename,
        'size_mb': round(file_size / 1024 / 1024, 2),
        'path': file_path
    })


@app.route('/api/video/quality', methods=['POST'])
@limiter.limit("20 per hour")
def video_quality_check():
    """
    视频质量预检接口（上传后、分析前调用）。
    Body: {video_file: 文件名}
    返回时长/帧率/有效帧比例 + 警告 + 重拍建议。
    """
    data = request.get_json(silent=True) or {}
    video_filename = data.get('video_file')
    if not video_filename:
        return jsonify({'error': '请提供 video_file'}), 400
    video_path = get_video_path(video_filename)
    if not os.path.exists(video_path):
        return jsonify({'error': f'视频文件不存在: {video_filename}'}), 404
    q = _video_quality(video_path)
    if q is None:
        return jsonify({'error': '质量检查失败'}), 500
    return jsonify({'success': True, **q})


@app.route('/api/analyze', methods=['POST'])
@limiter.limit("10 per hour")
def analyze_pose():
    """
    分析视频中的跑姿接口
    
    Request Body:
        video_file: 视频文件名（上传后返回的名称）
        user_name: 可选，用户姓名
        coach_name: 可选，评估教练名称
        keyframes: 可选，关键帧数量（默认5）
        report_format: 可选，报告格式 'pdf'（默认）或 'docx'
    
    Returns:
        JSON: 分析结果（关键指标、评分、问题列表、关键帧目录）
    """
    data = request.get_json()
    
    video_filename = data.get('video_file')
    if not video_filename:
        return jsonify({'error': '请提供视频文件名'}), 400
    
    video_path = get_video_path(video_filename)
    if not os.path.exists(video_path):
        return jsonify({'error': f'视频文件不存在: {video_filename}'}), 404
    
    # 构建用户信息（未指定时默认取当前登录用户）
    user_info = {
        'name': data.get('user_name') or current_display_name() or '匿名用户',
        'coach': data.get('coach_name', '系统自动评估'),
        'score': 0  # 稍后计算
    }
    
    # 解析关键帧数量
    num_keyframes = int(data.get('keyframes', 5))
    
    try:
        # 执行姿态分析
        analyzer = PoseAnalyzer()
        
        try:
            analysis_results = analyzer.process_video(video_path)
        finally:
            analyzer.close()
        
        if not analysis_results:
            return jsonify({
                'error': '未能识别到人体姿态，请确保视频中有人跑步'
            }), 400

        # 先提取并保存关键帧标注截图（报告需要嵌入这些截图）
        keyframe_analyzer = KeyFrameAnalyzer()
        key_frames = keyframe_analyzer.extract_key_frames(video_path, num_keyframes)
        unique_id = f"kf_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        keyframes_dir = get_keyframes_path(unique_id)
        save_keyframes(keyframes_dir, key_frames, analysis_results)

        # 生成报告（默认PDF，可选Word，嵌入关键帧截图）
        report_format = (data.get('report_format') or 'pdf').lower()
        if report_format not in ('pdf', 'docx'):
            report_format = 'pdf'
        generator = ReportGenerator()
        report_path = generator.generate_report(
            video_path,
            user_info,
            analysis_results,
            key_frames_dir=keyframes_dir,
            fmt=report_format
        )

        # 构建返回数据
        latest_result = analysis_results[-1]
        # 计算综合得分与问题/建议（与报告口径一致）
        score_info = ReportGenerator()._calculate_score(generator.metrics)
        score = score_info['total']
        problems = generator.metrics.get('problems', [])
        suggestions = generator.metrics.get('suggestions', [])

        # 自动写入历史记录（每位用户历次跑姿入库）
        saved_rec = save_history_record(
            user=getattr(g, 'current_user', None),
            video_file=video_filename,
            report_file=os.path.basename(report_path),
            keyframes_dir=os.path.basename(keyframes_dir),
            analysis_results=analysis_results,
            score=score,
            grade=score_info['grade'],
            problems=problems,
            suggestions=suggestions,
            coach=user_info['coach'],
            video_path=video_path,
        )

        summary = {
            'status': 'success',
            'video_file': video_filename,
            'user_name': user_info['name'],
            'coach_name': user_info['coach'],
            'score': score,
            'grade': score_info['grade'],
            'score_breakdown': [
                {
                    'item': b['item'],
                    'category': b['category'],
                    'result': b['result'],
                    'score': int(b['score']),
                } for b in score_info['breakdown']
            ],
            'total_frames': len(analysis_results),
            'keyframes_extracted': len(key_frames),
            'keyframes_dir': os.path.basename(keyframes_dir),
            'metrics': {
                'avg_knee_flexion': round(latest_result.get('knee_flexion', 0), 1),
                'left_knee_angle': round(latest_result.get('left_knee_angle', 0), 1),
                'right_knee_angle': round(latest_result.get('right_knee_angle', 0), 1),
                'avg_shin_angle': round(latest_result.get('shin_angle', 0), 1),
                'stride_level': latest_result.get('stride_analysis', {}).get('stride_level', '未知'),
                'symmetry_level': latest_result.get('symmetry', {}).get('symmetry_level', '未知'),
                'body_leang': round(latest_result.get('body_leang', 0), 1),
                'running_phase': latest_result.get('running_phase', '未知')
            },
            'gait': (saved_rec or {}).get('metrics', {}).get('gait'),
            'video_quality': _video_quality(video_path),
            'problems': problems,
            'suggestions': suggestions,
            'report_file': os.path.basename(report_path),
            'report_format': report_format,
            'history_record': saved_rec['id'] if saved_rec else None
        }

        return jsonify(summary)
    
    except Exception as e:
        return jsonify({'error': f'分析失败: {str(e)}'}), 500


@app.route('/api/analyze/advanced', methods=['POST'])
@limiter.limit("10 per hour")
def analyze_pose_advanced():
    """
    高级分析接口（支持关键帧、历史对比）
    
    Request Body:
        video_file: 视频文件名
        user_name: 用户姓名
        coach_name: 评估教练名称
        keyframes: 关键帧数量（默认5）
        history: 是否启用历史对比（默认false）
        report_format: 可选，报告格式 'pdf'（默认）或 'docx'
    
    Returns:
        JSON: 完整分析结果（包含历史对比）
    """
    data = request.get_json()
    
    video_filename = data.get('video_file')
    if not video_filename:
        return jsonify({'error': '请提供视频文件名'}), 400
    
    video_path = get_video_path(video_filename)
    if not os.path.exists(video_path):
        return jsonify({'error': f'视频文件不存在: {video_filename}'}), 404
    
    user_info = {
        'name': data.get('user_name') or current_display_name() or '匿名用户',
        'coach': data.get('coach_name', '系统自动评估'),
        'score': 0
    }
    
    num_keyframes = int(data.get('keyframes', 5))
    enable_history = data.get('history', False)
    
    try:
        analyzer = PoseAnalyzer()
        
        try:
            analysis_results = analyzer.process_video(video_path)
        finally:
            analyzer.close()
        
        if not analysis_results:
            return jsonify({'error': '未能识别到人体姿态'}), 400

        # 先提取并保存关键帧标注截图（报告需要嵌入）
        keyframe_analyzer = KeyFrameAnalyzer()
        key_frames = keyframe_analyzer.extract_key_frames(video_path, num_keyframes)
        unique_id = f"kf_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        keyframes_dir = get_keyframes_path(unique_id)
        save_keyframes(keyframes_dir, key_frames, analysis_results)

        # 生成报告（嵌入关键帧截图）
        report_format = (data.get('report_format') or 'pdf').lower()
        if report_format not in ('pdf', 'docx'):
            report_format = 'pdf'
        generator = ReportGenerator()
        report_path = generator.generate_report(
            video_path,
            user_info,
            analysis_results,
            key_frames_dir=keyframes_dir,
            fmt=report_format
        )

        # 计算综合得分与问题/建议（与报告口径一致）
        score_info = ReportGenerator()._calculate_score(generator.metrics)
        score = score_info['total']

        # 自动写入历史记录
        current_user = getattr(g, 'current_user', None)
        saved_rec = save_history_record(
            user=current_user,
            video_file=video_filename,
            report_file=os.path.basename(report_path),
            keyframes_dir=os.path.basename(keyframes_dir),
            analysis_results=analysis_results,
            score=score,
            grade=score_info['grade'],
            problems=generator.metrics.get('problems', []),
            suggestions=generator.metrics.get('suggestions', []),
            coach=user_info['coach'],
            video_path=video_path,
        )

        # 历史对比（来自数据库，按当前用户）
        history_result = None
        if enable_history:
            if current_user:
                recent = history_store.list_for_user(current_user, limit=5)
            else:
                recent = history_store.list_records(username=user_info['name'], limit=5)
            history_result = {
                'total_records': len(recent),
                'records': recent
            }

        # 构建返回数据
        latest_result = analysis_results[-1]
        summary = {
            'status': 'success',
            'video_file': video_filename,
            'user_name': user_info['name'],
            'score': score,
            'grade': score_info['grade'],
            'score_breakdown': [
                {
                    'item': b['item'],
                    'category': b['category'],
                    'result': b['result'],
                    'score': int(b['score']),
                } for b in score_info['breakdown']
            ],
            'total_frames': len(analysis_results),
            'keyframes_dir': os.path.basename(keyframes_dir),
            'metrics': {
                'avg_knee_flexion': round(latest_result.get('knee_flexion', 0), 1),
                'avg_shin_angle': round(latest_result.get('shin_angle', 0), 1),
                'stride_level': latest_result.get('stride_analysis', {}).get('stride_level', '未知'),
                'symmetry_level': latest_result.get('symmetry', {}).get('symmetry_level', '未知'),
                'running_phase': latest_result.get('running_phase', '未知')
            },
            'gait': (saved_rec or {}).get('metrics', {}).get('gait'),
            'video_quality': _video_quality(video_path),
            'problems': generator.metrics.get('problems', []),
            'suggestions': generator.metrics.get('suggestions', []),
            'report_file': os.path.basename(report_path),
            'report_format': report_format,
            'history': history_result,
            'history_record': saved_rec['id'] if saved_rec else None
        }

        return jsonify(summary)
    
    except Exception as e:
        return jsonify({'error': f'分析失败: {str(e)}'}), 500


@app.route('/api/report/<filename>', methods=['GET'])
def get_report(filename):
    """下载报告接口"""
    report_path, ok = safe_resolve(REPORTS_FOLDER, filename)
    if not ok or not os.path.isfile(report_path):
        return jsonify({'error': '报告文件不存在'}), 404

    return send_file(
        report_path,
        as_attachment=True,
        download_name=filename
    )


@app.route('/api/keyframes/<path:filename>', methods=['GET'])
def get_keyframe(filename):
    """获取关键帧标注图片接口"""
    target_path, ok = safe_resolve(KEYFRAMES_FOLDER, filename)
    if not ok or not os.path.isfile(target_path):
        return jsonify({'error': '关键帧图片不存在'}), 404

    return send_file(target_path, as_attachment=True)


@app.route('/api/keyframes/<path:filename>/download', methods=['GET'])
def download_keyframes(filename):
    """下载关键帧标注图片（打包为zip）"""
    keyframes_dir, ok = safe_resolve(KEYFRAMES_FOLDER, filename)
    if not ok or not os.path.isdir(keyframes_dir):
        return jsonify({'error': '关键帧目录不存在'}), 404
    
    # 创建zip文件
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
        for root, dirs, files in os.walk(keyframes_dir):
            for file in files:
                if file.endswith(('.jpg', '.png', '.json')):
                    file_path = os.path.join(root, file)
                    arcname = os.path.relpath(file_path, keyframes_dir)
                    zip_file.write(file_path, arcname)
    
    zip_buffer.seek(0)
    return send_file(
        zip_buffer,
        mimetype='application/zip',
        as_attachment=True,
        download_name=f"{filename}_keyframes.zip"
    )


@app.route('/api/history', methods=['GET'])
def get_history():
    """获取当前用户的历史跑姿记录（新→旧），?limit=&offset= 分页"""
    user = g.current_user
    limit = min(int(request.args.get('limit', 50)), 200)
    offset = max(int(request.args.get('offset', 0)), 0)
    records = history_store.list_for_user(user, limit=limit, offset=offset)
    total = history_store.count_for_user(user)
    return jsonify({
        'status': 'success',
        'total_records': total,
        'records': records
    })


@app.route('/api/history/records', methods=['GET'])
def get_history_records():
    """
    历史记录查询。
    普通用户：仅本人记录（?username= 参数忽略）。
    管理员：?username= 可按用户名模糊查；不传则返回全部用户记录。
    """
    user = g.current_user
    limit = min(int(request.args.get('limit', 50)), 200)
    offset = max(int(request.args.get('offset', 0)), 0)
    search = (request.args.get('username') or '').strip()
    if user['role'] == 'admin':
        records = history_store.list_records(username=search or None, limit=limit, offset=offset)
        total = history_store.count_records(username=search or None)
    else:
        records = history_store.list_for_user(user, limit=limit, offset=offset)
        total = history_store.count_for_user(user)
    return jsonify({
        'status': 'success',
        'is_admin': user['role'] == 'admin',
        'total_records': total,
        'records': records
    })


@app.route('/api/history/users', methods=['GET'])
def get_history_users():
    """管理员：按用户聚合的历史统计（总次数 / 最佳得分 / 最近分析）"""
    if g.current_user['role'] != 'admin':
        return jsonify({'error': '需要管理员权限', 'code': 'forbidden'}), 403
    return jsonify({'status': 'success', 'users': history_store.user_stats()})


@app.route('/api/history/compare', methods=['POST'])
def compare_history():
    """
    历史对比。Body: {"record_ids": [1, 2, 3]}（最多3条，按时间旧→新展示并计算趋势）
    普通用户仅能对比本人记录；管理员可对比任意记录。
    """
    data = request.get_json(silent=True) or {}
    ids = data.get('record_ids') or []
    if not isinstance(ids, list) or not ids:
        return jsonify({'error': '请提供 record_ids 列表（2-3 条记录）'}), 400
    user = g.current_user
    is_admin = user['role'] == 'admin'
    result = history_store.compare(ids, user=user, is_admin=is_admin)
    if not result:
        return jsonify({'error': '没有可对比的记录'}), 400
    if len(result['records']) < 2:
        return jsonify({'error': '至少需要 2 条有效记录进行对比'}), 400
    return jsonify({'status': 'success', **result})


@app.route('/api/history/delete/<int:record_id>', methods=['DELETE'])
def delete_history_record(record_id: int):
    """删除历史数据记录（本人或管理员）"""
    rec = history_store.get_record(record_id)
    if not rec:
        return jsonify({'error': '记录不存在'}), 404
    user = g.current_user
    if not history_store._owns_record(rec, user, user['role'] == 'admin'):
        return jsonify({'error': '无权删除该记录', 'code': 'forbidden'}), 403
    history_store.delete_record(record_id)
    return jsonify({'status': 'deleted', 'record_id': record_id})


@app.route('/api/batch', methods=['POST'])
@limiter.limit("3 per hour")
def batch_process():
    """
    批量处理视频文件接口
    
    Request Body:
        video_files: 视频文件列表（上传后返回的名称）
        user_name: 用户姓名
        coach_name: 评估教练名称
        keyframes: 关键帧数量（默认5）
        report_format: 可选，报告格式 'pdf'（默认）或 'docx'
    
    Returns:
        JSON: 批量分析结果（打包为zip文件下载）
    """
    data = request.get_json()
    
    video_filenames = data.get('video_files', [])
    if not video_filenames:
        return jsonify({'error': '请提供视频文件列表'}), 400
    
    user_info = {
        'name': data.get('user_name') or current_display_name() or '匿名用户',
        'coach': data.get('coach_name', '系统自动评估'),
        'score': 0
    }
    
    num_keyframes = int(data.get('keyframes', 5))
    
    results = []
    errors = []
    
    for i, video_filename in enumerate(video_filenames):
        video_path = get_video_path(video_filename)
        
        if not os.path.exists(video_path):
            errors.append({
                'filename': video_filename,
                'error': '文件不存在'
            })
            continue
        
        try:
            # 执行姿态分析
            analyzer = PoseAnalyzer()
            
            try:
                analysis_results = analyzer.process_video(video_path)
            finally:
                analyzer.close()
            
            if not analysis_results:
                errors.append({
                    'filename': video_filename,
                    'error': '未能识别到人体姿态'
                })
                continue

            # 先提取并保存关键帧标注截图（报告需要嵌入）
            keyframe_analyzer = KeyFrameAnalyzer()
            key_frames = keyframe_analyzer.extract_key_frames(video_path, num_keyframes)
            unique_id = f"batch_kf_{i}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
            keyframes_dir = get_keyframes_path(unique_id)
            save_keyframes(keyframes_dir, key_frames, analysis_results)

            # 生成报告（嵌入关键帧截图）
            report_format = (data.get('report_format') or 'pdf').lower()
            if report_format not in ('pdf', 'docx'):
                report_format = 'pdf'
            generator = ReportGenerator()
            report_path = generator.generate_report(
                video_path,
                user_info,
                analysis_results,
                key_frames_dir=keyframes_dir,
                fmt=report_format
            )

            # 计算综合得分（与报告口径一致）
            score_info = ReportGenerator()._calculate_score(generator.metrics)
            score = score_info['total']

            # 自动写入历史记录
            save_history_record(
                user=getattr(g, 'current_user', None),
                video_file=video_filename,
                report_file=os.path.basename(report_path),
                keyframes_dir=os.path.basename(keyframes_dir),
                analysis_results=analysis_results,
                score=score,
                grade=score_info['grade'],
                problems=generator.metrics.get('problems', []),
                suggestions=generator.metrics.get('suggestions', []),
                coach=user_info['coach'],
                video_path=video_path,
            )

            results.append({
                'video_filename': video_filename,
                'score': score,
                'grade': score_info['grade'],
                'report_file': os.path.basename(report_path),
                'keyframes_dir': os.path.basename(keyframes_dir),
                'problems': generator.metrics.get('problems', []),
                'suggestions': generator.metrics.get('suggestions', []),
                'metrics': analysis_results[-1]
            })
        
        except Exception as e:
            errors.append({
                'filename': video_filename,
                'error': str(e)
            })
    
    # 构建返回数据
    summary = {
        'status': 'completed',
        'total_files': len(video_filenames),
        'successful': len(results),
        'failed': len(errors),
        'results': results,
        'errors': errors
    }
    
    # 如果需要下载，创建zip文件
    if request.args.get('download') == 'true':
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
            # 添加报告和关键帧
            for result in results:
                report_path = get_report_path(result['report_file'])
                if os.path.exists(report_path):
                    zip_file.write(report_path, f"reports/{result['report_file']}")
                
                keyframes_dir = get_keyframes_path(result['keyframes_dir'])
                if os.path.exists(keyframes_dir):
                    for root, dirs, files in os.walk(keyframes_dir):
                        for file in files:
                            file_path = os.path.join(root, file)
                            arcname = f"keyframes/{os.path.basename(result['keyframes_dir'])}/{file}"
                            zip_file.write(file_path, arcname)
        
        zip_buffer.seek(0)
        return send_file(
            zip_buffer,
            mimetype='application/zip',
            as_attachment=True,
            download_name=f"batch_analysis_{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip"
        )
    
    return jsonify(summary)


@app.route('/api/delete/<filename>', methods=['POST'])
def delete_video(filename):
    """删除上传的视频文件"""
    video_path = get_video_path(filename)
    if os.path.exists(video_path):
        os.remove(video_path)
        return jsonify({'status': 'deleted', 'filename': filename})
    return jsonify({'error': '文件不存在'}), 404


@app.route('/api/templates', methods=['GET'])
def get_templates():
    """获取可用的报告模板列表"""
    templates = [
        {
            'id': 'default',
            'name': '标准姿势跑法评估',
            'description': '基于PDF模板的完整技术评分'
        },
        {
            'id': 'simple',
            'name': '简易评估报告',
            'description': '仅包含关键指标和简要建议'
        }
    ]
    return jsonify({'templates': templates})


# 错误处理
@app.errorhandler(413)
def too_large(e):
    return jsonify({'error': '文件过大，最大支持100MB'}), 413


@app.errorhandler(429)
def rate_limited(e):
    return jsonify({'error': '请求过于频繁，请稍后再试', 'code': 'rate_limited'}), 429


@app.errorhandler(500)
def internal_error(e):
    return jsonify({'error': '服务器内部错误'}), 500


# 启动服务
if __name__ == '__main__':
    print("=" * 50)
    print("PoseRun Analyzer 启动中...")
    print("=" * 50)
    
    # 创建必要的文件夹
    for folder in [UPLOAD_FOLDER, REPORTS_FOLDER, KEYFRAMES_FOLDER]:
        os.makedirs(folder, exist_ok=True)
    
    app.run(
        host='0.0.0.0',
        port=8080,
        debug=False
    )