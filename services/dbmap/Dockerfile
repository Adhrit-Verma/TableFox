# Hosted multi-user TableFox. Configure with environment variables (see MULTI_TENANT_PLAN.md).
FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir . && useradd --create-home tablefox && mkdir /data && chown tablefox /data
USER tablefox
ENV DBMAP_MULTI_TENANT=true \
    DBMAP_MCP_BIND=0.0.0.0 \
    DBMAP_CACHE_DIR=/data/cache \
    DBMAP_AUDIT_DIR=/data/audit \
    DBMAP_TENANT_DB=/data/tenants.sqlite
VOLUME /data
# Listens on 8765 unless DBMAP_MCP_PORT or a host-provided PORT says otherwise.
EXPOSE 8765
CMD ["dbmap-mcp"]
