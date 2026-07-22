# Nginx Deployment (ASGI)

`httk-web` should be run as an **ASGI app** (for example with `uvicorn`) behind Nginx reverse proxy.

For new deployments, prefer:

- Nginx reverse proxy
- `uvicorn` running the `httk.web` ASGI app
- `systemd` to keep the ASGI worker process running

## Minimal app runner

Create a small app entrypoint (example: `/opt/my-site/app.py`):

```python
from pathlib import Path
from httk.web import create_asgi_app

SRC_DIR = Path("/opt/my-site/src")
app = create_asgi_app(SRC_DIR, config_name="config_dynamic")
```

## Example `systemd` service for uvicorn

```ini
[Unit]
Description=httk-web ASGI service
After=network.target

[Service]
User=www-data
Group=www-data
WorkingDirectory=/opt/my-site
Environment="PYTHONUNBUFFERED=1"
ExecStart=/opt/my-site/.venv/bin/uvicorn app:app --host 127.0.0.1 --port 8001 --proxy-headers --forwarded-allow-ips=127.0.0.1
Restart=always
RestartSec=2

[Install]
WantedBy=multi-user.target
```

## Nginx reverse-proxy config (HTTP -> HTTPS + ASGI upstream)

This mirrors your previous FastAPI setup and is suitable for `httk-web` as well.

```nginx
server {
    listen 80;
    server_name example.org;

    location /.well-known/acme-challenge/ {
        root /var/www/certbot;
    }

    location / {
        return 301 https://$host$request_uri;
    }
}

server {
    listen 443 ssl http2;
    server_name example.org;

    ssl_certificate     /etc/letsencrypt/live/example.org/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/example.org/privkey.pem;

    location / {
        proxy_pass http://127.0.0.1:8001;

        # Optional HTTP basic auth
        # auth_basic "Restricted Area";
        # auth_basic_user_file /etc/nginx/.htpasswd;

        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_http_version 1.1;
        proxy_read_timeout 300;
    }
}
```

## Optional internal/protected location

If you need Apache-like protected internal paths, define them directly in Nginx:

```nginx
location /internal/ {
    alias /opt/my-site/internal/;

    auth_basic "Restricted Internal Content";
    auth_basic_user_file /etc/nginx/.htpasswd;
}
```

## Split hosting (static + dynamic)

If you publish static files to one host and keep dynamic pages on another host, use:

- `httk.web.publish(..., host=..., host_static=...)`

This rewrites generated links so static/dynamic routes point to the intended host.
