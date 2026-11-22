# Redis with the ACL template and its entrypoint baked in, for Coolify, which copies no files.
FROM redis:7.4.12-alpine
COPY entrypoint.sh users.acl.tmpl /etc/redis/
