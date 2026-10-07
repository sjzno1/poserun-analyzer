#!/bin/sh
# poseRun 环境幂等修复脚本（mediapipe 1.x 升级后重建容器用）
# 作用：确保容器内 mediapipe==1.0.1、libEGL 系统库、cv2 headless 4.10.0.84 就位。
# 幂等：已满足则快速跳过；不满足才执行对应修复。
set -u
OUT=/tmp/ensure_env.txt
{
  echo "START $(date)"
  MP_VER=$(python3 -c "import mediapipe; print(mediapipe.__version__)" 2>/dev/null | tail -1)
  echo "current mediapipe: ${MP_VER:-none}"

  # 1) mediapipe 版本
  if [ "$MP_VER" != "1.0.1" ]; then
    echo ">>> 升级 mediapipe -> 1.0.1"
    pip install mediapipe==1.0.1 2>&1 | tail -3
  else
    echo "mediapipe 已是 1.0.1，跳过"
  fi

  # 2) libEGL 系统库（mediapipe 1.x 需要，0.10.14 不需要）
  if ! find / -name "libEGL.so*" 2>/dev/null | grep -q .; then
    echo ">>> 安装 libEGL/libGLES 系统库"
    apt-get update -qq 2>&1 | tail -1
    apt-get install -y -qq libegl1 libgles2 2>&1 | tail -2
    ldconfig
  else
    echo "libEGL 已存在，跳过"
  fi

  # 3) cv2 headless（contrib 5.x 会污染 cv2，需确保 headless 4.10.0.84 可用）
  CV2_OK=$(python3 -c "import cv2; print(cv2.__version__)" 2>/dev/null | tail -1)
  if [ "$CV2_OK" != "4.10.0" ]; then
    echo ">>> 修复 cv2（当前: ${CV2_OK:-broken}）"
    pip uninstall -y opencv-contrib-python 2>&1 | tail -1
    python3 -c "
import shutil, glob
for d in glob.glob('/usr/local/lib/python3.11/site-packages/cv2*'):
    shutil.rmtree(d, ignore_errors=True)
"
    pip install -q --force-reinstall --no-deps opencv-python-headless==4.10.0.84 2>&1 | tail -1
  else
    echo "cv2 已是 4.10.0，跳过"
  fi

  # 4) 最终校验
  echo "=== 校验 ==="
  python3 -c "import mediapipe; print('mediapipe', mediapipe.__version__)" 2>&1 | tail -1
  python3 -c "import cv2; print('cv2', cv2.__version__)" 2>&1 | tail -1
  python3 -c "from mediapipe.tasks.python.vision import PoseLandmarker; print('tasks OK')" 2>&1 | tail -1
  python3 -c "
import sys; sys.path.insert(0,'/app')
from pose_analyzer import PoseAnalyzer
a=PoseAnalyzer(); print('backend:', a.backend); a.close()
" 2>&1 | grep -v "^W0000\|inference_feedback\|WARNING\|INFO"
  echo "DONE $(date)"
} > "$OUT" 2>&1
