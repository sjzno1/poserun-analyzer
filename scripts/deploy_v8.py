#!/usr/bin/env python3
"""
poseRun 容器部署脚本（v8：认证 + 人员管理）
流程：
  1. 读取当前 poseRun 容器配置（镜像/Env/挂载/静态IP）
  2. stop + delete 旧容器
  3. 用原镜像重建，新增 /share/Container/poserun/data -> /app/data 持久化挂载
  4. 启动 + 健康检查
  5. archive PUT 推送新代码（auth.py / app.py / static/index.html / _restart.sh）
  6. exec 重启 Flask
  7. 健康检查
所有 NAS Docker API 调用走 curl（TLS 已验证可用）。
"""
import json, os, shlex, subprocess, sys, tarfile, time, urllib.request

CERT_DIR = '/Users/sjz/.hermes/secrets/docker-nas'
B = 'https://192.168.3.2:2376'
APP_DIR = '/Users/sjz/projects/poserun-analyzer'
SCRATCH = os.path.dirname(os.path.abspath(__file__))
CONTAINER = 'poseRun'
NEW_BIND = '/share/Container/poserun/data:/app/data'

BASE_IP = '192.168.3.24'  # 若容器实际IP不同，步骤2会打印出来，脚本会自动采用发现的IP

def curl(method, path, data=None, ctype=None, max_time=60):
    cmd = ['curl', '-s', '--max-time', str(max_time), '-X', method,
           '--cacert', 'ca.pem', '--cert', 'cert.pem', '--key', 'key.pem']
    if ctype:
        cmd += ['-H', 'Content-Type: ' + ctype]
    if data is not None:
        if isinstance(data, (dict, list)):
            data = json.dumps(data)
        cmd += ['--data-binary', '@-']
    cmd.append(B + path)
    r = subprocess.run(cmd, cwd=CERT_DIR, input=data.encode() if isinstance(data, str) else data,
                       capture_output=True, timeout=max_time + 30)
    if r.returncode != 0:
        raise RuntimeError(f'curl {method} {path} failed: {r.stderr.decode()[:300]}')
    return r.stdout

def curl_json(method, path, data=None, ctype=None, max_time=60):
    out = curl(method, path, data=data, ctype=ctype, max_time=max_time)
    if not out:
        return {}
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        return {'_raw': out.decode(errors='replace')}

def http_get(path, timeout=10):
    try:
        with urllib.request.urlopen(f'http://{BASE_IP}:8080' + path, timeout=timeout) as r:
            return r.status
    except Exception:
        return None

def wait_health(what, tries=30, delay=3):
    for i in range(tries):
        if http_get('/api/health') == 200:
            print(f'  health OK ({what})')
            return True
        time.sleep(delay)
    print(f'  health FAIL after {tries*delay}s ({what})')
    return False


def deploy_code(cid):
    """推送新代码（archive PUT）并校验"""
    tar1 = os.path.join(SCRATCH, '_deploy_root.tar')
    with tarfile.open(tar1, 'w') as t:
        for f in ('auth.py', 'app.py', 'pose_analyzer.py', 'history.py', 'gait_analyzer.py', 'gait_metrics.py', 'smoothing.py'):
            t.add(os.path.join(APP_DIR, f), arcname=f)
        t.add(os.path.join(SCRATCH, '_restart.sh'), arcname='_restart.sh')
        t.add(os.path.join(SCRATCH, 'ensure_env.sh'), arcname='ensure_env.sh')
    with open(tar1, 'rb') as fh:
        r = curl('PUT', f'/containers/{cid}/archive?path=/app/', fh.read(), ctype='application/x-tar')
    print('  /app PUT:', len(r), 'bytes resp')
    tar2 = os.path.join(SCRATCH, '_deploy_static.tar')
    with tarfile.open(tar2, 'w') as t:
        for f in ('index.html', 'upload.html', 'results.html', 'camera.html',
                  'users.html', 'history.html', 'common.css', 'common.js'):
            t.add(os.path.join(APP_DIR, 'static', f), arcname=f)
    with open(tar2, 'rb') as fh:
        r = curl('PUT', f'/containers/{cid}/archive?path=/app/static/', fh.read(), ctype='application/x-tar')
    print('  /app/static PUT:', len(r), 'bytes resp')
    # 验证文件到位
    ex = curl_json('POST', f'/containers/{cid}/exec',
                   {'Cmd': ['sh', '-c', 'rm -f /tmp/chk.txt; ls -la /app/auth.py /app/app.py /app/pose_analyzer.py /app/history.py /app/gait_analyzer.py /app/gait_metrics.py /app/smoothing.py /app/static/index.html /app/static/upload.html /app/static/results.html /app/static/camera.html /app/static/users.html /app/static/history.html /app/static/common.css /app/static/common.js > /tmp/chk.txt 2>&1; grep -c auth_manager /app/app.py >> /tmp/chk.txt; grep -c history_store /app/app.py >> /tmp/chk.txt; grep -c gait_metrics /app/app.py >> /tmp/chk.txt']},
                   ctype='application/json')
    curl_json('POST', f"/exec/{ex['Id']}/start", {'Detach': True}, ctype='application/json')
    time.sleep(3)
    chk = curl('GET', f'/containers/{cid}/archive?path=/tmp/chk.txt')
    import io
    tf = tarfile.open(fileobj=io.BytesIO(chk))
    content = tf.extractfile('chk.txt').read().decode(errors='replace')
    print('  容器内文件检查:')
    print('   ' + content.replace('\n', '\n   '))
    lines = [l.strip() for l in content.splitlines() if l.strip()]
    if 'auth.py' not in content or 'pose_analyzer.py' not in content or 'history.py' not in content or 'gait_analyzer.py' not in content or 'smoothing.py' not in content:
        sys.exit('代码推送校验失败：缺少文件')
    # 最后三行应为 grep 计数（auth_manager>0, history_store>0, gait_metrics>0）
    if len(lines) < 3 or not all(lines[i].isdigit() for i in (-3, -2, -1)) or any(int(lines[i]) <= 0 for i in (-3, -2, -1)):
        sys.exit('代码推送校验失败：app.py 未包含新代码')


def ensure_env(cid):
    """幂等环境修复：mediapipe 1.0.1 + libEGL + cv2 headless（重建容器后必跑）"""
    # 先清理上次运行残留，避免读到旧结果
    curl_json('POST', f'/containers/{cid}/exec',
              {'Cmd': ['sh', '-c', 'rm -f /tmp/ensure_env.txt']},
              ctype='application/json')
    ex = curl_json('POST', f'/containers/{cid}/exec',
                   {'Cmd': ['sh', '/app/ensure_env.sh']},
                   ctype='application/json')
    curl_json('POST', f"/exec/{ex['Id']}/start", {'Detach': True}, ctype='application/json')
    import io
    for i in range(40):
        time.sleep(10)
        try:
            blob = curl('GET', f'/containers/{cid}/archive?path=/tmp/ensure_env.txt')
            tf = tarfile.open(fileobj=io.BytesIO(blob))
            content = tf.extractfile('ensure_env.txt').read().decode(errors='replace')
        except Exception:
            content = ''
        if 'DONE' in content:
            print('  环境校验:')
            for line in content.splitlines():
                if line.startswith(('mediapipe', 'cv2', 'tasks', 'backend', 'DONE')):
                    print('   ', line)
            if 'backend: tasks_heavy' not in content:
                print('   完整输出:')
                print('   ' + content.replace('\n', '\n   '))
                sys.exit('环境修复后 pose 后端不是 tasks_heavy，中止')
            return
    print('  环境修复超时（400s），请手动检查容器内 /tmp/ensure_env.txt')
    sys.exit('ensure_env 超时')


def restart_flask(cid):
    """重启容器（CMD=python app.py 会加载新代码，镜像内 python 已含全部依赖）"""
    curl('POST', f'/containers/{cid}/restart?t=30')
    if not wait_health('after code deploy', tries=40, delay=3):
        out = curl('GET', f'/containers/{cid}/logs?stdout=1&stderr=1&tail=30')
        print(out.decode(errors='replace'))
        sys.exit('部署后服务未起来')
    st = http_get('/api/auth/me')
    print('  /api/auth/me 状态码:', st, '(401=新代码已加载)')
    if st != 401:
        print('  警告: 新路由未生效，请检查容器日志')
    with open(os.path.join(SCRATCH, '_poserun_ip.txt'), 'w') as f:
        f.write(BASE_IP)
    print('\nDEPLOY OK ✅')

def main():
    global BASE_IP
    print('[1/7] preflight: Docker API')
    v = curl_json('GET', '/version')
    print('  docker:', v.get('Version'))
    if not v.get('Version'):
        sys.exit('Docker API 不可达')

    print('[2/7] 读取当前容器配置')
    # 先列出所有容器，按名字/镜像定位 poseRun
    lst = curl_json('GET', '/containers/json?all=true')
    if isinstance(lst, dict) and 'message' in lst:
        print('  list 失败:', lst)
        sys.exit(1)
    names = [(c.get('Names', ['?'])[0].lstrip('/'), c.get('Image'), c.get('State')) for c in lst]
    print('  全部容器:')
    for n, im, st in names:
        print(f'    {n}  [{st}]  {im}')
    target = None
    for c in lst:
        nm = c.get('Names', [''])[0].lstrip('/')
        if nm == CONTAINER or CONTAINER.lower() in nm.lower() or (c.get('Image') or '').lower().startswith('poserun'):
            target = c
            break
    if not target:
        # 回退：使用上次保存的配置重建（容器已被删除的场景）
        saved = os.path.join(SCRATCH, '_poserun_cfg.json')
        if not os.path.exists(saved):
            sys.exit('未找到 poseRun 容器，也没有已保存配置，中止')
        print('  容器不存在，使用已保存配置恢复:', saved)
        with open(saved) as f:
            saved_cfg = json.load(f)
        image = saved_cfg['image']
        env = saved_cfg['env']
        binds = saved_cfg['binds']
        privileged = saved_cfg['privileged']
        restart = saved_cfg['restart']
        cmd = saved_cfg.get('cmd')
        entrypoint = saved_cfg.get('entrypoint')
        labels = saved_cfg.get('labels') or {}
        net_name, net_ip = saved_cfg['net_name'], saved_cfg['net_ip']
        orig_name = saved_cfg.get('orig_name', 'poserun-analyzer')
        BASE_IP = net_ip
        print(f'  恢复: image={image} net={net_name} ip={net_ip}')
        if NEW_BIND not in binds:
            binds.append(NEW_BIND)
        for e in ('ADMIN_USERNAME=admin', 'ADMIN_PASSWORD=admin123'):
            if not any(x.startswith(e.split('=')[0] + '=') for x in env):
                env.append(e)
        print('[3/7] 跳过（容器已不存在）')
        print('[4/7] 重建容器（+ data 持久化挂载）')
        payload = {
            'Image': image,
            'Env': env,
            'Labels': labels,
            'ExposedPorts': {'8080/tcp': {}},
            'HostConfig': {
                'Binds': binds,
                'Privileged': privileged,
                'RestartPolicy': restart,
            },
            'NetworkingConfig': {
                'EndpointsConfig': {
                    net_name: {'IPAMConfig': {'IPv4Address': net_ip}}
                }
            },
        }
        if cmd:
            payload['Cmd'] = cmd
        if entrypoint:
            payload['Entrypoint'] = entrypoint
        r = curl_json('POST', f'/containers/create?name={orig_name}', data=payload, ctype='application/json')
        if 'Id' not in r:
            sys.exit(f'create 失败: {r}')
        cid = r['Id']
        print('  created:', cid[:12])
        curl('POST', f'/containers/{cid}/start')
        print('[5/7] 等待健康检查')
        if not wait_health('after recreate'):
            out = curl('GET', f'/containers/{cid}/logs?stdout=1&stderr=1&tail=40')
            print(out.decode(errors='replace'))
            sys.exit('重建后服务未起来')
        print('[6/7] 推送新代码（archive PUT）')
        deploy_code(cid)
        print('[7/7] 环境修复 + 重启 Flask 并等待健康检查')
        ensure_env(cid)
        restart_flask(cid)
        return
    cid0 = target['Id']
    print(f'  目标容器: {cid0[:12]} names={target.get("Names")} state={target.get("State")}')
    cfg = curl_json('GET', f'/containers/{cid0}/json')
    if not isinstance(cfg, dict) or 'Image' not in cfg:
        print('  inspect 返回异常:', str(cfg)[:500])
        sys.exit('无法 inspect 容器')
    image = cfg['Image']
    orig_name = target.get('Names', ['poserun-analyzer'])[0].lstrip('/')
    # 保存配置快照，供容器被删后的恢复
    import tarfile as _tf
    nets_tmp = cfg.get('NetworkSettings', {}).get('Networks') or {}
    net_tmp = next(((n, i.get('IPAddress')) for n, i in nets_tmp.items() if i.get('IPAddress')), (None, None))
    with open(os.path.join(SCRATCH, '_poserun_cfg.json'), 'w') as f:
        json.dump({
            'image': image,
            'env': list(cfg.get('Config', {}).get('Env') or []),
            'binds': list(cfg.get('HostConfig', {}).get('Binds') or []),
            'privileged': bool(cfg.get('HostConfig', {}).get('Privileged')),
            'restart': cfg.get('HostConfig', {}).get('RestartPolicy') or {'Name': 'always'},
            'cmd': cfg.get('Config', {}).get('Cmd'),
            'entrypoint': cfg.get('Config', {}).get('Entrypoint'),
            'labels': cfg.get('Config', {}).get('Labels') or {},
            'net_name': net_tmp[0],
            'net_ip': net_tmp[1],
            'orig_name': orig_name,
        }, f, indent=2)
    print('  配置已保存 ->', os.path.join(SCRATCH, '_poserun_cfg.json'))
    env = list(cfg.get('Config', {}).get('Env') or [])
    binds = list(cfg.get('HostConfig', {}).get('Binds') or [])
    privileged = bool(cfg.get('HostConfig', {}).get('Privileged'))
    restart = cfg.get('HostConfig', {}).get('RestartPolicy') or {'Name': 'always'}
    cmd = cfg.get('Config', {}).get('Cmd')
    entrypoint = cfg.get('Config', {}).get('Entrypoint')
    labels = cfg.get('Config', {}).get('Labels') or {}
    nets = cfg.get('NetworkSettings', {}).get('Networks') or {}
    net_name, net_ip = None, None
    for n, info in nets.items():
        if info.get('IPAddress'):
            net_name, net_ip = n, info['IPAddress']
    print(f'  image={image}')
    print(f'  net={net_name} ip={net_ip} privileged={privileged} restart={restart}')
    if net_ip:
        BASE_IP = net_ip
        print(f'  健康检查将使用 {BASE_IP}:8080')
    print(f'  binds={binds}')
    if not net_name or not net_ip:
        sys.exit('未读到静态IP，中止（避免随机IP）')

    if NEW_BIND not in binds:
        binds.append(NEW_BIND)
    for e in ('ADMIN_USERNAME=admin', 'ADMIN_PASSWORD=admin123'):
        if not any(x.startswith(e.split('=')[0] + '=') for x in env):
            env.append(e)

    print('[3/7] stop + delete 旧容器')
    curl('POST', f'/containers/{cid0}/stop?t=30')
    time.sleep(2)
    curl('DELETE', f'/containers/{cid0}?force=1')
    time.sleep(2)

    print('[4/7] 重建容器（+ data 持久化挂载）')
    payload = {
        'Image': image,
        'Env': env,
        'Labels': labels,
        'ExposedPorts': {'8080/tcp': {}},
        'HostConfig': {
            'Binds': binds,
            'Privileged': privileged,
            'RestartPolicy': restart,
        },
        'NetworkingConfig': {
            'EndpointsConfig': {
                net_name: {'IPAMConfig': {'IPv4Address': net_ip}}
            }
        },
    }
    if cmd:
        payload['Cmd'] = cmd
    if entrypoint:
        payload['Entrypoint'] = entrypoint
    r = curl_json('POST', f'/containers/create?name={orig_name}', data=payload, ctype='application/json')
    if 'Id' not in r:
        sys.exit(f'create 失败: {r}')
    cid = r['Id']
    print('  created:', cid[:12])
    curl('POST', f'/containers/{cid}/start')

    print('[5/7] 等待健康检查（旧代码也应能起来）')
    if not wait_health('after recreate'):
        # 打印日志辅助诊断
        out = curl('GET', f'/containers/{cid}/logs?stdout=1&stderr=1&tail=40')
        print(out.decode(errors='replace'))
        sys.exit('重建后服务未起来')

    print('[6/7] 推送新代码（archive PUT）')
    deploy_code(cid)

    print('[7/7] 环境修复 + 重启 Flask 并等待健康检查')
    ensure_env(cid)
    restart_flask(cid)

if __name__ == '__main__':
    main()
