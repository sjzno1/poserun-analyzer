"""端到端：合成跑步视频 → PoseAnalyzer(新后端) → 逐帧指标 → GaitAnalyzer
在容器内运行：python tests/test_backend_e2e.py
"""
import sys, os, math
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import cv2

FPS = 30
N_FRAMES = 150  # 5 秒
W, H = 640, 480

# 跑步周期：170 步/分 → 单腿周期 0.706s
PERIOD = 60.0 / 170.0 * 2


def _leg(t, phase, support_shin=60.0, swing_shin=30.0):
    ph = (t / PERIOD + phase) % 1.0
    if ph < 0.6:
        return support_shin, support_shin
    return swing_shin, -swing_shin


def draw_runner(img, i):
    """画一个更写实的跑者（填充躯干/头/四肢），返回各关节像素坐标"""
    t = i / FPS
    cx = 320
    hip_y = 200 + int(10 * math.sin(2 * math.pi * t / PERIOD))  # 髋部上下振荡
    sh_y = 120  # 肩高
    sh_x = cx - 12  # 肩（略前倾）
    head = (sh_x - 4, sh_y - 34)

    def leg(hip_pt, phase):
        shin, signed = _leg(t, phase)
        L = 100.0
        rad = math.radians(abs(shin))
        dx = L * math.cos(rad) * (-1 if signed > 0 else 1)
        dy = L * math.sin(rad)
        knee = (hip_pt[0] + int(dx * 0.5), hip_pt[1] + int(dy * 0.5))
        ankle = (hip_pt[0] + int(dx), hip_pt[1] + int(dy))
        return knee, ankle

    lh, rh = (cx - 20, hip_y), (cx + 20, hip_y)
    lk, la = leg(lh, 0.0)
    rk, ra = leg(rh, 0.5)
    # 手臂（摆动）
    arm_ph = (t / PERIOD) % 1.0
    sw = math.sin(2 * math.pi * arm_ph)
    l_elb = (sh_x - 18, sh_y + 34)
    l_wri = (sh_x - 18 + int(-24 * sw), sh_y + 34 + int(20 * sw))
    r_elb = (sh_x + 30, sh_y + 34)
    r_wri = (sh_x + 30 + int(24 * sw), sh_y + 34 + int(-20 * sw))

    # 躯干（填充四边形）
    torso = np.array([[sh_x - 22, sh_y], [sh_x + 30, sh_y],
                      [rh[0] + 16, hip_y + 8], [lh[0] - 16, hip_y + 8]], np.int32)
    cv2.fillPoly(img, [torso], (70, 110, 160))
    # 头
    cv2.circle(img, head, 20, (90, 140, 190), -1)
    # 四肢（粗线，圆头）
    cv2.line(img, (sh_x, sh_y + 6), lh, (70, 110, 160), 16, cv2.LINE_AA)   # 躯干-髋
    cv2.line(img, lh, lk, (60, 95, 140), 18, cv2.LINE_AA)
    cv2.line(img, lk, la, (60, 95, 140), 14, cv2.LINE_AA)
    cv2.line(img, rh, rk, (60, 95, 140), 18, cv2.LINE_AA)
    cv2.line(img, rk, ra, (60, 95, 140), 14, cv2.LINE_AA)
    # 手臂
    cv2.line(img, (sh_x, sh_y), l_elb, (80, 120, 170), 14, cv2.LINE_AA)
    cv2.line(img, l_elb, l_wri, (80, 120, 170), 12, cv2.LINE_AA)
    cv2.line(img, (sh_x + 30, sh_y), r_elb, (80, 120, 170), 14, cv2.LINE_AA)
    cv2.line(img, r_elb, r_wri, (80, 120, 170), 12, cv2.LINE_AA)
    return {'head': head, 'sh': (sh_x, sh_y), 'lh': lh, 'rh': rh,
            'lk': lk, 'rk': rk, 'la': la, 'ra': ra,
            'l_elb': l_elb, 'l_wri': l_wri, 'r_elb': r_elb, 'r_wri': r_wri}


def make_video(path):
    fourcc = cv2.VideoWriter_fourcc(*'mp4v')
    vw = cv2.VideoWriter(path, fourcc, FPS, (W, H))
    for i in range(N_FRAMES):
        img = np.zeros((H, W, 3), np.uint8)
        img[:] = (40, 40, 40)
        cv2.line(img, (0, 430), (W, 430), (120, 120, 120), 2)  # 地面
        draw_runner(img, i)
        vw.write(img)
    vw.release()


def main():
    from pose_analyzer import PoseAnalyzer
    from gait_analyzer import GaitAnalyzer

    vid = os.path.join('/tmp', 'e2e_runner.mp4')
    make_video(vid)
    print(f"video: {vid} ({os.path.getsize(vid)} bytes)")

    pa = PoseAnalyzer()
    print(f"backend: {pa.backend}")
    assert pa.backend == 'tasks_heavy', f"应使用 heavy 后端，实际 {pa.backend}"

    results = pa.process_video(vid)
    pa.close()
    n_valid = len(results)
    print(f"valid frames: {n_valid}/{N_FRAMES} ({n_valid/N_FRAMES:.0%})")
    assert n_valid >= N_FRAMES * 0.7, f"人体检测率过低: {n_valid}/{N_FRAMES}"

    # 逐帧指标合理性
    knees = [r['knee_flexion'] for r in results]
    shins = [r['shin_angle'] for r in results]
    has_signed = all('left_shin_signed' in r and 'right_shin_signed' in r for r in results)
    assert has_signed, "缺少带符号小腿角"
    vis_ok = all(0.0 <= lm.visibility <= 1.0 for r in results for lm in r['landmarks'])
    assert vis_ok, "visibility 超出 [0,1]"
    print(f"knee range: {min(knees):.0f}-{max(knees):.0f}  shin range: {min(shins):.0f}-{max(shins):.0f}")
    assert 40 < min(knees) and max(knees) < 180, "膝盖角度超出合理范围"
    # 小腿角应覆盖合成数据的支撑(60°)/摆动(30°)两个区间
    assert max(shins) >= 55 and min(shins) <= 45, f"小腿角未覆盖支撑/摆动区间: {min(shins)}-{max(shins)}"

    # 步态：管线产出有效值即可（合成卡通图的步频精度不作硬性要求，
    # 步态算法精度已由 tests/test_gait_v2.py 用干净角度序列验证）
    g = GaitAnalyzer(fps=FPS).analyze(results)
    assert g is not None, "步态计算失败（帧数不足？）"
    print(f"gait: cadence={g['cadence']} steps={g['total_steps']} "
          f"symmetry={g['step_symmetry']} valid_ratio={g['valid_frame_ratio']}")
    assert g['cadence'] > 0 and g['total_steps'] > 0, f"步态未检出有效步数: {g}"
    sp = g['strike_posture']
    print(f"strike_posture: {sp}")

    print("\nE2E 通过 ✅  后端=tasks_heavy，检测率 {:.0%}，步频 {:.1f}，步数 {}".format(
        n_valid / N_FRAMES, g['cadence'], g['total_steps']))


if __name__ == '__main__':
    main()
