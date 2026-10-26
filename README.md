<p align="center">
<img alt="Logo Banner" src="https://raw.githubusercontent.com/operationETH/ABSulli/refs/heads/main/absulli/web/static/img/absulli-banner.svg?sanitize=true"/>
<h3 align="center">A self-hosted companion dashboard for Audiobookshelf</h3>

<p align="center">
  <a href="https://github.com/operationETH/ABSulli/releases"><img alt="Current Release" src="https://img.shields.io/github/v/release/operationETH/ABSulli"></a>
  <a href="https://github.com/operationETH/ABSulli/blob/main/LICENSE"><img alt="License" src="https://img.shields.io/github/license/operationETH/ABSulli"></a>
  <a href="https://hub.docker.com/r/operationeth/absulli"><img alt="Docker Hub Pulls" src="https://img.shields.io/docker/pulls/operationeth/absulli?label=Docker%20Hub%20pulls&logo=docker"></a>
  <a href="https://github.com/operationETH/ABSulli/pkgs/container/absulli"><img alt="GHCR Pulls" src="https://img.shields.io/badge/dynamic/json?url=https://ghcr-badge.elias.eu.org/api/operationETH/ABSulli/absulli&query=downloadCount&label=GHCR%20pulls&logo=github&color=blue"></a>
  <a href="https://ca.unraid.net/apps/absulli-08kx2l31cthp3f"><img alt="Unraid Community Apps" src="https://img.shields.io/badge/Unraid-Community%20Apps-F15A2C?logo=unraid&logoColor=white"></a>
  <a href="https://discord.gg/qvVyfjBuwv"><img alt="Discord" src="https://img.shields.io/badge/Discord-Join%20Server-5865F2?logo=discord&logoColor=white"></a>
</p>

---

> [!NOTE]
> AI assistance was used during development of this project. The code has been reviewed and tested to the best of my ability, but ABSulli is still under active development. Users should review and validate it for their own environment before relying on it. If you find a bug, please open an [issue](https://github.com/operationETH/ABSulli/issues).

ABSulli is an [Audiobookshelf](https://github.com/advplyr/audiobookshelf) companion dashboard inspired by the concept of [Tautulli](https://github.com/Tautulli/Tautulli). Track audiobook and podcast listening activity, browse library stats, view history, manage notifications, and expose Prometheus metrics from one self-hosted web interface.

---

## Features

- First-run setup wizard - no config file needed to get started
- GUI settings management with optional environment variable overrides
- Current listening activity
- Library and user overview
- Recently added media
- Listening history
- Built-in graphs and Prometheus metrics endpoint
- Notification support (Gotify, ntfy, Discord, Slack, Telegram, Pushover, Pushbullet, Email, Webhook)
- Private RSS feed for newly added media
- Local SQLite storage - no external database
- Built-in login with rate limiting and audit logging

---

## Screenshots

![ABSulli Screenshot 1](screenshots/screenshot-absulli-homepage.png)
![ABSulli Screenshot 2](screenshots/screenshot-absulli-homepage-with-activity.png)
![ABSulli Screenshot 3](screenshots/screenshot-absulli-book-page.png)
![ABSulli Screenshot 4](screenshots/screenshot-absulli-author-page.png)
![ABSulli Screenshot 5](screenshots/screenshot-absulli-libraries-page.png)
![ABSulli Screenshot 6](screenshots/screenshot-absulli-libraries-page-2.png)
![ABSulli Screenshot 7](screenshots/screenshot-absulli-graphs.png)

---

## Quick start

### docker-compose.yml

```yaml
services:
  absulli:
    image: ghcr.io/operationeth/absulli:latest
    container_name: absulli
    restart: unless-stopped
    security_opt:
      - no-new-privileges:true
    ports:
      - "8272:8272"
    environment:
      - TZ=America/Phoenix
      - PUID=1000
      - PGID=1000
    volumes:
      - ./data:/config
```

```bash
docker compose up -d
```

Then open **http://\<server-ip\>:8272** and follow the setup wizard.

The wizard will ask for your Audiobookshelf URL and API key, create an admin login, and immediately start importing your data. No `.env` file required.

> [!IMPORTANT]
> Until setup is finished, anyone who can reach port `8272` can complete the wizard and create the admin account. Run setup right after starting the container.

After setup, application settings can be managed from the **Settings** page.

---

## Getting your Audiobookshelf API key

1. Open Audiobookshelf and go to **Settings → API-keys → Add API Key**
2. Name it ABSulli
3. Select an admin user
4. Copy the API token from the token field

ABSulli needs an **Admin** account to read users and listening sessions. To limit what the key can do, create a dedicated Audiobookshelf admin user for ABSulli and set its permissions as follows:

| Permission | Setting | Reason |
|------------|---------|--------|
| `Can Download` | Off | ABSulli only reads data |
| `Can Update` | Off | ABSulli only reads data |
| `Can Delete` | Off | ABSulli only reads data |
| `Can Upload` | Off | ABSulli only reads data |
| `Can Create Ereader` | Off | ABSulli only reads data |
| `Can Access Explicit Content` | On | Explicit items are hidden from ABSulli otherwise |
| `Can Access All Libraries` | On | Unselected libraries are hidden from ABSulli otherwise |
| `Can Access All Tags` | On | Items with restricted tags are hidden from ABSulli otherwise |

These toggles don't remove admin-level access, such as managing users and server settings, so keep the key private.

---

## Optional: pre-fill with environment variables

If you prefer to manage the Audiobookshelf connection via env rather than the setup wizard, create a `.env` file next to your `docker-compose.yml`:

```env
ABS_URL=http://192.168.1.50:13378
ABS_API_KEY=your_abs_api_key_here
```

When these are set, the setup wizard shows them as read-only and uses them automatically.

---

## API access

When authentication is enabled, the API token gives read-only `GET` access to:

- `/api/v1/status`
- `/api/v1/activity`
- `/api/v1/history`
- `/api/v1/users`
- `/api/v1/libraries`

Deprecated `/api/` aliases still work. Send the token as `X-API-Key`, `X-Absulli-Api-Token`, or `Authorization: Bearer`.

The API token can't open the admin UI, change settings, run jobs, or revoke sessions. To revoke all browser sessions, use **Sign out everywhere** in Settings. Prometheus `/metrics` needs its own metrics token.

---

## Prometheus metrics

ABSulli exposes Prometheus metrics at `/metrics`. This can be scraped by Prometheus for Grafana dashboards or external monitoring.

To scrape with authentication enabled, configure a metrics token in **Settings → Network** and send it as a bearer token or with the `X-Absulli-Metrics-Token` header.

---

## RSS feed

ABSulli can provide a private RSS feed containing new books, podcasts, and podcast episodes detected after the feed is enabled. Open **Settings → Notifications**, enable the RSS feed, save the settings, and copy the generated URL into your feed reader.

The feed contains up to 100 of the most recently detected books, podcasts, and podcast episodes. Anyone with the URL can read it without signing in to ABSulli. Disabling the feed blocks access. Regenerating the URL immediately invalidates the previous one.

Set the ABSulli Public URL under **Settings → Network** when the generated feed URL should use a reverse proxy address.

> [!NOTE]
> The RSS feed must be reachable by the RSS reader. Remote readers may require a VPN, reverse proxy, or other external access configuration.

---

## Data and privacy

ABSulli stores all data locally in `/config`:

| File | Contents |
|------|----------|
| `absulli.db` | SQLite database - activity, history, settings |
| `secret_key` | Session signing key - keep private |

ABSulli talks to your Audiobookshelf instance and checks GitHub (`api.github.com`) for new releases. No listening data or credentials are included in update checks. Notification requests are only sent if you configure notification agents.

The `/config` directory and its backups contain listening data and sensitive credentials, including Audiobookshelf and notification tokens. Keep the directory and all backups private. Do not upload `absulli.db` to bug reports or commit it to a repository. ABSulli restricts the directory to its container user and creates sensitive files with owner-only permissions.

---

## Upgrading

Before upgrading, back up your config directory. The most important file is `/config/absulli.db`, which contains your ABSulli settings, activity, and listening history. If you use the example compose file, that is usually `./data/absulli.db` on the host.

To upgrade a Docker install:

```bash
docker compose pull
docker compose up -d
```

ABSulli runs database migrations automatically during startup. Watch the logs after upgrading to confirm the app starts cleanly:

```bash
docker logs absulli -f
```

For local development builds, rebuild and restart instead:

```bash
docker compose build --no-cache
docker compose up -d
```

---

## Database migrations

ABSulli uses Alembic for database schema migrations. Migrations run automatically on startup.

Existing installs created before Alembic are safely baselined on first start: ABSulli keeps the old additive column check for that one upgrade path, stamps the database at the current migration, and future schema changes are handled through Alembic revisions.

---

## Reverse proxy

ABSulli runs on port `8272` by default. Proxy to `http://absulli:8272`, and if serving over HTTPS, enable **HTTPS Secure Cookies** in Settings → Network or set `ABSULLI_COOKIE_SECURE=true`.

ABSulli sets its own security headers (`Content-Security-Policy`, `X-Frame-Options`, `Referrer-Policy`, etc.) on every response. You do not need to add these in your proxy config.

To rate-limit logins by client IP behind a reverse proxy:

1. Enable proxy trust in Settings → Network or set `ABSULLI_TRUST_PROXY=true`.
2. Add your reverse proxy's IP under Trusted Proxy IP Addresses or `ABSULLI_TRUSTED_PROXY_IPS`. Use a static IP and avoid trusting entire Docker subnets.
3. Restrict direct access to port `8272` with firewall rules or Docker networking.

ABSulli uses the last `X-Forwarded-For` address for login rate limiting. Your reverse proxy must append the client IP to this header.

---

## Security

ABSulli ships with the following enabled by default:

- PBKDF2-SHA256 (600,000 iterations) for password storage
- Signed session cookies with server-side revocation
- Per-request CSP nonces - no `unsafe-inline`
- Login rate limiting with lockout, persisted across restarts
- Login audit log (IP, user agent, outcome, reason)
- CSRF protection on all form submissions
- Separate API and metrics tokens with independent scopes
- Runs as a non-root user with read-only application files

Authentication is enabled by default after setup. Keep it enabled for all installs.

---