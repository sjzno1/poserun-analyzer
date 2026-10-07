"""
PoseRun Analyzer 跑姿历史记录模块
- SQLite 历史库（data/history.db，与 auth.db 同卷持久化）
- 每位注册用户的历次跑姿记录：综合得分、关键指标、报告/关键帧引用
- 普通用户仅可见本人记录；管理员可见全部用户记录
- 首次启动自动迁移旧版 history.json（一次性，迁移后不改原文件）

用法:
    store = HistoryStore(db_path)
    rec = store.add_record(user=g.current_user, video_file=..., score=85, ...)
    records = store.list_records(user_id=1, limit=50)
    cmp = store.compare([1, 2, 3], allowed_user_ids={1})
"""

import json
import os
import sqlite3
import threading
import uuid
from datetime import datetime

# 对比时参与趋势计算的数值型指标（列名 -> 展示名）
NUMERIC_METRICS = (
    ('score', '综合得分'),
    ('cadence', '步频'),
    ('avg_knee', '膝盖弯曲角度'),
    ('avg_shin', '小腿角度'),
    ('body_lean', '身体前倾角度'),
)


def _now():
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')


class HistoryStore:
    """跑姿历史记录存储（零第三方依赖）"""

    def __init__(self, db_path):
        self.db_path = db_path
        db_dir = os.path.dirname(os.path.abspath(db_path))
        os.makedirs(db_dir, exist_ok=True)
        self._lock = threading.Lock()
        self._init_db()
        self._migrate_legacy_json(os.path.join(db_dir, '..', 'history.json'))

    # ---------- 基础设施 ----------

    def _conn(self):
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA journal_mode=WAL')
        return conn

    def _init_db(self):
        with self._lock, self._conn() as conn:
            conn.execute('''
                CREATE TABLE IF NOT EXISTS history_records (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    record_id TEXT UNIQUE NOT NULL,
                    user_id INTEGER,
                    username TEXT NOT NULL DEFAULT '',
                    user_name TEXT NOT NULL DEFAULT '',
                    coach TEXT NOT NULL DEFAULT '',
                    video_file TEXT NOT NULL DEFAULT '',
                    report_file TEXT NOT NULL DEFAULT '',
                    keyframes_dir TEXT NOT NULL DEFAULT '',
                    score INTEGER,
                    grade TEXT NOT NULL DEFAULT '',
                    avg_knee REAL,
                    avg_shin REAL,
                    stride_level TEXT NOT NULL DEFAULT '',
                    symmetry_level TEXT NOT NULL DEFAULT '',
                    body_lean REAL,
                    running_phase TEXT NOT NULL DEFAULT '',
                    cadence REAL,
                    total_frames INTEGER,
                    problems_json TEXT NOT NULL DEFAULT '[]',
                    suggestions_json TEXT NOT NULL DEFAULT '[]',
                    metrics_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL
                )
            ''')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_hist_user ON history_records(user_id)')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_hist_username ON history_records(username)')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_hist_created ON history_records(created_at)')
            # 兼容旧库：补齐新增列
            self._ensure_columns(conn)

    def _ensure_columns(self, conn):
        """为已存在的旧表补齐新增列（幂等）"""
        cols = {r[1] for r in conn.execute('PRAGMA table_info(history_records)').fetchall()}
        migrations = {
            'cadence': 'ALTER TABLE history_records ADD COLUMN cadence REAL',
        }
        for col, stmt in migrations.items():
            if col not in cols:
                conn.execute(stmt)
                print(f'[history] 已为旧库补齐列: {col}')

    # ---------- 旧版 history.json 迁移 ----------

    def _migrate_legacy_json(self, json_path):
        """把旧版全局 history.json 的记录并入数据库（按 record_id 去重）"""
        if not os.path.exists(json_path):
            return
        try:
            with open(json_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            legacy = data.get('history', []) if isinstance(data, dict) else []
        except Exception:
            return
        if not legacy:
            return
        with self._lock, self._conn() as conn:
            for item in legacy:
                m = item.get('metrics', {}) or {}
                record_id = item.get('record_id') or f"legacy_{uuid.uuid4().hex[:12]}"
                exists = conn.execute(
                    'SELECT id FROM history_records WHERE record_id=?', (record_id,)
                ).fetchone()
                if exists:
                    continue
                date = item.get('date') or m.get('date') or ''
                created_at = f'{date} 00:00:00' if date else _now()
                conn.execute(
                    'INSERT INTO history_records (record_id, user_id, username, user_name, '
                    'video_file, score, avg_knee, avg_shin, stride_level, symmetry_level, '
                    'metrics_json, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
                    (
                        record_id,
                        None,
                        str(m.get('name') or ''),
                        str(m.get('name') or ''),
                        str(item.get('record_id') or ''),
                        m.get('score'),
                        m.get('avg_knee'),
                        m.get('avg_shin'),
                        str(m.get('stride_level') or ''),
                        str(m.get('symmetry_level') or ''),
                        json.dumps(m, ensure_ascii=False, default=str),
                        created_at,
                    )
                )
            print(f'[history] 已从 history.json 迁移 {len(legacy)} 条旧记录')

    # ---------- 记录读写 ----------

    @staticmethod
    def _row_to_dict(row):
        d = dict(row)
        try:
            d['problems'] = json.loads(d.pop('problems_json') or '[]')
        except Exception:
            d['problems'] = []
        try:
            d['suggestions'] = json.loads(d.pop('suggestions_json') or '[]')
        except Exception:
            d['suggestions'] = []
        try:
            d['metrics'] = json.loads(d.pop('metrics_json') or '{}')
        except Exception:
            d['metrics'] = {}
        return d

    def add_record(self, user, video_file='', report_file='', keyframes_dir='',
                   score=None, grade='', metrics=None, problems=None,
                   suggestions=None, total_frames=None, coach=''):
        """写入一条跑姿记录。user 为登录用户 dict（None 时记为匿名）"""
        metrics = metrics or {}
        record_id = f"rec_{uuid.uuid4().hex[:12]}"
        with self._lock, self._conn() as conn:
            cur = conn.execute(
                'INSERT INTO history_records (record_id, user_id, username, user_name, coach, '
                'video_file, report_file, keyframes_dir, score, grade, avg_knee, avg_shin, '
                'stride_level, symmetry_level, body_lean, running_phase, cadence, total_frames, '
                'problems_json, suggestions_json, metrics_json, created_at) '
                'VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                (
                    record_id,
                    (user or {}).get('id'),
                    str((user or {}).get('username') or ''),
                    str((user or {}).get('name') or ''),
                    str(coach or ''),
                    str(video_file or ''),
                    str(report_file or ''),
                    str(keyframes_dir or ''),
                    score,
                    str(grade or ''),
                    metrics.get('avg_knee'),
                    metrics.get('avg_shin'),
                    str(metrics.get('stride_level') or ''),
                    str(metrics.get('symmetry_level') or ''),
                    metrics.get('body_lean'),
                    str(metrics.get('running_phase') or ''),
                    metrics.get('cadence'),
                    total_frames,
                    json.dumps(problems or [], ensure_ascii=False),
                    json.dumps(suggestions or [], ensure_ascii=False),
                    json.dumps(metrics, ensure_ascii=False, default=str),
                    _now(),
                )
            )
            row = conn.execute(
                'SELECT * FROM history_records WHERE id=?', (cur.lastrowid,)
            ).fetchone()
        return self._row_to_dict(row)

    def list_records(self, user_id=None, username=None, limit=50, offset=0):
        """按用户查询记录（新→旧）。user_id 与 username 二选一或同时；都不传返回全部（管理员）"""
        sql = 'SELECT * FROM history_records'
        clauses, args = [], []
        if user_id is not None:
            clauses.append('user_id=?')
            args.append(user_id)
        if username:
            clauses.append('username LIKE ?')
            args.append(f'%{username}%')
        if clauses:
            sql += ' WHERE ' + ' AND '.join(clauses)
        sql += ' ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?'
        args.extend([int(limit), int(offset)])
        with self._lock, self._conn() as conn:
            rows = conn.execute(sql, args).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def list_for_user(self, user, limit=50, offset=0):
        """当前用户可见的记录：user_id / username / user_name 任一匹配（兼容旧版迁移记录）"""
        sql = ('SELECT * FROM history_records '
               'WHERE user_id=? OR username=? OR user_name=? '
               'ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?')
        args = [user.get('id'), user.get('username') or '', user.get('name') or '',
                int(limit), int(offset)]
        with self._lock, self._conn() as conn:
            rows = conn.execute(sql, args).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def count_for_user(self, user):
        sql = ('SELECT COUNT(*) FROM history_records '
               'WHERE user_id=? OR username=? OR user_name=?')
        args = [user.get('id'), user.get('username') or '', user.get('name') or '']
        with self._lock, self._conn() as conn:
            return conn.execute(sql, args).fetchone()[0]

    def get_record(self, record_id):
        with self._lock, self._conn() as conn:
            row = conn.execute(
                'SELECT * FROM history_records WHERE id=?', (int(record_id),)
            ).fetchone()
        return self._row_to_dict(row) if row else None

    def delete_record(self, record_id):
        with self._lock, self._conn() as conn:
            cur = conn.execute(
                'DELETE FROM history_records WHERE id=?', (int(record_id),)
            )
            return cur.rowcount > 0

    def delete_by_user(self, user_id):
        """用户被删除时级联清理其历史记录。返回删除条数"""
        with self._lock, self._conn() as conn:
            cur = conn.execute(
                'DELETE FROM history_records WHERE user_id=?', (int(user_id),)
            )
            return cur.rowcount

    def count_records(self, user_id=None, username=None):
        sql = 'SELECT COUNT(*) FROM history_records'
        clauses, args = [], []
        if user_id is not None:
            clauses.append('user_id=?')
            args.append(user_id)
        if username:
            clauses.append('username LIKE ?')
            args.append(f'%{username}%')
        if clauses:
            sql += ' WHERE ' + ' AND '.join(clauses)
        with self._lock, self._conn() as conn:
            return conn.execute(sql, args).fetchone()[0]

    def user_stats(self):
        """按用户聚合统计（管理员视图）"""
        sql = '''
            SELECT username,
                   MAX(user_name) AS user_name,
                   COUNT(*) AS total,
                   MAX(score) AS best_score,
                   MAX(created_at) AS latest_at
            FROM history_records
            WHERE username != ''
            GROUP BY username
            ORDER BY latest_at DESC
        '''
        with self._lock, self._conn() as conn:
            rows = conn.execute(sql).fetchall()
        return [dict(r) for r in rows]

    # ---------- 历史对比 ----------

    @staticmethod
    def _owns_record(rec, user, is_admin):
        """记录归属判断：管理员放行；否则 user_id / username / user_name 任一匹配"""
        if is_admin:
            return True
        if user is None:
            return False
        if rec.get('user_id') and rec.get('user_id') == user.get('id'):
            return True
        return (rec.get('username') and rec.get('username') == user.get('username')) or \
               (rec.get('user_name') and rec.get('user_name') == user.get('name'))

    def compare(self, record_ids, user=None, is_admin=False):
        """
        取最多 3 条记录做对比。
        user: 当前登录用户 dict；非管理员只能对比本人记录（含旧版迁移记录）。
        is_admin: 管理员可对比任意记录。
        返回 {records: [按时间 旧→新], deltas: {指标: 最新-最早}}
        """
        ids = [int(i) for i in record_ids if str(i).isdigit()][:3]
        if not ids:
            return None
        records = []
        for rid in ids:
            rec = self.get_record(rid)
            if not rec:
                continue
            if not self._owns_record(rec, user, is_admin):
                continue
            records.append(rec)
        if not records:
            return None
        # 按时间排序：旧 → 新
        records.sort(key=lambda r: (r.get('created_at') or '', r.get('id') or 0))
        first, last = records[0], records[-1]
        deltas = {}
        for col, label in NUMERIC_METRICS:
            a, b = first.get(col), last.get(col)
            deltas[col] = {
                'label': label,
                'from': a,
                'to': b,
                'delta': round(b - a, 1) if (a is not None and b is not None) else None,
            }
        return {'records': records, 'deltas': deltas}
