#!/bin/sh
set -e

mkdir -p /data

# 首次启动时把默认配置复制到持久化目录，之后用户可直接编辑 /data/config.yaml。
if [ ! -f /data/config.yaml ]; then
  cp /opt/wx_video_download/config.yaml /data/config.yaml
fi

# Cookie 改为共享卷直写：后端写 /cookies/cookies.json，wx-channels 通过软链
# 继续读 /data/cookies.json。首次启动时把旧 /data/cookies.json 迁移到共享卷。
mkdir -p /cookies
if [ ! -e /cookies/cookies.json ] && [ -e /data/cookies.json ]; then
  cp /data/cookies.json /cookies/cookies.json
fi
rm -f /data/cookies.json
ln -s /cookies/cookies.json /data/cookies.json

cd /data
exec /usr/local/bin/wx_video_download server -c /data/config.yaml --workdir /data
