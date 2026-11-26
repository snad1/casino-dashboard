#!/bin/sh
# Render the ACL file from Docker secrets (password hashes only), then start Redis.
# WolfBet's users come from the template; every other service in DASHBOARD_SERVICES gets
# its own publisher, allowed to append to its own streams only, and the dashboard may read them.
set -eu

hash_file() {
    tr -d '\n' < "$1" | sha256sum | cut -d' ' -f1
}

hash_secret() {
    file="/run/secrets/$1"
    if [ ! -s "$file" ]; then
        echo "redis: secret $1 is missing or empty; refusing to start" >&2
        exit 1
    fi
    hash_file "$file"
}

publisher=$(hash_secret redis_publisher_password)
dashboard=$(hash_secret redis_dashboard_password)

umask 077
sed -e "s/PUBLISHER_HASH/$publisher/" -e "s/DASHBOARD_HASH/$dashboard/" \
    /etc/redis/users.acl.tmpl > /tmp/users.acl

for service in $(echo "${DASHBOARD_SERVICES:-wolfbet}" | tr ',' ' '); do
    [ "$service" = wolfbet ] && continue
    case "$service" in
        *[!a-z0-9-]*)
            echo "redis: service names are a-z, 0-9 and -; refusing to start" >&2
            exit 1 ;;
    esac
    file="/run/publishers/$service.txt"
    if [ ! -s "$file" ]; then
        echo "redis: publisher password for $service is missing or empty; refusing to start" >&2
        exit 1
    fi
    echo "user $service-pub on #$(hash_file "$file") resetkeys ~$service:* resetchannels -@all +xadd +ping" \
        >> /tmp/users.acl
    # The dashboard reads the service's streams; its auth selector in ( ) stays as it is.
    sed -i "/^user dashboard /s/ resetchannels / ~$service:* resetchannels /" /tmp/users.acl
done

exec redis-server \
    --bind 0.0.0.0 \
    --protected-mode yes \
    --aclfile /tmp/users.acl \
    --maxmemory 256mb \
    --maxmemory-policy allkeys-lru \
    --save "" \
    --appendonly no \
    --dir /tmp
