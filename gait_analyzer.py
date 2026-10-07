"""
PoseRun Analyzer - 步态（Gait）分析模块
从逐帧姿态关键点中计算跑步步态指标。纯标准库实现（不依赖 numpy），
与 pose_analyzer 的逐帧结果对接。

参考同类开源项目（brilliantbeaver/runninggait、kamil-czurak/running-gait-analyzer、
pandahui528/running-pose-analysis）补充本地原不具备的指标：
  - 步频 cadence（步/分）
  - 垂直振荡 vertical oscillation（上下颠簸，按腿长归一化）
  - 支撑/摆动时间比 + 接地时间
  - 步幅 stride length（按腿长归一化 + 可选米制估算）
  - 左右步态对称性

用法:
    ga = GaitAnalyzer(fps=30.0)
    gait = ga.analyze(analysis_results)   # 返回 dict 或 None（数据不足）
"""

import math
from statistics import mean, pstdev

from smoothing import smooth_segments

# 小腿角度（与地面夹角，竖直=90°）用于判定支撑/摆动的阈值
PLANT_HIGH = 58.0   # 高于此视为"支撑"（脚已着地）
SWING_LOW = 42.0    # 低于此视为"摆动"（脚已离地）
MIN_FRAMES = 20     # 少于该帧数不计算步态

# 关键点可见度阈值：低于此视为遮挡/不可信
MIN_VISIBILITY = 0.5
# One Euro 平滑参数（角度序列）
SMOOTH_MIN_CUTOFF = 1.2
SMOOTH_BETA = 0.7


def _mean(xs):
    return mean(xs) if xs else 0.0


def _std(xs):
    return pstdev(xs) if len(xs) >= 2 else 0.0


class GaitAnalyzer:
    """基于小腿角度状态机的步态事件检测 + 指标计算"""

    def __init__(self, fps=30.0):
        self.fps = float(fps) if fps and fps > 0 else 30.0

    # ---------- 关键点提取 ----------

    @staticmethod
    def _points(landmarks):
        return {lm.name: lm for lm in (landmarks or [])}

    def _frame_features(self, result):
        """从单帧结果提取步态所需特征；缺关键点/低可见度返回 None 或置空角度"""
        pts = self._points(result.get('landmarks'))
        need = ['LEFT_HIP', 'RIGHT_HIP', 'LEFT_KNEE', 'RIGHT_KNEE',
                'LEFT_ANKLE', 'RIGHT_ANKLE']
        t = result.get('frame_index', 0) / self.fps
        if any(n not in pts for n in need):
            # 退化：用 metrics 里的小腿角度
            m = result.get('metrics', result)
            l_shin = m.get('left_shin_angle')
            r_shin = m.get('right_shin_angle')
            if l_shin is None or r_shin is None:
                return None
            l_signed = m.get('left_shin_signed')
            r_signed = m.get('right_shin_signed')
            return {
                't': t,
                'l_shin': float(l_shin), 'r_shin': float(r_shin),
                'l_shin_s': float(l_signed) if l_signed is not None else None,
                'r_shin_s': float(r_signed) if r_signed is not None else None,
                'hip_y': None, 'l_ax': None, 'r_ax': None, 'leg_len': None,
            }
        # 可见度过滤：任一关键点被遮挡/不可信 → 角度置 None（不跨缺口平滑）
        low_vis = any(
            float(getattr(pts[n], 'visibility', 1.0) or 1.0) < MIN_VISIBILITY
            for n in need
        )
        # 腿长（髋-踝，归一化坐标距离）；加合理性边界，异常值视为不可用
        def seg(a, b):
            return math.hypot(pts[a].x - pts[b].x, pts[a].y - pts[b].y)
        leg_l = seg('LEFT_HIP', 'LEFT_ANKLE')
        leg_r = seg('RIGHT_HIP', 'RIGHT_ANKLE')
        leg_len = (leg_l + leg_r) / 2 if (leg_l + leg_r) > 0 else None
        if leg_len is not None and not (0.05 < leg_len < 1.0):
            leg_len = None
        # 小腿角度（与地面夹角，竖直=90）
        def shin(knee, ankle):
            dx = pts[ankle].x - pts[knee].x
            dy = pts[ankle].y - pts[knee].y
            if dx == 0 and dy == 0:
                return 0.0
            return math.degrees(math.atan2(abs(dy), abs(dx)))
        # 带符号小腿角（踝在前为正 / 在后为负）
        def shin_signed(knee, ankle):
            dx = pts[ankle].x - pts[knee].x
            dy = pts[ankle].y - pts[knee].y
            if dx == 0 and dy == 0:
                return 0.0
            mag = math.degrees(math.atan2(abs(dy), abs(dx)))
            return mag if dx < 0 else -mag
        if low_vis:
            l_shin = r_shin = None
            l_s = r_s = None
        else:
            l_shin = shin('LEFT_KNEE', 'LEFT_ANKLE')
            r_shin = shin('RIGHT_KNEE', 'RIGHT_ANKLE')
            l_s = shin_signed('LEFT_KNEE', 'LEFT_ANKLE')
            r_s = shin_signed('RIGHT_KNEE', 'RIGHT_ANKLE')
        hip_y = (pts['LEFT_HIP'].y + pts['RIGHT_HIP'].y) / 2
        return {
            't': t,
            'l_shin': l_shin, 'r_shin': r_shin,
            'l_shin_s': l_s, 'r_shin_s': r_s,
            'hip_y': hip_y,
            'l_ax': pts['LEFT_ANKLE'].x, 'r_ax': pts['RIGHT_ANKLE'].x,
            'leg_len': leg_len,
        }

    # ---------- 主入口 ----------

    def analyze(self, results, body_height_m=None):
        """计算步态指标。results 为逐帧分析结果列表。返回 dict 或 None。"""
        if not results or len(results) < MIN_FRAMES:
            return None
        frames = []
        for r in results:
            f = self._frame_features(r)
            if f is not None:
                frames.append(f)
        if len(frames) < MIN_FRAMES:
            return None

        # 去重/排序（按时间）
        frames.sort(key=lambda f: f['t'])
        duration = frames[-1]['t'] - frames[0]['t']
        if duration <= 0.3:
            return None

        # One Euro 平滑（含 None 缺口不跨段）
        t_series = [f['t'] for f in frames]
        l_shin = smooth_segments([f['l_shin'] for f in frames], t_series,
                                 min_cutoff=SMOOTH_MIN_CUTOFF, beta=SMOOTH_BETA)
        r_shin = smooth_segments([f['r_shin'] for f in frames], t_series,
                                 min_cutoff=SMOOTH_MIN_CUTOFF, beta=SMOOTH_BETA)
        # 单帧有效比例（可见度过滤后的可信帧占比）
        valid_count = sum(1 for a, b in zip(l_shin, r_shin) if a is not None and b is not None)
        valid_ratio = valid_count / len(frames) if frames else 0.0

        # 1) 步态事件检测（每腿 swing→planted 记一次落地）
        l_strikes, l_stance = self._detect_events(l_shin, t_series)
        r_strikes, r_stance = self._detect_events(r_shin, t_series)

        total_steps = len(l_strikes) + len(r_strikes)
        cadence = (total_steps / duration * 60.0) if duration > 0 else 0.0

        # 2) 接地/支撑时间
        all_stance = l_stance + r_stance
        ground_contact_ms = round(_mean(all_stance) * 1000.0) if all_stance else None
        # 单步周期（同腿相邻落地间隔）→ 摆动时间
        def swing_times(strikes):
            return [b - a for a, b in zip(strikes, strikes[1:])]
        l_period = swing_times(l_strikes)
        r_period = swing_times(r_strikes)
        periods = l_period + r_period
        stance_mean = _mean(all_stance)
        period_mean = _mean(periods)
        if period_mean > 0 and stance_mean > 0:
            stance_ratio = min(1.0, stance_mean / period_mean)
            swing_ms = (period_mean - stance_mean) * 1000.0
        else:
            stance_ratio = None
            swing_ms = None

        # 3) 垂直振荡（髋部上下位移，按腿长归一化）
        hip_ys = [f['hip_y'] for f in frames if f['hip_y'] is not None]
        leg_lens = [f['leg_len'] for f in frames if f['leg_len'] is not None]
        leg_len = _mean(leg_lens)
        bounce_ratio = None
        bounce_level = '未知'
        if len(hip_ys) >= MIN_FRAMES and leg_len and leg_len > 0:
            # 平滑后取峰谷差
            sm = self._smooth(hip_ys, 3)
            amp = max(sm) - min(sm)
            bounce_ratio = round(amp / leg_len, 3)
            bounce_level = self._bounce_level(bounce_ratio)

        # 4) 步幅（同腿相邻落地时踝部水平位移，按腿长归一化）
        def stride_legs(strikes, ax_series, t_series):
            idx_by_t = {round(t, 3): i for i, t in enumerate(t_series)}
            vals = []
            for a, b in zip(strikes, strikes[1:]):
                ia = idx_by_t.get(round(a, 3))
                ib = idx_by_t.get(round(b, 3))
                if ia is None or ib is None:
                    continue
                xa = ax_series[ia]
                xb = ax_series[ib]
                if xa is None or xb is None:
                    continue
                vals.append(abs(xb - xa))
            return vals
        t_series = [f['t'] for f in frames]
        l_ax = [f['l_ax'] for f in frames]
        r_ax = [f['r_ax'] for f in frames]
        stride_vals = stride_legs(l_strikes, l_ax, t_series) + \
            stride_legs(r_strikes, r_ax, t_series)
        stride_legs_val = round(_mean(stride_vals) / leg_len, 2) if (stride_vals and leg_len) else None
        stride_m = None
        if stride_legs_val is not None and body_height_m:
            # 腿长 ≈ 0.48 * 身高；米制估算
            leg_len_m = 0.48 * float(body_height_m)
            stride_m = round(stride_legs_val * leg_len_m, 2)

        # 5) 左右对称性（步数 + 步频）
        step_sym, step_sym_level = self._symmetry(len(l_strikes), len(r_strikes),
                                                  l_period, r_period)

        # 6) 落地姿态（带符号小腿角：前倾/后倾/跨步占比）
        # 落地是瞬时事件：用原始（未平滑）序列取值，避免滤波延迟失真；
        # 多次落地取均值可抵消单帧噪声
        l_f, l_b, l_over = self._strike_posture(l_strikes, [f['l_shin_s'] for f in frames], t_series)
        r_f, r_b, r_over = self._strike_posture(r_strikes, [f['r_shin_s'] for f in frames], t_series)
        all_over = [x for x in (l_over, r_over) if x is not None]
        over_stride_ratio = round(_mean(all_over), 2) if all_over else None
        over_stride_level = self._over_stride_level(over_stride_ratio)

        return {
            'cadence': round(cadence, 1),
            'cadence_level': self._cadence_level(cadence),
            'total_steps': total_steps,
            'left_steps': len(l_strikes),
            'right_steps': len(r_strikes),
            'duration_sec': round(duration, 1),
            'vertical_oscillation': bounce_ratio,
            'vertical_oscillation_level': bounce_level,
            'stance_ratio': round(stance_ratio, 2) if stance_ratio is not None else None,
            'ground_contact_time_ms': ground_contact_ms,
            'swing_time_ms': round(swing_ms) if swing_ms is not None else None,
            'stride_length_legs': stride_legs_val,
            'stride_length_m': stride_m,
            'step_symmetry': step_sym,
            'step_symmetry_level': step_sym_level,
            'valid_frame_ratio': round(valid_ratio, 2),
            'strike_posture': {
                'left_forward_deg': l_f, 'left_backward_deg': l_b,
                'right_forward_deg': r_f, 'right_backward_deg': r_b,
                'over_stride_ratio': over_stride_ratio,
                'over_stride_level': over_stride_level,
            },
        }

    # ---------- 子过程 ----------

    def _detect_events(self, shin_series, t_series):
        """对一条腿的小腿角度序列做状态机检测（None=遮挡帧，跳过不判状态）。
        返回 (strikes:[落地时刻], stance_durations:[每次支撑时长])"""
        strikes = []
        stances = []
        state = None
        plant_start = None
        for shin, t in zip(shin_series, t_series):
            if shin is None:
                continue
            if state is None:
                if shin > PLANT_HIGH:
                    state = 'planted'
                    plant_start = t
                elif shin < SWING_LOW:
                    state = 'swing'
                continue
            if state == 'swing' and shin > PLANT_HIGH:
                strikes.append(t)          # 落地
                state = 'planted'
                plant_start = t
            elif state == 'planted' and shin < SWING_LOW:
                if plant_start is not None:
                    stances.append(t - plant_start)   # 支撑结束（离地）
                state = 'swing'
        return strikes, stances

    def _strike_posture(self, strikes, shin_s_series, t_series):
        """落地瞬间小腿前倾/后倾统计（带符号角度，踝在前为正）。
        过度前倾 → 跨步/刹车步风险；后倾 → 蹬离未充分前送。
        返回 (前倾均值, 后倾均值, 跨步帧占比)"""
        idx_by_t = {round(t, 3): i for i, t in enumerate(t_series)}
        vals = []
        for s in strikes:
            i = idx_by_t.get(round(s, 3))
            if i is None:
                continue
            v = shin_s_series[i]
            if v is not None:
                vals.append(v)
        if not vals:
            return None, None, None
        forward = [v for v in vals if v > 0]
        backward = [v for v in vals if v < 0]
        f_mean = round(_mean(forward), 1) if forward else None
        b_mean = round(_mean(backward), 1) if backward else None
        # 跨步占比：落地时小腿前倾超过 65°（明显在身体前方等着地）
        over = sum(1 for v in vals if v > 65)
        over_ratio = round(over / len(vals), 2)
        return f_mean, b_mean, over_ratio

    @staticmethod
    def _smooth(xs, w):
        if w <= 1 or len(xs) < w:
            return xs
        out = []
        half = w // 2
        for i in range(len(xs)):
            lo = max(0, i - half)
            hi = min(len(xs), i + half + 1)
            out.append(sum(xs[lo:hi]) / (hi - lo))
        return out

    @staticmethod
    def _cadence_level(cadence):
        if cadence < 150:
            return '偏低'
        if cadence <= 190:
            return '适中'
        return '偏高'

    @staticmethod
    def _over_stride_level(ratio):
        """落地时小腿前倾>65° 的占比 → 跨步/刹车步风险"""
        if ratio is None:
            return '未知'
        if ratio < 0.25:
            return '良好'
        if ratio < 0.5:
            return '偏多'
        return '明显'

    @staticmethod
    def _bounce_level(ratio):
        if ratio is None:
            return '未知'
        if ratio < 0.05:
            return '较小'
        if ratio <= 0.10:
            return '适中'
        return '偏大'

    @staticmethod
    def _symmetry(l_n, r_n, l_period, r_period):
        if l_n < 2 or r_n < 2:
            return None, '样本不足'
        # 步数差 + 步频差综合
        count_diff = abs(l_n - r_n) / max(l_n, r_n)
        lm, rm = _mean(l_period), _mean(r_period)
        if lm > 0 and rm > 0:
            period_diff = abs(lm - rm) / ((lm + rm) / 2)
        else:
            period_diff = 0.0
        asym = 0.5 * count_diff + 0.5 * period_diff
        score = round(max(0.0, 1.0 - asym), 2)
        level = '良好' if score >= 0.85 else '一般' if score >= 0.65 else '较差'
        return score, level
