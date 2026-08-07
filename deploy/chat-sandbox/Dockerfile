FROM python:3.12-slim

ARG APT_MIRROR=mirrors.aliyun.com
ARG PIP_INDEX=https://mirrors.aliyun.com/pypi/simple/

ENV DEBIAN_FRONTEND=noninteractive

RUN sed -i "s@deb.debian.org@${APT_MIRROR}@g" /etc/apt/sources.list.d/debian.sources

RUN apt-get update && apt-get install -y --no-install-recommends \
    libreoffice-writer \
    libreoffice-calc \
    libreoffice-impress \
    nodejs \
    npm \
    poppler-utils \
    pandoc \
    fontconfig \
    fonts-noto-cjk \
    fonts-noto-cjk-extra \
    fonts-liberation \
    && fc-cache -f \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir -i ${PIP_INDEX} \
    pandas==2.2.3 \
    openpyxl==3.1.5 \
    python-docx==1.1.2 \
    pypdf==5.1.0 \
    pdfplumber==0.11.4 \
    reportlab==4.2.5 \
    markitdown==0.0.1a3 \
    matplotlib==3.9.2

# Skill 常用全局 npm 包（构建期联网；沙箱执行期无网，禁止再 npm install）
ARG NPM_REGISTRY=https://registry.npmmirror.com
RUN npm config set registry "${NPM_REGISTRY}" \
    && npm install -g pptxgenjs docx \
    && npm cache clean --force

# npm -g 不会自动进 require 搜索路径
ENV NODE_PATH=/usr/local/lib/node_modules

WORKDIR /workspace
