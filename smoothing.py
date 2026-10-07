"""
PoseRun Analyzer - 关键点时序平滑（One Euro 滤波器）
零依赖（纯标准库）。用于抑制 MediaPipe 逐帧关键点抖动，
避免噪声直接传到角度/步态事件检测。

参考: Stepputtis et al., "1€ Filter" (2019)
用法:
    ys = smooth_series(xs, ts, min_cutoff=1.2, beta=0.7)
"""

import math


class OneEuroFilter:
    """一维 One Euro 滤波器。低噪声时平滑强，快速运动时跟随快。"""

    def __init__(self, min_cutoff=1.0, beta=0.0, d_cutoff=1.0):
        self.min_cutoff = float(min_cutoff)
        self.beta = float(beta)
        self.d_cutoff = float(d_cutoff)
        self.x_prev = None
        self.dx_prev = 0.0
        self.t_prev = None

    @staticmethod
    def _alpha(cutoff, dt):
        tau = 1.0 / (2.0 * math.pi * cutoff)
        return 1.0 / (1.0 + tau / dt)

    def __call__(self, t, x):
        if self.t_prev is None:
            self.t_prev, self.x_prev, self.dx_prev = t, x, 0.0
            return x
        dt = t - self.t_prev
        if dt <= 0:
            dt = 1.0 / 60.0
        dx = (x - self.x_prev) / dt
        a_d = self._alpha(self.d_cutoff, dt)
        dx_hat = a_d * dx + (1.0 - a_d) * self.dx_prev
        cutoff = self.min_cutoff + self.beta * abs(dx_hat)
        a = self._alpha(cutoff, dt)
        x_hat = a * x + (1.0 - a) * self.x_prev
        self.x_prev, self.dx_prev, self.t_prev = x_hat, dx_hat, t
        return x_hat

    def reset(self):
        self.x_prev = None
        self.dx_prev = 0.0
        self.t_prev = None


def smooth_series(xs, ts, min_cutoff=1.0, beta=0.0, d_cutoff=1.0):
    """对一维序列做 One Euro 平滑。xs 与 ts 等长，ts 单调递增（秒）。
    返回与 xs 等长的平滑序列。"""
    if not xs:
        return []
    f = OneEuroFilter(min_cutoff=min_cutoff, beta=beta, d_cutoff=d_cutoff)
    return [f(t, x) for t, x in zip(ts, xs)]


def smooth_segments(xs, ts, min_cutoff=1.0, beta=0.0, d_cutoff=1.0):
    """含 None 缺口的序列平滑：按连续非 None 段分别滤波，None 位置保持 None。
    用于单腿小腿角度序列（遮挡帧无数据时不跨缺口平滑）。"""
    out = [None] * len(xs)
    seg_start = None
    seg_x, seg_t = [], []

    def flush():
        nonlocal seg_start, seg_x, seg_t
        if not seg_x:
            return
        sm = smooth_series(seg_x, seg_t, min_cutoff, beta, d_cutoff)
        for j, v in enumerate(sm):
            out[seg_start + j] = v
        seg_start, seg_x, seg_t = None, [], []

    for i, (x, t) in enumerate(zip(xs, ts)):
        if x is None:
            flush()
            continue
        if seg_start is None:
            seg_start = i
        seg_x.append(x)
        seg_t.append(t)
    flush()
    return out
