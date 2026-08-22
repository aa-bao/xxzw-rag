# 微信视频号下载器（wx_channels_download）Linux 版容器镜像。
# 使用官方 Linux release 二进制（静态链接，x86 32 位），Alpine 可直接运行。
FROM alpine:3.20

RUN apk add --no-cache ca-certificates tzdata

WORKDIR /opt/wx_video_download

COPY wx_video_download /usr/local/bin/wx_video_download
COPY config.docker.yaml /opt/wx_video_download/config.yaml
COPY docker-entrypoint.sh /usr/local/bin/wx-entrypoint

RUN chmod +x /usr/local/bin/wx_video_download /usr/local/bin/wx-entrypoint

ENV WX_VIDEO_HOME=/data
ENV WX_CHANNELS_DOWNLOAD_CONFIG_FILEPATH=/data/config.yaml

VOLUME /data
EXPOSE 2022 2023

ENTRYPOINT ["/usr/local/bin/wx-entrypoint"]
