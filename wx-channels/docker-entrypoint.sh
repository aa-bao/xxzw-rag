#!/bin/sh
set -e

mkdir -p /data

# 首次启动时把默认配置复制到持久化目录，之后用户可直接编辑 /data/config.yaml。
if [ ! -f /data/config.yaml ]; then
  cp /opt/wx_video_download/config.yaml /data/config.yaml
fi

cd /data
exec /usr/local/bin/wx_video_download server -c /data/config.yaml --workdir /data
