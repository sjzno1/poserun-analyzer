#!/bin/bash
# restart_flask.sh - 在 poseRun 容器内重启 Flask 服务（使用 /app/.venv，随挂载卷持久保留）
VENV_PY=/app/.venv/bin/python
container exec poseRun bash -c "
for p in \$(ls /proc/ | grep -E '^[0-9]+\$'); do
  c=\$(tr '\0' ' ' < /proc/\$p/cmdline 2>/dev/null)
  case \"\$c\" in *\"app.py\"*) kill \$p 2>/dev/null;; esac
done
sleep 1
cd /app && nohup $VENV_PY app.py > /tmp/flask.log 2>&1 &
sleep 5
tail -2 /tmp/flask.log
"
# 等待宿主 6000 端口转发就绪（container 转发需要几秒）
for i in $(seq 1 15); do
  code=$(curl -s -m 3 -o /dev/null -w "%{http_code}" http://127.0.0.1:6000/ 2>/dev/null)
  [ "$code" = "200" ] && { echo "dashboard ready: HTTP $code"; exit 0; }
  sleep 2
done
echo "dashboard 未就绪，最后状态: $code（查看日志: container exec poseRun tail /tmp/flask.log）"
