# 多阶段构建 - 优化镜像体积

# 构建阶段
FROM python:3.11-slim AS builder

# 设置工作目录
WORKDIR /app

# 复制依赖文件
COPY requirements.txt .

# 安装依赖（不安装文档和测试）
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple && \
    pip uninstall -y opencv-contrib-python opencv-python opencv-python-headless || true && \
    python -c "import shutil, pathlib; [shutil.rmtree(p, ignore_errors=True) for p in pathlib.Path('/install/lib/python3.11/site-packages').glob('cv2*')]" && \
    pip install --no-cache-dir --prefix=/install opencv-python-headless==4.10.0.84 -i https://pypi.tuna.tsinghua.edu.cn/simple

# 运行阶段
FROM python:3.11-slim

# mediapipe 1.x 依赖 libEGL/libGLES（pose 后端初始化需要）
RUN apt-get update && apt-get install -y --no-install-recommends libegl1 libgles2 && \
    rm -rf /var/lib/apt/lists/*

# 设置工作目录
WORKDIR /app

# 从构建阶段复制已安装的包
COPY --from=builder /install /usr/local

# 复制应用代码
COPY . .

# 创建必要的目录（data 用于用户库 auth.db）
RUN mkdir -p /app/uploads /app/reports /app/keyframes /app/data

# 暴露端口
EXPOSE 8080

# 健康检查（app.py 监听 8080）
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/api/health')" || exit 1

# 启动应用
CMD ["python", "app.py"]