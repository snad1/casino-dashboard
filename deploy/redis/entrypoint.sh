#!/bin/sh
# Render the ACL file from Docker secrets (password hashes only), then start Redis.
set -eu

hash_secret() {
    file="/run/secrets/$1"
    if [ ! -s "$file" ]; then
        echo "redis: secret $1 is missing or empty; refusing to start" >&2
        exit 1
    fi
    tr -d '\n' < "$file" | sha256sum | cut -d' ' -f1
}

publisher=$(hash_secret redis_publisher_password)
dashboard=$(hash_secret redis_dashboard_password)

umask 077
sed -e "s/PUBLISHER_HASH/$publisher/" -e "s/DASHBOARD_HASH/$dashboard/" \
    /etc/redis/users.acl.tmpl > /tmp/users.acl

exec redis-server \
    --bind 0.0.0.0 \
    --protected-mode yes \
    --aclfile /tmp/users.acl \
    --maxmemory 256mb \
    --maxmemory-policy allkeys-lru \
    --save "" \
    --appendonly no \
    --dir /tmp
