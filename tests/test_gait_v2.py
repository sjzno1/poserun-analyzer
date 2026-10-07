"""验证：One Euro 平滑 + 可见度过滤 + 带符号小腿角 + 落地姿态统计"""
import sys, math, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from smoothing import smooth_series, smooth_segments, OneEuroFilter
from gait_analyzer import GaitAnalyzer


class FakeLM:
    def __init__(self, x, y, vis=1.0, name='p'):
        self.x, self.y, self.visibility, self.name = x, y, vis, name


def _leg_state(t, period, phase, support_shin, swing_shin):
    """给定时刻返回 (shin_mag, signed)。
    支撑期：角度 support_shin（>58），踝在前 → signed=+shin
    摆动期：角度 swing_shin（<42），踝在后 → signed=-shin"""
    ph = (t / period + phase) % 1.0
    if ph < 0.6:  # 支撑 60%
        return support_shin, support_shin
    return swing_shin, -swing_shin


def make_frame(i, fps, vis=1.0, jitter=0.0, support_shin=60.0, swing_shin=38.0):
    """合成跑步周期：30fps，170 步/分 → 单腿周期 60/170*2 ≈ 0.706s。
    左右腿同帧、相位差 0.5（真实跑步交替着地）。
    落地瞬间 signed = +support_shin（前倾），用于跨步判定。"""
    period = 60.0 / 170.0 * 2
    t = i / fps
    l_shin, l_signed = _leg_state(t, period, 0.0, support_shin, swing_shin)
    r_shin, r_signed = _leg_state(t, period, 0.5, support_shin, swing_shin)
    if jitter:
        l_shin += jitter * math.sin(i * 2.7)
        r_shin += jitter * math.sin(i * 2.7 + 1.3)
        l_signed += jitter * math.cos(i * 3.1)
        r_signed += jitter * math.cos(i * 3.1 + 1.3)

    def ankle(knee_x, shin, signed):
        L = 0.25
        rad = math.radians(abs(shin))
        # 算法约定：踝在膝左（x 减小）= 前倾 = 正
        dx = L * math.cos(rad) * (-1 if signed > 0 else 1)
        dy = L * math.sin(rad)
        return knee_x + dx, 0.6 + dy

    la_x, la_y = ankle(0.48, l_shin, l_signed)
    ra_x, ra_y = ankle(0.54, r_shin, r_signed)
    return {
        'frame_index': i,
        'landmarks': [
            FakeLM(0.5, 0.4, vis, 'LEFT_HIP'), FakeLM(0.52, 0.4, vis, 'RIGHT_HIP'),
            FakeLM(0.48, 0.6, vis, 'LEFT_KNEE'), FakeLM(0.54, 0.6, vis, 'RIGHT_KNEE'),
            FakeLM(la_x, la_y, vis, 'LEFT_ANKLE'),
            FakeLM(ra_x, ra_y, vis, 'RIGHT_ANKLE'),
        ],
    }


def build_results(n=300, fps=30, jitter=0.0, occlude=(), **kw):
    res = []
    for i in range(n):
        vis = 0.1 if i in occlude else 1.0
        res.append(make_frame(i, fps, vis, jitter, **kw))
    return res


# ---- 1) One Euro 基本性质 ----
ts = [i / 30 for i in range(100)]
xs = [10 + 0.5 * math.sin(i / 5) for i in range(100)]
sm = smooth_series(xs, ts, min_cutoff=1.2, beta=0.7)
assert len(sm) == len(xs)
raw_var = sum((a - b) ** 2 for a, b in zip(xs[1:], xs))
sm_var = sum((a - b) ** 2 for a, b in zip(sm[1:], sm))
assert sm_var < raw_var, f"平滑后波动应减小: {sm_var} vs {raw_var}"
print(f"[1] One Euro 平滑 OK  波动 {raw_var:.4f} -> {sm_var:.4f}")

# ---- 2) smooth_segments 缺口不跨段 ----
out = smooth_segments([1.0, 2.0, None, None, 10.0, 11.0], [0, 1/30, 2/30, 3/30, 4/30, 5/30])
assert out[2] is None and out[3] is None
assert out[4] == 10.0  # 段首帧直通（无历史）
assert out[5] < 11.0 and out[5] > 10.0  # 段内平滑，向 10 靠拢
print(f"[2] 缺口平滑 OK  {['None' if v is None else round(v,2) for v in out]}")

# ---- 3) 步态：无噪声基线 ----
ga = GaitAnalyzer(fps=30)
g = ga.analyze(build_results(jitter=0.0))
assert g is not None
assert 155 < g['cadence'] < 185, f"步频应在 170 附近: {g['cadence']}"
assert g['total_steps'] >= 25, f"10s@170spm 应约 28 步: {g['total_steps']}"
print(f"[3] 无噪声基线 OK  步频={g['cadence']} 步数={g['total_steps']} 对称={g['step_symmetry']}")

# ---- 4) 步态：加噪声（jitter=3°）后平滑应稳住步频 ----
g2 = ga.analyze(build_results(jitter=3.0))
assert g2 is not None
assert 155 < g2['cadence'] < 185, f"噪声下步频应仍稳定: {g2['cadence']}"
print(f"[4] 噪声(jitter=3°) OK  步频={g2['cadence']} 步数={g2['total_steps']}")

# ---- 5) 可见度过滤：遮挡帧被剔除 ----
occl = set(range(100, 130))  # 中间 1 秒全遮挡
g3 = ga.analyze(build_results(occlude=occl))
assert g3 is not None
assert g3['valid_frame_ratio'] < 1.0
assert 0.8 <= g3['valid_frame_ratio'] <= 0.9
print(f"[5] 可见度过滤 OK  可信帧比例={g3['valid_frame_ratio']} 步频={g3['cadence']}")

# ---- 6) 落地姿态：前倾为正、跨步占比 ----
sp = g2['strike_posture']
assert sp is not None
assert sp['left_forward_deg'] is not None and sp['left_forward_deg'] > 0, f"前倾应为正: {sp}"
assert sp['over_stride_ratio'] is not None
print(f"[6] 落地姿态 OK  左前倾={sp['left_forward_deg']}° 右前倾={sp['right_forward_deg']}° 跨步占比={sp['over_stride_ratio']} ({sp['over_stride_level']})")

# ---- 7) 跨步场景：落地前倾角 70°（>65° 阈值）→ 跨步占比升高 ----
ga2 = GaitAnalyzer(fps=30)
g4 = ga2.analyze(build_results(support_shin=70.0))
assert g4 is not None
sp4 = g4['strike_posture']
assert sp4['over_stride_ratio'] is not None and sp4['over_stride_ratio'] > g2['strike_posture']['over_stride_ratio']
print(f"[7] 跨步检测 OK  跨步占比 {sp4['over_stride_ratio']} ({sp4['over_stride_level']}) > 正常 {g2['strike_posture']['over_stride_ratio']}")

# ---- 8) 数据不足返回 None ----
assert ga.analyze(build_results(n=5)) is None
print("[8] 数据不足返回 None OK")

print("\n全部 8 项测试通过 ✅")
