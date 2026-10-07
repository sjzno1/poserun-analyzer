#!/usr/bin/env python3
"""pose_proxy.py - 宿主侧 TCP 代理：*:6000 -> poseRun 容器:8080
当 container CLI 的 -p 端口转发失效时使用。自动从 container list 获取最新容器 IP，
带缓存：container 命令暂时不可用时回退到上次已知 IP。
由 LaunchAgent com.sjz.pose-proxy 托管（KeepAlive 自动拉起）。
"""
import json
import os
import shutil
import socket
import subprocess
import threading

HOST_PORT = 6000
GUEST_PORT = 8080
CONTAINER = "poseRun"
# launchd 环境 PATH 不含 /opt/homebrew/bin，必须用绝对路径或先探测
CONTAINER_BIN = next(
    (p for p in ("/opt/homebrew/bin/container", "/usr/local/bin/container", "/usr/bin/container")
     if os.path.exists(p)),
    "container",
)

_ip_lock = threading.Lock()
_cached_ip = None


def get_guest_ip():
    global _cached_ip
    with _ip_lock:
        try:
            out = subprocess.run(
                [CONTAINER_BIN, "list", "--format", "json"],
                capture_output=True, text=True, timeout=10,
            ).stdout
            for c in json.loads(out):
                if c.get("id") == CONTAINER and c.get("status", {}).get("state") == "running":
                    ip = c["status"]["networks"][0]["ipv4Address"].split("/")[0]
                    _cached_ip = ip
                    return ip
        except Exception as e:
            print(f"container list failed: {e}", flush=True)
        return _cached_ip  # 回退缓存


def pipe(src, dst):
    try:
        while True:
            data = src.recv(65536)
            if not data:
                break
            dst.sendall(data)
    except OSError:
        pass
    finally:
        for s in (src, dst):
            try:
                s.shutdown(socket.SHUT_WR)
            except OSError:
                pass
            try:
                s.close()
            except OSError:
                pass


def handle(client, ip):
    try:
        remote = socket.create_connection((ip, GUEST_PORT), timeout=5)
    except OSError as e:
        print(f"connect to {ip}:{GUEST_PORT} failed: {e}", flush=True)
        client.close()
        return
    threading.Thread(target=pipe, args=(client, remote), daemon=True).start()
    threading.Thread(target=pipe, args=(remote, client), daemon=True).start()


def main():
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("0.0.0.0", HOST_PORT))
    srv.listen(64)
    print(f"pose proxy ready: 0.0.0.0:{HOST_PORT} -> {CONTAINER}:{GUEST_PORT} (bin={CONTAINER_BIN})", flush=True)
    while True:
        client, _ = srv.accept()
        ip = get_guest_ip()
        if not ip:
            client.close()
            continue
        threading.Thread(target=handle, args=(client, ip), daemon=True).start()


if __name__ == "__main__":
    main()
