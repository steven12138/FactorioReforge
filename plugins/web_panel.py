"""A read-only status page, served over plain HTTP.

Everything on it already exists as structured data -- the Lua layer returns JSON
and the other plugins expose helpers -- so this is a rendering layer, nothing
more. It uses ``http.server`` on a worker thread rather than pulling in a web
framework for one page.

**Read-only, deliberately.** There are no controls: no stop button, no rollback,
no console. A page with no authentication and no write path cannot be abused
into doing damage, and it binds to localhost by default. Anyone who wants
control from outside the machine already has Telegram, which authenticates.
"""

from __future__ import annotations

import asyncio
import html
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from factorio_reforge.command.builder import Literal
from factorio_reforge.core.errors import QueryError
from factorio_reforge.permission import PermissionLevel

PLUGIN_METADATA = {
    "id": "web_panel",
    "version": "1.0.0",
    "name": "Web Panel",
    "description": "Read-only status page over HTTP",
    "author": "FactorioReforge",
    "dependencies": {"factorio_reforge": ">=0.1.0"},
}

DEFAULT_CONFIG = {
    "enabled": True,
    #: Localhost by default. Binding to 0.0.0.0 publishes player names and world
    #: state to anyone who can reach the port -- put it behind a reverse proxy
    #: with auth if you want that.
    "host": "127.0.0.1",
    "port": 8080,
    "title": "FactorioReforge",
    "refresh_seconds": 15,
    "log_lines": 40,
}

_state: dict = {}


def on_load(server, prev):
    config = server.load_config_simple("config.json", DEFAULT_CONFIG)
    _state.clear()
    _state.update(
        config=config, server=server, httpd=None, thread=None,
        snapshot={}, log=[], task=None,
    )

    server.register_command(
        Literal("!!web").requires(PermissionLevel.ADMIN).runs(_cmd_status)
    )
    server.register_help_message("!!web", "web panel address", PermissionLevel.ADMIN)

    if not config.get("enabled", True):
        server.logger.info("web_panel is disabled in its config")
        return

    _state["task"] = asyncio.create_task(_refresher(server, config))
    _start_http(server, config)


async def on_unload(server):
    task = _state.get("task")
    if task is not None:
        task.cancel()
    httpd = _state.get("httpd")
    if httpd is not None:
        httpd.shutdown()
        httpd.server_close()
    thread = _state.get("thread")
    if thread is not None:
        thread.join(timeout=5)
    _state.clear()


async def on_info(server, info):
    """Keep a short tail of server output for the page."""
    if not info.is_from_server:
        return
    log = _state.get("log")
    if log is None:
        return
    log.append(info.content)
    limit = (_state.get("config") or {}).get("log_lines", 40)
    if len(log) > limit:
        del log[: len(log) - limit]


# -- data collection --------------------------------------------------------

async def _refresher(server, config):
    """Poll on a timer so HTTP requests never wait on RCON.

    The handler thread cannot touch the event loop, and a page load should not
    be able to stall behind a slow query, so the snapshot is always pre-computed.
    """
    interval = max(5, int(config.get("refresh_seconds", 15)))
    while True:
        try:
            _state["snapshot"] = await _collect(server)
        except Exception:
            server.logger.debug("web_panel refresh failed", exc_info=True)
        await asyncio.sleep(interval)


async def _collect(server) -> dict:
    data: dict[str, Any] = {
        "generated_at": time.time(),
        "server_running": server.is_server_running(),
        "server_startup": server.is_server_startup(),
        "rcon": server.is_rcon_running(),
        "uptime": server.get_server_uptime(),
        "plugins": server.get_plugin_list(),
        "snapshots": [
            {"id": s.id, "at": s.created_at_text, "comment": s.comment, "by": s.created_by}
            for s in server.saves.list()[:10]
        ],
        "players": [],
        "stats": {},
        "production": {},
        "blueprints": {},
        "error": None,
    }

    if not server.is_server_startup():
        return data

    try:
        data["players"] = await server.get_online_player_details()
        data["stats"] = await server.get_server_stats()
    except QueryError as exc:
        data["error"] = str(exc)

    # Other plugins are optional -- the page degrades rather than breaking.
    production = server.get_plugin_instance("production")
    if production is not None:
        data["production"] = production.get_history()
        data["_svg"] = production.svg_chart
    blueprints = server.get_plugin_instance("blueprints")
    if blueprints is not None:
        data["blueprints"] = blueprints.get_library()

    return data


# -- http -------------------------------------------------------------------

def _start_http(server, config):
    host, port = config.get("host", "127.0.0.1"), int(config.get("port", 8080))

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 -- the base class dictates the name
            if self.path.startswith("/api"):
                self._send(200, "application/json", _render_json())
            elif self.path in ("/", "/index.html"):
                self._send(200, "text/html; charset=utf-8", _render_html())
            else:
                self._send(404, "text/plain", b"not found")

        def _send(self, code, content_type, body: bytes):
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            """Silence per-request logging; it would drown the server console."""

    try:
        httpd = ThreadingHTTPServer((host, port), Handler)
    except OSError as exc:
        server.logger.error("web_panel could not bind %s:%s: %s", host, port, exc)
        return

    thread = threading.Thread(target=httpd.serve_forever, name="web_panel", daemon=True)
    thread.start()
    _state["httpd"], _state["thread"] = httpd, thread
    server.logger.info("web_panel is serving on http://%s:%s", host, port)


def _render_json() -> bytes:
    data = {k: v for k, v in (_state.get("snapshot") or {}).items() if not k.startswith("_")}
    data["log"] = list(_state.get("log") or [])
    return json.dumps(data, indent=2, default=str).encode("utf-8")


def _render_html() -> bytes:
    data = _state.get("snapshot") or {}
    config = _state.get("config") or {}
    stats = data.get("stats") or {}
    esc = html.escape

    def card(title: str, body: str) -> str:
        return f'<section><h2>{esc(title)}</h2>{body}</section>'

    status = "running" if data.get("server_running") else "stopped"
    if data.get("server_running") and not data.get("server_startup"):
        status = "starting"

    overview = "<dl>" + "".join(
        f"<dt>{esc(k)}</dt><dd>{esc(str(v))}</dd>" for k, v in [
            ("Status", status),
            ("RCON", "connected" if data.get("rcon") else "not connected"),
            ("Uptime", _duration(data.get("uptime"))),
            ("Surface", stats.get("surface", "-")),
            ("Played", _duration((stats.get("ticks_played", 0) or 0) / 60)),
            ("Evolution", f"{(stats.get('evolution') or 0) * 100:.2f}%"),
            ("Pollution", f"{stats.get('pollution', 0):,.0f}"),
            ("Research", stats.get("research") or "idle"),
        ]
    ) + "</dl>"

    players = data.get("players") or []
    players_html = (
        "<ul>" + "".join(
            f"<li>{esc(p['name'])}"
            f"{' <em>admin</em>' if p.get('admin') else ''}"
            f" — {int(p.get('online_time', 0)) // 3600}m played</li>"
            for p in players
        ) + "</ul>"
        if players else "<p class=muted>Nobody online.</p>"
    )

    snapshots = data.get("snapshots") or []
    snapshots_html = (
        "<ul>" + "".join(
            f"<li>#{s['id']} {esc(s['at'])} — {esc(s['comment'] or '(no comment)')}"
            f" <span class=muted>by {esc(s['by'])}</span></li>"
            for s in snapshots
        ) + "</ul>"
        if snapshots else "<p class=muted>No snapshots.</p>"
    )

    charts = ""
    svg = data.get("_svg")
    for item, series in (data.get("production") or {}).items():
        if series and svg is not None:
            charts += f"<figure>{svg(item, series)}</figure>"
    charts = charts or '<p class=muted>No production samples yet.</p>'

    blueprints = data.get("blueprints") or {}
    bp_html = (
        "<ul>" + "".join(
            f"<li>{esc(name)} — {entry.get('entities', 0)} entities "
            f"<span class=muted>by {esc(str(entry.get('saved_by', '?')))}</span></li>"
            for name, entry in sorted(blueprints.items())
        ) + "</ul>"
        if blueprints else '<p class=muted>Library is empty.</p>'
    )

    log_html = "<pre>" + esc("\n".join(_state.get("log") or [])) + "</pre>"

    error = (
        f'<p class="error">Some data is unavailable: {esc(data["error"])}</p>'
        if data.get("error") else ""
    )
    age = int(time.time() - (data.get("generated_at") or time.time()))

    return f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="refresh" content="{int(config.get('refresh_seconds', 15))}">
<title>{esc(config.get('title', 'FactorioReforge'))}</title>
<style>
  :root {{ color-scheme: light dark; --fg: #1a1a1a; --bg: #fafafa; --muted: #6b6b6b;
           --card: #fff; --line: #e0e0e0; --accent: #d4761a; }}
  @media (prefers-color-scheme: dark) {{
    :root {{ --fg: #e8e8e8; --bg: #16181c; --muted: #9aa0a6; --card: #1e2126; --line: #2c3038; }}
  }}
  * {{ box-sizing: border-box; }}
  body {{ margin: 0; padding: 1.5rem; background: var(--bg); color: var(--fg);
          font: 15px/1.55 ui-sans-serif, system-ui, -apple-system, sans-serif; }}
  header {{ display: flex; align-items: baseline; gap: .75rem; flex-wrap: wrap;
            margin-bottom: 1.25rem; }}
  h1 {{ font-size: 1.35rem; margin: 0; }}
  .muted {{ color: var(--muted); }}
  .grid {{ display: grid; gap: 1rem;
           grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); }}
  section {{ background: var(--card); border: 1px solid var(--line);
             border-radius: 10px; padding: 1rem 1.1rem; min-width: 0; }}
  h2 {{ font-size: .8rem; text-transform: uppercase; letter-spacing: .06em;
        color: var(--muted); margin: 0 0 .6rem; }}
  dl {{ display: grid; grid-template-columns: auto 1fr; gap: .3rem .9rem; margin: 0; }}
  dt {{ color: var(--muted); }}
  dd {{ margin: 0; text-align: right; font-variant-numeric: tabular-nums; }}
  ul {{ margin: 0; padding-left: 1.1rem; }}
  li {{ margin: .2rem 0; }}
  pre {{ margin: 0; overflow-x: auto; font-size: 12px; line-height: 1.4;
         color: var(--muted); max-height: 22rem; }}
  figure {{ margin: 0 0 .75rem; color: var(--accent); }}
  .error {{ color: #c0392b; }}
  .status {{ padding: .1rem .5rem; border-radius: 999px; font-size: .8rem;
             background: var(--accent); color: #fff; }}
  .wide {{ grid-column: 1 / -1; }}
</style>
</head><body>
<header>
  <h1>{esc(config.get('title', 'FactorioReforge'))}</h1>
  <span class="status">{esc(status)}</span>
  <span class="muted">updated {age}s ago · read-only</span>
</header>
{error}
<div class="grid">
  {card("Overview", overview)}
  {card(f"Online ({len(players)})", players_html)}
  {card("Recent snapshots", snapshots_html)}
  {card("Blueprints", bp_html)}
  <div class="wide">{card("Production", charts)}</div>
  <div class="wide">{card("Recent output", log_html)}</div>
</div>
</body></html>""".encode()


def _duration(seconds: float | None) -> str:
    if not seconds:
        return "-"
    seconds = int(seconds)
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    if hours >= 24:
        days, hours = divmod(hours, 24)
        return f"{days}d{hours:02d}h"
    if hours:
        return f"{hours}h{minutes:02d}m"
    return f"{minutes}m{secs:02d}s"


async def _cmd_status(source):
    config = _state["config"]
    if not config.get("enabled", True):
        await source.reply("web_panel is disabled in its config.")
        return
    if _state.get("httpd") is None:
        await source.reply("web_panel is not serving -- check the log for a bind error.")
        return
    await source.reply(
        f"Serving on http://{config.get('host')}:{config.get('port')} "
        f"(JSON at /api). Read-only."
    )
