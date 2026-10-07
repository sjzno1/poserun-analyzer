"""无依赖测试：验证小腿角度几何 + 跑步阶段识别（纯数学，绕过 mediapipe）"""
import sys, types, math, importlib.util

# stub mediapipe so pose_analyzer can import
mp = types.ModuleType('mediapipe')
mp.solutions = types.SimpleNamespace(pose=types.SimpleNamespace(Pose=lambda *a,**k:None, PoseLandmark=lambda *a:None))
mp.solutions.drawing_utils = None
sys.modules['mediapipe'] = mp
# stub cv2 & numpy
sys.modules['cv2'] = types.ModuleType('cv2')
np = types.ModuleType('numpy'); np.array=list; np.ndarray=object; np.linalg=types.SimpleNamespace(norm=lambda v: math.hypot(*v))
sys.modules['numpy']=np

spec = importlib.util.spec_from_file_location('pose_analyzer', 'pose_analyzer.py')
pa = importlib.util.module_from_spec(spec); spec.loader.exec_module(pa)

# 绕过 __init__（不需要 mediapipe）
a = object.__new__(pa.PoseAnalyzer)

def LM(x,y): return pa.Landmark('p',x,y,0.0)

# 1. 小腿竖直（knee 在 ankle 正上方，像素 y 向下 => ankle.y > knee.y）
# 归一化坐标 y 向下为正（与像素一致），to_pixel 直接 y*h，所以 knee 在上方 => knee.y 小
knee = LM(0.5, 0.4); ankle = LM(0.5, 0.7)  # 竖直
ang = a._calculate_angle_with_ground(knee, ankle, 100, 100)
print('vertical shin angle = %.1f (应≈90)' % ang)
assert 85 <= ang <= 90, ang

# 2. 小腿前倾 45°（ankle 在 knee 右下方等距）
knee = LM(0.5, 0.6); ankle = LM(0.6, 0.5)
ang = a._calculate_angle_with_ground(knee, ankle, 100, 100)
print('45deg shin angle = %.1f (应≈45)' % ang)
assert 40 <= ang <= 50, ang

# 3. 跑步阶段
pts = {'LEFT_HIP':LM(0.5,0.5),'RIGHT_HIP':LM(0.5,0.5),
       'LEFT_KNEE':LM(0.5,0.3),'RIGHT_KNEE':LM(0.5,0.3),
       'LEFT_ANKLE':LM(0.5,0.1),'RIGHT_ANKLE':LM(0.5,0.1),
       'LEFT_SHOULDER':LM(0.5,0.7),'RIGHT_SHOULDER':LM(0.5,0.7)}
# 竖直小腿 + 膝盖微弯 => 支撑期
m = {'shin_angle':88,'knee_flexion':155}
print('phase (shin=88,knee=155) =', a._detect_running_phase(pts, m))
assert a._detect_running_phase(pts, m) == 'toe_off'
# 脚跟刚触地（小腿略前倾）
m = {'shin_angle':75,'knee_flexion':155}
print('phase (shin=75,knee=155) =', a._detect_running_phase(pts, m))
assert a._detect_running_phase(pts, m) == 'heel_strike'
# 摆动期（小腿角度小 / 膝盖大幅弯曲）
m = {'shin_angle':40,'knee_flexion':175}
print('phase (shin=40,knee=175) =', a._detect_running_phase(pts, m))
assert a._detect_running_phase(pts, m) == 'swing'

print('\nPOSE MATH PASSED ✔')
