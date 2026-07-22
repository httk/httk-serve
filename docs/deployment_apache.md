# Apache Deployment (ASGI)

`httk-web` should be run as an **ASGI app** (for example with `uvicorn`) behind Apache reverse proxy.

For new deployments, prefer:

- Apache (`mod_proxy`, `mod_proxy_http`, optional `mod_proxy_wstunnel`)
- `uvicorn` running the `httk.web` ASGI app
- `systemd` to keep the ASGI worker process running

WSGI (`mod_wsgi`) is from the legacy path and is not the recommended runtime for `httk-web`.

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

## Apache vhost proxying to uvicorn

```apache
<VirtualHost *:443>
  ServerName example.org

  SSLEngine on
  SSLCertificateFile    /etc/letsencrypt/live/example.org/fullchain.pem
  SSLCertificateKeyFile /etc/letsencrypt/live/example.org/privkey.pem

  ProxyRequests Off
  ProxyPreserveHost On
  RequestHeader set X-Forwarded-Proto "https"

  # Forward everything to ASGI app
  ProxyPass        / http://127.0.0.1:8001/
  ProxyPassReverse / http://127.0.0.1:8001/

  ErrorLog  /var/log/apache2/httk-web-error.log
  CustomLog /var/log/apache2/httk-web-access.log combined
</VirtualHost>
```

## Keeping protected/internal Apache paths

If you already use Apache auth-protected paths (for example `/internal`), keep those rules in Apache and only proxy the application routes:

```apache
Alias /internal /opt/my-site/internal

<Directory "/opt/my-site/internal">
  AllowOverride None
  Require user internal
  AuthType Basic
  AuthName "Restricted Internal Content"
  AuthBasicProvider file
  AuthUserFile /opt/my-site/htpasswd
</Directory>

ProxyPass        / http://127.0.0.1:8001/
ProxyPassReverse / http://127.0.0.1:8001/
```

## Split hosting (static + dynamic)

If you publish static files to one host and keep dynamic pages on another host, use:

- `httk.web.publish(..., host=..., host_static=...)`

This rewrites generated links so static/dynamic routes point to the intended host.
