# PoseRun Analyzer - 姿势跑法分析系统

基于Docker部署的跑姿分析系统，使用MediaPipe进行姿态识别，自动生成标准化的跑步技术评估报告。

## 功能特性

- 🎥 视频处理：支持MP4、AVI、MOV等主流视频格式
- 🏃 姿态识别：MediaPipe Pose检测33个身体关键点
- 📊 指标计算：膝盖角度、小腿角度、跨步分析等关键指标
- 📋 报告生成：基于模板的标准化Word/PDF格式报告
- 🖼️ 关键帧标注：自动提取并标注关键跑步帧
- 📈 历史对比：支持多次分析结果对比
- 📁 批量处理：支持文件夹批量视频处理
- 🔐 用户认证：注册/登录，令牌鉴权（7天有效）
- 👥 人员管理：管理员可管理全部用户（角色/状态/密码/删除）
- 🐳 容器化：Docker一键部署，开箱即用

## 技术架构

```
PoseRun Analyzer v2.0.0
├── 核心技术
│   ├── Python 3.11 + Flask API
│   ├── OpenCV视频处理
│   ├── MediaPipe Pose姿态识别
│   └── python-docx报告生成
├── API接口
│   ├── POST /api/auth/register - 注册账号
│   ├── POST /api/auth/login - 登录（返回令牌）
│   ├── GET /api/auth/me - 当前登录用户
│   ├── POST /api/auth/logout - 登出
│   ├── GET /api/users - 人员列表（管理员）
│   ├── PATCH /api/users/<id> - 修改角色/姓名/状态（管理员）
│   ├── POST /api/users/<id>/password - 重置密码（管理员）
│   ├── DELETE /api/users/<id> - 删除用户（管理员）
│   ├── POST /api/upload - 上传视频（需登录）
│   ├── POST /api/analyze - 基本姿态分析（需登录）
│   ├── POST /api/analyze/advanced - 高级分析（含历史对比）（需登录）
│   ├── GET /api/report/<filename> - 下载报告（需登录）
│   ├── GET /api/keyframes/<path> - 获取关键帧（需登录）
│   ├── GET /api/keyframes/<path>/download - 下载关键帧（打包zip）（需登录）
│   ├── GET /api/history - 历史数据分析（需登录）
│   ├── DELETE /api/history/delete/<id> - 删除历史记录（需登录）
│   ├── POST /api/batch - 批量处理视频（需登录）
│   └── GET /api/health - 健康检查（免登录）
└── 输出格式
    ├── Word文档 (.docx)
    ├── 关键帧标注图片 (.jpg)
    ├── 关键帧标注信息 (.json)
    └── PDF文档 (可选导出)
```

## 快速开始

### 方式一：Docker一键部署（推荐）

```bash
# 1. 构建Docker镜像
docker-compose build

# 2. 启动服务
docker-compose up -d

# 3. 检查服务状态
docker-compose ps

# 4. 查看日志
docker-compose logs -f
```

### 方式二：本地安装

```bash
# 1. 安装Python 3.11+
python3 --version

# 2. 创建虚拟环境
python3 -m venv venv
source venv/bin/activate  # Linux/Mac
# 或
venv\Scripts\activate  # Windows

# 3. 安装依赖
pip install -r requirements.txt

# 4. 运行服务
python app.py
```

## API接口文档

### 认证

> 除 `/api/health`、`/api/auth/login`、`/api/auth/register` 外，所有接口都需要在请求头携带 `Authorization: Bearer <令牌>`。

**首次启动默认管理员**：`admin` / `admin123`（可用环境变量 `ADMIN_USERNAME` / `ADMIN_PASSWORD` 覆盖，请登录后尽快修改）。

### 1. 注册
```bash
curl -X POST http://localhost:5000/api/auth/register \
  -H "Content-Type: application/json" \
  -d '{"username": "zhangsan", "password": "secret123", "name": "张三"}'
```
响应：`{"success": true, "user": {"id": 2, "username": "zhangsan", "name": "张三", "role": "user", ...}}`

### 2. 登录
```bash
curl -X POST http://localhost:5000/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username": "zhangsan", "password": "secret123"}'
```
响应：`{"success": true, "token": "<7天有效令牌>", "user": {...}}`

### 3. 当前用户
```bash
curl http://localhost:5000/api/auth/me -H "Authorization: Bearer <token>"
```

### 4. 人员管理（仅管理员）
```bash
# 列表 / 搜索
curl "http://localhost:5000/api/users?search=张" -H "Authorization: Bearer <token>"

# 提升/降级角色、启用/禁用、改姓名
curl -X PATCH http://localhost:5000/api/users/2 \
  -H "Authorization: Bearer <token>" -H "Content-Type: application/json" \
  -d '{"role": "admin"}'
curl -X PATCH http://localhost:5000/api/users/2 \
  -H "Authorization: Bearer <token>" -H "Content-Type: application/json" \
  -d '{"active": 0}'

# 重置密码
curl -X POST http://localhost:5000/api/users/2/password \
  -H "Authorization: Bearer <token>" -H "Content-Type: application/json" \
  -d '{"password": "newpass456"}'

# 删除用户
curl -X DELETE http://localhost:5000/api/users/2 -H "Authorization: Bearer <token>"
```
安全约束：不能删除/禁用/降级**最后一个管理员**；不能删除自己账号；不能修改自己的角色；响应中不含密码哈希等敏感字段。

### 5. 健康检查
```bash
curl http://localhost:5000/api/health
```

### 6. 上传视频文件
```bash
curl -X POST http://localhost:5000/api/upload \
  -H "Authorization: Bearer <token>" \
  -F "video=@path/to/your/video.mp4"
```
响应示例：
```json
{
  "success": true,
  "filename": "a1b2c3d4_video.mp4",
  "original_name": "video.mp4",
  "size_mb": 15.6
}
```

### 7. 基本姿态分析
```bash
curl -X POST http://localhost:5000/api/analyze \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{
    "video_file": "a1b2c3d4_video.mp4",
    "user_name": "张三",
    "coach_name": "教练姓名",
    "keyframes": 5
  }'
```
> `user_name` 可省略，默认使用当前登录账号的姓名。

### 8. 高级分析（包含历史对比）
```bash
curl -X POST http://localhost:5000/api/analyze/advanced \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{
    "video_file": "a1b2c3d4_video.mp4",
    "user_name": "张三",
    "coach_name": "教练姓名",
    "keyframes": 5,
    "history": true
  }'
```

### 9. 下载报告
```bash
curl -O http://localhost:5000/api/report/报告_20250801.docx \
  -H "Authorization: Bearer <token>"
```

### 10. 获取历史数据分析
```bash
curl http://localhost:5000/api/history -H "Authorization: Bearer <token>"
```

### 11. 批量处理视频
```bash
curl -X POST "http://localhost:5000/api/batch?download=true" \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{
    "video_files": ["file1.mp4", "file2.mp4", "file3.mp4"],
    "user_name": "测试组",
    "keyframes": 5
  }'
# 返回zip文件，包含所有分析报告和关键帧图片
```

## 关键指标说明

| 指标 | 说明 | 正常范围 |
|------|------|----------|
| 膝盖弯曲角度 | 支撑腿膝关节角度 | 150°-165° |
| 小腿角度 | 小腿与地面夹角 | 75°-90° |
| 跨步程度 | 落脚点与重心距离 | 轻度/中度/严重 |
| 对称性 | 双膝间距评分 | 80+为良好 |
| 身体前倾 | 身体垂直线与重心夹角 | 5°-15° |

## 项目结构

```
poserun-analyzer/
├── pose_analyzer.py      # 核心姿态分析引擎（含关键帧分析）
├── report_generator.py   # 报告生成器（支持关键帧插入）
├── app.py               # Flask API接口（含高级功能）
├── auth.py              # 认证与人员管理（SQLite用户库，零第三方依赖）
├── requirements.txt     # Python依赖
├── Dockerfile           # Docker配置
├── docker-compose.yml   # Docker Compose配置
├── scripts/             # 部署/验证脚本（NAS Docker API）
│   ├── deploy_v8.py     # 一键部署：重建容器+推代码+重启+健康检查
│   └── verify_auth.py   # 端到端认证/人员管理验证（22项）
├── history.json         # 历史分析数据（运行时生成）
├── data/                # 用户库 auth.db + 签名密钥（运行时创建，需持久化）
├── uploads/             # 上传视频目录（运行时创建）
├── reports/             # 生成报告目录（运行时创建）
└── keyframes/           # 关键帧标注图片目录（运行时创建）
```

## 依赖说明

- **OpenCV**: 视频帧提取和处理
- **MediaPipe**: Google开源姿态识别库，33个身体关键点检测
- **NumPy**: 数值计算和向量运算
- **Flask**: Web框架，提供RESTful API
- **python-docx**: 生成Word格式报告

## 系统要求

- Docker 20.10+ / Docker Compose 1.29+
- 或 Python 3.11+
- 推荐内存：2GB+
- 视频文件大小限制：100MB

## 常见问题

### Q: 分析速度慢怎么办？
A: 系统默认每10帧处理一次以提高效率。可在`pose_analyzer.py`中调整`frame_count % 10`参数。

### Q: 报告中没有图片怎么办？
A: 当前版本为简化实现，后续可添加关键帧截图功能。

### Q: 如何修改评分标准？
A: 修改`report_generator.py`中的`_identify_problems()`和`_generate_suggestions()`方法。

## 版本历史

- v2.1.0 (2026-10-01): 新增用户认证（注册/登录/令牌鉴权）与人员管理（管理员：角色/状态/密码/删除），用户库 SQLite 持久化于 data/
- v2.0.0 (2025-08-01): 更新高级功能（关键帧标注、历史对比、批量处理）
- v1.0.0 (2025-08-01): 初始版本，支持基本姿态分析和报告生成

## 许可证

MIT License

---

**技术支持**: 有问题请提Issue或联系开发团队