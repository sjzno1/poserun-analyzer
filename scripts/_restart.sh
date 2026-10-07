#!/bin/sh
# 容器内环境修复 + 重启 Flask
# venv 随容器重建丢失时，先重建 venv 并安装依赖
if [ ! -x /app/.venv/bin/python ]; then
  echo "venv missing, rebuilding..."
  python3 -m venv /app/.venv
  /app/.venv/bin/pip install -q -r /app/requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
  /app/.venv/bin/pip uninstall -y opencv-contrib-python opencv-python opencv-python-headless 2>/dev/null || true
  /app/.venv/bin/python -c "import shutil, pathlib; [shutil.rmtree(p, ignore_errors=True) for p in pathlib.Path('/app/.venv/lib/python3.11/site-packages').glob('cv2*')]"
  /app/.venv/bin/pip install -q opencv-python-headless==4.10.0.84 -i https://pypi.tuna.tsinghua.edu.cn/simple
  echo "venv rebuilt"
fi
for p in $(ls /proc/ | grep -E '^[0-9]+$'); do
  c=$(tr '\0' ' ' < /proc/$p/cmdline 2>/dev/null)
  case "$c" in *app.py*) kill $p 2>/dev/null;; esac
done
sleep 1
cd /app && nohup /app/.venv/bin/python app.py > /tmp/flask.log 2>&1 &
echo restarted
