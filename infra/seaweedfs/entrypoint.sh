#!/bin/sh
# Генерирует конфиг S3-доступа SeaweedFS из env и запускает сервер (master + volume + filer + s3).
# Ключи берутся из .env (S3_ACCESS_KEY / S3_SECRET_KEY), в репозитории их нет.
# Анонимного доступа нет: без подписи S3 запросы отклоняются.
set -eu

: "${S3_ACCESS_KEY:?S3_ACCESS_KEY не задан}"
: "${S3_SECRET_KEY:?S3_SECRET_KEY не задан}"

mkdir -p /etc/seaweedfs
cat > /etc/seaweedfs/s3.json <<JSON
{
  "identities": [
    {
      "name": "hr-app",
      "credentials": [{"accessKey": "${S3_ACCESS_KEY}", "secretKey": "${S3_SECRET_KEY}"}],
      "actions": ["Admin", "Read", "Write", "List", "Tagging"]
    }
  ]
}
JSON
chmod 600 /etc/seaweedfs/s3.json

exec weed server -dir=/data -ip.bind=0.0.0.0 \
  -master.volumeSizeLimitMB=1024 -volume.max=0 \
  -s3 -s3.port=8333 -s3.config=/etc/seaweedfs/s3.json
