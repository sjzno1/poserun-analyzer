// ===== PoseRun 共享脚本 v2.3（多页面版）=====
// 页面在引入本脚本前设置 window.PAGE = 'home' | 'upload' | 'results' | 'history' | 'camera' | 'users'
// 本脚本负责：登录/注册遮罩、导航栏、页脚注入，令牌校验，退出登录
window.PAGE = window.PAGE || 'home';

let authToken = localStorage.getItem('poserun_token') || null;
let authUser = null;
try { authUser = JSON.parse(localStorage.getItem('poserun_user') || 'null'); } catch (e) { authUser = null; }

// ===== 带鉴权的 fetch 封装 =====
async function apiFetch(url, options) {
    options = options || {};
    const headers = Object.assign({}, options.headers || {});
    if (authToken) headers['Authorization'] = 'Bearer ' + authToken;
    options.headers = headers;
    const resp = await fetch(url, options);
    if (resp.status === 401 && !url.startsWith('/api/auth/login') && !url.startsWith('/api/auth/register')) {
        clearSession();
        showAuthOverlay('登录已过期，请重新登录');
        throw new Error('unauthorized');
    }
    return resp;
}

function clearSession() {
    authToken = null;
    authUser = null;
    localStorage.removeItem('poserun_token');
    localStorage.removeItem('poserun_user');
}

// HTML转义
function escapeHtml(s) {
    return String(s == null ? '' : s)
        .replace(/&/g, '&amp;').replace(/</g, '&lt;')
        .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

// ===== 页面骨架注入：登录遮罩 + 导航栏 + 页脚 =====
function buildShell() {
    const navItems = [
        ['home', '/', '首页'],
        ['upload', '/upload.html', '视频上传'],
        ['results', '/results.html', '分析结果'],
        ['history', '/history.html', '历史记录'],
        ['camera', '/camera.html', '实时分析'],
    ];
    let nav = navItems.map(function (it) {
        const active = window.PAGE === it[0] ? ' active' : '';
        return '<li class="nav-item"><a class="nav-link' + active + '" href="' + it[1] + '">' + it[2] + '</a></li>';
    }).join('');
    const usersActive = window.PAGE === 'users' ? ' active' : '';
    nav += '<li class="nav-item" id="navUsersItem" style="display: none;">' +
        '<a class="nav-link' + usersActive + '" href="/users.html">人员管理</a></li>';

    document.body.insertAdjacentHTML('afterbegin',
        '<div id="authOverlay" class="auth-overlay" style="display: none;">' +
        '  <div class="auth-card">' +
        '    <div class="auth-header">' +
        '      <div class="auth-logo">🏃</div>' +
        '      <h4>PoseRun 跑姿分析系统</h4>' +
        '      <p>登录后开始跑姿分析</p>' +
        '    </div>' +
        '    <div class="auth-body">' +
        '      <ul class="nav nav-tabs auth-tabs" id="authTabs">' +
        '        <li class="nav-item"><a class="nav-link active" id="tabLogin" data-bs-toggle="tab" href="#paneLogin">登录</a></li>' +
        '        <li class="nav-item"><a class="nav-link" id="tabRegister" data-bs-toggle="tab" href="#paneRegister">注册</a></li>' +
        '      </ul>' +
        '      <div class="tab-content mt-3">' +
        '        <div class="tab-pane fade show active" id="paneLogin">' +
        '          <div class="mb-3"><label class="form-label">用户名</label>' +
        '          <input type="text" class="form-control" id="loginUsername" autocomplete="username" maxlength="32"></div>' +
        '          <div class="mb-3"><label class="form-label">密码</label>' +
        '          <input type="password" class="form-control" id="loginPassword" autocomplete="current-password"></div>' +
        '          <button class="btn btn-primary w-100" id="loginBtn">登 录</button>' +
        '        </div>' +
        '        <div class="tab-pane fade" id="paneRegister">' +
        '          <div class="mb-3"><label class="form-label">用户名 <span class="text-muted small">（3-32位，字母/数字/下划线）</span></label>' +
        '          <input type="text" class="form-control" id="regUsername" autocomplete="username" maxlength="32"></div>' +
        '          <div class="mb-3"><label class="form-label">姓名（用于报告署名）</label>' +
        '          <input type="text" class="form-control" id="regName" maxlength="20"></div>' +
        '          <div class="mb-3"><label class="form-label">密码 <span class="text-muted small">（至少6位）</span></label>' +
        '          <input type="password" class="form-control" id="regPassword" autocomplete="new-password"></div>' +
        '          <div class="mb-3"><label class="form-label">确认密码</label>' +
        '          <input type="password" class="form-control" id="regPassword2" autocomplete="new-password"></div>' +
        '          <button class="btn btn-primary w-100" id="registerBtn">注 册</button>' +
        '        </div>' +
        '      </div>' +
        '      <div id="authError" class="alert mt-3" style="display: none;"></div>' +
        '      <p class="auth-hint text-center mt-2 mb-0">首次使用？切换到"注册"创建账号</p>' +
        '    </div>' +
        '  </div>' +
        '</div>' +
        '<nav class="navbar navbar-expand-lg navbar-light fixed-top">' +
        '  <div class="container">' +
        '    <a class="navbar-brand" href="/"><i class="fas fa-running me-2"></i>PoseRun 跑姿分析系统</a>' +
        '    <button class="navbar-toggler" type="button" data-bs-toggle="collapse" data-bs-target="#navbarNav"><span class="navbar-toggler-icon"></span></button>' +
        '    <div class="collapse navbar-collapse" id="navbarNav">' +
        '      <ul class="navbar-nav ms-auto align-items-lg-center">' +
        nav +
        '        <li class="nav-item dropdown nav-user ms-lg-2">' +
        '          <a class="nav-link dropdown-toggle" href="#" role="button" data-bs-toggle="dropdown" id="navUserLabel">' +
        '            <i class="fas fa-user-circle me-1"></i><span id="navUserName">-</span>' +
        '          </a>' +
        '          <ul class="dropdown-menu dropdown-menu-end">' +
        '            <li><span class="dropdown-item-text small text-muted" id="navUserDetail"></span></li>' +
        '            <li><hr class="dropdown-divider"></li>' +
        '            <li><a class="dropdown-item" href="#" id="navLogoutBtn"><i class="fas fa-sign-out-alt me-2"></i>退出登录</a></li>' +
        '          </ul>' +
        '        </li>' +
        '      </ul>' +
        '    </div>' +
        '  </div>' +
        '</nav>');

    document.body.insertAdjacentHTML('beforeend',
        '<footer class="footer">' +
        '  <div class="container">' +
        '    <p class="mb-0">PoseRun 跑姿分析系统 v2.5.0 | 基于 MediaPipe 的姿态识别技术</p>' +
        '    <p class="mb-0">技术支持: sunjianzhong@gmail.com</p>' +
        '  </div>' +
        '</footer>');
}

// ===== 登录/注册界面 =====
const authOverlay = document.getElementById('authOverlay');
const authErrorBox = document.getElementById('authError');

function showAuthOverlay(msg) {
    authOverlay.style.display = 'flex';
    if (msg) {
        authErrorBox.textContent = msg;
        authErrorBox.style.display = 'block';
    }
}

function hideAuthOverlay() {
    authOverlay.style.display = 'none';
    authErrorBox.style.display = 'none';
}

function applyAuthedUI() {
    hideAuthOverlay();
    const name = (authUser && (authUser.name || authUser.username)) || '用户';
    const isAdmin = authUser && authUser.role === 'admin';
    document.getElementById('navUserName').textContent = name;
    document.getElementById('navUserDetail').innerHTML =
        '用户名: ' + escapeHtml(authUser.username) +
        ' <span class="badge ' + (isAdmin ? 'badge-admin' : 'badge-user') + ' ms-1">' +
        (isAdmin ? '管理员' : '普通用户') + '</span>';
    document.getElementById('navUsersItem').style.display = isAdmin ? '' : 'none';
    // 页面级钩子：各页面可定义 onAuthReady(user) 做初始化
    if (typeof onAuthReady === 'function') onAuthReady(authUser);
}

async function doLogin() {
    const username = document.getElementById('loginUsername').value.trim();
    const password = document.getElementById('loginPassword').value;
    if (!username || !password) { showAuthOverlay('请输入用户名和密码'); return; }
    try {
        const resp = await apiFetch('/api/auth/login', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ username: username, password: password })
        });
        const data = await resp.json();
        if (!resp.ok) { showAuthOverlay(data.error || '登录失败'); return; }
        authToken = data.token;
        authUser = data.user;
        localStorage.setItem('poserun_token', authToken);
        localStorage.setItem('poserun_user', JSON.stringify(authUser));
        applyAuthedUI();
    } catch (err) {
        showAuthOverlay('网络错误，请稍后重试');
    }
}

async function doRegister() {
    const username = document.getElementById('regUsername').value.trim();
    const name = document.getElementById('regName').value.trim();
    const p1 = document.getElementById('regPassword').value;
    const p2 = document.getElementById('regPassword2').value;
    if (!username || !p1) { showAuthOverlay('请填写用户名和密码'); return; }
    if (p1 !== p2) { showAuthOverlay('两次输入的密码不一致'); return; }
    try {
        const resp = await apiFetch('/api/auth/register', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ username: username, name: name, password: p1 })
        });
        const data = await resp.json();
        if (!resp.ok) { showAuthOverlay(data.error || '注册失败'); return; }
        // 注册成功后自动登录
        document.getElementById('loginUsername').value = username;
        document.getElementById('loginPassword').value = p1;
        authErrorBox.style.display = 'none';
        await doLogin();
    } catch (err) {
        showAuthOverlay('网络错误，请稍后重试');
    }
}

// ===== 初始化：注入骨架 + 绑定事件 + 校验本地令牌 =====
document.addEventListener('DOMContentLoaded', async function () {
    buildShell();
    document.getElementById('loginBtn').addEventListener('click', doLogin);
    document.getElementById('registerBtn').addEventListener('click', doRegister);
    document.getElementById('loginPassword').addEventListener('keydown', function (e) { if (e.key === 'Enter') doLogin(); });
    document.getElementById('navLogoutBtn').addEventListener('click', async function (e) {
        e.preventDefault();
        try { await apiFetch('/api/auth/logout', { method: 'POST' }); } catch (err) { /* 忽略 */ }
        clearSession();
        location.reload();
    });

    if (authToken) {
        try {
            const resp = await apiFetch('/api/auth/me');
            if (resp.ok) {
                const data = await resp.json();
                authUser = data.user;
                localStorage.setItem('poserun_user', JSON.stringify(authUser));
                applyAuthedUI();
                return;
            }
        } catch (e) { /* 令牌失效 */ }
        clearSession();
    }
    showAuthOverlay();
});
