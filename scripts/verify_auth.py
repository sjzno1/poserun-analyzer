"""NAS 端到端验证：从主机直连 192.168.3.24:8080 实测认证与人员管理"""
import json, urllib.request, urllib.error, sys, uuid, os

ip_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), '_poserun_ip.txt')
ip = open(ip_file).read().strip() if os.path.exists(ip_file) else '192.168.3.24'
BASE = f'http://{ip}:8080'
print(f'目标: {BASE}')
PASS, FAIL = [], []

def req(method, path, body=None, token=None, raw=False):
    r = urllib.request.Request(BASE + path, method=method)
    if body is not None:
        r.data = json.dumps(body).encode()
        r.add_header('Content-Type', 'application/json')
    if token:
        r.add_header('Authorization', 'Bearer ' + token)
    try:
        with urllib.request.urlopen(r, timeout=30) as resp:
            data = resp.read()
            return resp.status, (data if raw else json.loads(data))
    except urllib.error.HTTPError as e:
        data = e.read()
        try:
            return e.code, json.loads(data)
        except Exception:
            return e.code, data.decode(errors='replace')

def check(name, cond, extra=''):
    (PASS if cond else FAIL).append(name)
    print(('  PASS ' if cond else '  FAIL ') + name + (f' [{extra}]' if extra and not cond else ''))

# 1. 健康检查（免登录）
s, d = req('GET', '/api/health')
check('health 免登录 200', s == 200, str(s))

# 2. 首页含登录界面
s, d = req('GET', '/', raw=True)
check('首页 200', s == 200, str(s))
check('首页含登录/人员管理 UI', b'authOverlay' in d and b'users-section' in d)

# 3. 业务接口未登录 401
s, d = req('GET', '/api/history')
check('未登录访问业务接口 401', s == 401, str(s))

# 4. 注册
uname = 'authtest_' + uuid.uuid4().hex[:6]
s, d = req('POST', '/api/auth/register', {'username': uname, 'password': 'test1234', 'name': '测试用户'})
check('注册 201', s == 201, f'{s} {d}')
uid = (d.get('user') or {}).get('id')
check('注册返回用户信息', uid is not None, str(d))

# 5. 管理员登录
s, d = req('POST', '/api/auth/login', {'username': 'admin', 'password': 'admin123'})
check('admin 登录成功', s == 200 and d.get('token'), f'{s} {d}')
admin_tok = d.get('token', '')

# 6. 新用户登录
s, d = req('POST', '/api/auth/login', {'username': uname, 'password': 'test1234'})
check('新用户登录成功', s == 200 and d.get('token'), f'{s} {d}')
user_tok = d.get('token', '')

# 7. 当前用户
s, d = req('GET', '/api/auth/me', token=user_tok)
check('me 返回当前用户', s == 200 and (d.get('user') or {}).get('username') == uname, f'{s} {d}')

# 8. 带令牌访问业务接口
s, d = req('GET', '/api/history', token=user_tok)
check('带令牌访问业务接口 200', s == 200, str(s))

# 9. 人员列表（管理员）
s, d = req('GET', '/api/users', token=admin_tok)
check('管理员查看人员列表', s == 200 and d.get('total', 0) >= 2, f'{s} {d}')

# 10. 普通用户访问人员列表 403
s, d = req('GET', '/api/users', token=user_tok)
check('普通用户访问人员列表 403', s == 403, str(s))

# 11/12. 角色调整
s, d = req('PATCH', f'/api/users/{uid}', {'role': 'admin'}, token=admin_tok)
check('提升为管理员', s == 200, f'{s} {d}')
s, d = req('PATCH', f'/api/users/{uid}', {'role': 'user'}, token=admin_tok)
check('降回普通用户', s == 200, f'{s} {d}')

# 13. 重置密码
s, d = req('POST', f'/api/users/{uid}/password', {'password': 'reset5678'}, token=admin_tok)
check('重置密码', s == 200, f'{s} {d}')
s, d = req('POST', '/api/auth/login', {'username': uname, 'password': 'reset5678'})
check('新密码可登录', s == 200, f'{s} {d}')
s, d = req('POST', '/api/auth/login', {'username': uname, 'password': 'test1234'})
check('旧密码失效', s == 401, str(s))

# 14. 禁用账号
s, d = req('PATCH', f'/api/users/{uid}', {'active': 0}, token=admin_tok)
check('禁用账号', s == 200, f'{s} {d}')
s, d = req('POST', '/api/auth/login', {'username': uname, 'password': 'reset5678'})
check('禁用后无法登录', s == 401, f'{s} {d}')

# 15. 删除账号
s, d = req('DELETE', f'/api/users/{uid}', token=admin_tok)
check('删除账号', s == 200, f'{s} {d}')
s, d = req('GET', '/api/users?search=' + uname, token=admin_tok)
check('账号已不存在', s == 200 and d.get('total') == 0, f'{s} {d}')

# 16. 最后管理员保护
s, d = req('GET', '/api/users', token=admin_tok)
admin_id = [u for u in d.get('users', []) if u['role'] == 'admin'][0]['id']
s, d = req('DELETE', f'/api/users/{admin_id}', token=admin_tok)
check('不能删除最后一个管理员', s == 400, f'{s} {d}')

print('=' * 40)
print(f'结果: {len(PASS)} 通过, {len(FAIL)} 失败')
if FAIL:
    print('失败项:', FAIL)
    sys.exit(1)
print('ALL PASS ✅')
