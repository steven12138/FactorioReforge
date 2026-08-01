"""Discover, load, reload and unload plugins; dispatch events to them.

Two plugin shapes are supported:

* solo -- ``plugins/save_guard.py``
* multi -- ``plugins/telegram_bridge/`` containing ``__init__.py``

Both declare a ``PLUGIN_METADATA`` dict at module level. Packed ``.mcdr``
archives and the plugin marketplace are deliberately out of scope.

Reload works by dropping the module from ``sys.modules`` and importing it fresh
under the same synthetic package name, so an edited file really does take
effect. Anything the old version registered goes away with its registry.
"""

from __future__ import annotations

import asyncio
import importlib.machinery
import importlib.util
import inspect
import logging
import sys
import traceback
from collections.abc import Iterable
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING, Any

from factorio_reforge.plugin import events as ev
from factorio_reforge.plugin.metadata import Metadata, MetadataError, satisfies
from factorio_reforge.plugin.registry import GlobalRegistry, PluginRegistry

if TYPE_CHECKING:
    from factorio_reforge.core.server import ReforgeServer
    from factorio_reforge.plugin.interface import PluginServerInterface

CORE_ID = "factorio_reforge"
CORE_VERSION = "0.1.0"
DISABLED_SUFFIX = ".disabled"
MODULE_PREFIX = "reforge_plugin_"


class PluginError(Exception):
    pass


class _NoBytecodeCacheLoader(importlib.machinery.SourceFileLoader):
    """Always compile from source, never from ``__pycache__``.

    Python decides a cached ``.pyc`` is current by comparing the source's mtime
    and size. Editing a plugin within the same second without changing its
    length -- bumping a version string, flipping a constant -- satisfies both,
    so a reload would quietly re-run the old bytecode. Refusing to stat the
    source makes the loader skip the cache entirely, which costs a few
    milliseconds per reload and removes the whole class of "my edit did nothing".
    """

    def path_stats(self, path):
        raise OSError(f"bytecode cache disabled for plugin {path}")

    def _cache_bytecode(self, source_path, bytecode_path, data):
        return None


class LoadedPlugin:
    def __init__(self, metadata: Metadata, module: ModuleType, path: Path):
        self.metadata = metadata
        self.module = module
        self.path = path
        self.registry = PluginRegistry(metadata.id)
        self.interface: PluginServerInterface | None = None
        self.mtime: float = _mtime(path)

    @property
    def id(self) -> str:
        return self.metadata.id

    @property
    def module_name(self) -> str:
        return MODULE_PREFIX + self.id

    def file_changed(self) -> bool:
        return _mtime(self.path) != self.mtime

    def __repr__(self) -> str:
        return f"<Plugin {self.metadata}>"


class PluginManager:
    def __init__(
        self,
        server: ReforgeServer,
        directories: Iterable[Path],
        logger: logging.Logger | None = None,
    ):
        #: The core, not a ServerInterface -- PluginServerInterface wraps this.
        self.server = server
        self.directories = [Path(d) for d in directories]
        self.logger = logger or logging.getLogger(__name__)
        self.plugins: dict[str, LoadedPlugin] = {}
        self.registry = GlobalRegistry(self.logger)
        self._lock = asyncio.Lock()

    # -- discovery -----------------------------------------------------------

    def discover(self) -> list[Path]:
        """Candidate plugin paths. A trailing ``.disabled`` opts a file out."""
        found: list[Path] = []
        for directory in self.directories:
            if not directory.is_dir():
                self.logger.debug("Plugin directory %s does not exist, skipping", directory)
                continue
            for entry in sorted(directory.iterdir()):
                if entry.name.startswith(("_", ".")) or entry.name.endswith(DISABLED_SUFFIX):
                    continue
                if entry.is_file() and entry.suffix == ".py" or entry.is_dir() and (entry / "__init__.py").is_file():
                    found.append(entry)
        return found

    # -- loading -------------------------------------------------------------

    async def load_all(self) -> tuple[list[str], list[str]]:
        """Import every discovered plugin, then load them in dependency order."""
        async with self._lock:
            candidates: list[tuple[Path, Metadata, ModuleType]] = []
            failed: list[str] = []
            for path in self.discover():
                try:
                    metadata, module = self._import(path)
                except Exception as exc:
                    self.logger.error("Failed to import plugin %s: %s", path.name, exc)
                    self.logger.debug("%s", traceback.format_exc())
                    failed.append(path.name)
                    continue
                candidates.append((path, metadata, module))

            ordered, unresolved = _resolve_order(
                {m.id: m for _, m, _ in candidates}, self.logger
            )
            failed.extend(unresolved)

            by_id = {m.id: (p, m, mod) for p, m, mod in candidates}
            loaded: list[str] = []
            for plugin_id in ordered:
                path, metadata, module = by_id[plugin_id]
                if await self._activate(LoadedPlugin(metadata, module, path)):
                    loaded.append(plugin_id)
                else:
                    failed.append(plugin_id)

            self._rebuild()
            return loaded, failed

    def _import(self, path: Path) -> tuple[Metadata, ModuleType]:
        entry = path / "__init__.py" if path.is_dir() else path
        fallback_id = path.stem if path.is_file() else path.name

        # Import under a private name so a plugin called e.g. "logging" cannot
        # shadow a stdlib module for the rest of the process.
        module_name = MODULE_PREFIX + fallback_id
        sys.modules.pop(module_name, None)
        for name in [n for n in sys.modules if n.startswith(module_name + ".")]:
            sys.modules.pop(name, None)

        spec = importlib.util.spec_from_file_location(
            module_name,
            entry,
            loader=_NoBytecodeCacheLoader(module_name, str(entry)),
            submodule_search_locations=[str(path)] if path.is_dir() else None,
        )
        if spec is None or spec.loader is None:
            raise PluginError(f"cannot build an import spec for {entry}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        try:
            spec.loader.exec_module(module)
        except Exception:
            sys.modules.pop(module_name, None)
            raise

        raw = getattr(module, "PLUGIN_METADATA", None)
        if raw is None:
            raise MetadataError(f"{entry.name} has no PLUGIN_METADATA")
        metadata = Metadata.from_dict(raw, fallback_id=fallback_id)
        if metadata.id in self.plugins:
            raise PluginError(f"duplicate plugin id {metadata.id!r}")
        return metadata, module

    async def _activate(self, plugin: LoadedPlugin) -> bool:
        """Wire up listeners and commands, then fire ``on_load``."""
        from factorio_reforge.plugin.interface import PluginServerInterface

        plugin.interface = PluginServerInterface(self.server, plugin)
        self.plugins[plugin.id] = plugin
        try:
            self._collect_listeners(plugin)
            await self._call_load_hook(plugin)
        except Exception:
            self.logger.exception("Plugin %s failed during load", plugin.id)
            self.plugins.pop(plugin.id, None)
            sys.modules.pop(plugin.module_name, None)
            return False
        self.logger.info("Loaded plugin %s", plugin.metadata)
        return True

    def _collect_listeners(self, plugin: LoadedPlugin) -> None:
        """Pick up both name-based hooks and @event_listener decorations."""
        for obj in vars(plugin.module).values():
            if not callable(obj):
                continue
            for event_id, priority in getattr(obj, "_reforge_listeners", []):
                plugin.registry.add_listener(event_id, obj, priority)
        for event in ev.ALL_EVENTS:
            if event.default_listener is None:
                continue
            callback = getattr(plugin.module, event.default_listener, None)
            if callable(callback) and not getattr(callback, "_reforge_listeners", None):
                plugin.registry.add_listener(event, callback)

    async def _call_load_hook(self, plugin: LoadedPlugin) -> None:
        hook = getattr(plugin.module, "on_load", None)
        if not callable(hook):
            return
        await _invoke(hook, plugin.interface, None)

    # -- unload / reload -----------------------------------------------------

    async def unload(self, plugin_id: str) -> bool:
        async with self._lock:
            return await self._unload_locked(plugin_id)

    async def _unload_locked(self, plugin_id: str) -> bool:
        plugin = self.plugins.get(plugin_id)
        if plugin is None:
            return False
        dependents = [p.id for p in self.plugins.values()
                      if plugin_id in p.metadata.dependencies and p.id != plugin_id]
        if dependents:
            raise PluginError(
                f"{plugin_id} is required by {', '.join(dependents)}; unload those first"
            )

        hook = getattr(plugin.module, "on_unload", None)
        if callable(hook):
            try:
                await _invoke(hook, plugin.interface)
            except Exception:
                self.logger.exception("Plugin %s raised in on_unload", plugin_id)

        plugin.registry.clear()
        self.plugins.pop(plugin_id, None)
        sys.modules.pop(plugin.module_name, None)
        for name in [n for n in sys.modules if n.startswith(plugin.module_name + ".")]:
            sys.modules.pop(name, None)
        self._rebuild()
        self.logger.info("Unloaded plugin %s", plugin_id)
        return True

    async def reload(self, plugin_id: str) -> bool:
        async with self._lock:
            plugin = self.plugins.get(plugin_id)
            if plugin is None:
                return False
            path = plugin.path
            await self._unload_locked(plugin_id)
            try:
                metadata, module = self._import(path)
            except Exception as exc:
                self.logger.error("Reload of %s failed: %s", plugin_id, exc)
                return False
            ok = await self._activate(LoadedPlugin(metadata, module, path))
            self._rebuild()
            return ok

    async def reload_changed(self) -> list[str]:
        """Reload plugins whose file changed on disk. Powers ``!!FR reload``."""
        changed = [p.id for p in self.plugins.values() if p.file_changed()]
        for plugin_id in changed:
            await self.reload(plugin_id)
        return changed

    async def unload_all(self) -> None:
        for plugin_id in list(self.plugins):
            try:
                await self.unload(plugin_id)
            except PluginError:
                # Dependency ordering: retry after the dependents are gone.
                continue
        for plugin_id in list(self.plugins):
            await self.unload(plugin_id)

    def _rebuild(self) -> None:
        self.registry.rebuild([p.registry for p in self.plugins.values()])

    # -- dispatch ------------------------------------------------------------

    async def dispatch(self, event: ev.Event | str, *args: Any) -> None:
        """Fire an event at every listener, in priority order.

        A listener that raises is logged and skipped; one broken plugin must not
        stop the others from seeing the event.
        """
        for listener in self.registry.listeners_for(event):
            plugin = self.plugins.get(listener.plugin_id)
            interface = plugin.interface if plugin else self.server.interface
            try:
                await _invoke(listener.callback, interface, *args)
            except Exception:
                self.logger.exception(
                    "Plugin %s raised while handling %s", listener.plugin_id, event
                )

    def get(self, plugin_id: str) -> LoadedPlugin | None:
        return self.plugins.get(plugin_id)

    def list_ids(self) -> list[str]:
        return sorted(self.plugins)


async def _invoke(callback, *args) -> Any:
    """Call a plugin hook with as many of ``args`` as its signature accepts."""
    try:
        params = inspect.signature(callback).parameters
    except (TypeError, ValueError):
        params = {}
    if any(p.kind is inspect.Parameter.VAR_POSITIONAL for p in params.values()):
        accepted = args
    else:
        positional = [
            p for p in params.values()
            if p.kind in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
        ]
        accepted = args[: len(positional)]
    result = callback(*accepted)
    if inspect.isawaitable(result):
        return await result
    return result


def _resolve_order(
    metadata_by_id: dict[str, Metadata], logger: logging.Logger
) -> tuple[list[str], list[str]]:
    """Topologically sort by dependency; report what cannot be satisfied.

    Returns ``(ordered_ids, rejected_ids)``. A plugin whose dependency is
    missing, at the wrong version, or part of a cycle is rejected rather than
    loaded into a half-working state.
    """
    rejected: list[str] = []
    available = dict(metadata_by_id)

    changed = True
    while changed:
        changed = False
        for plugin_id, metadata in list(available.items()):
            for dep_id, requirement in metadata.dependencies.items():
                if dep_id == CORE_ID:
                    if not satisfies(CORE_VERSION, requirement):
                        logger.error(
                            "Plugin %s needs %s %s but core is %s",
                            plugin_id, CORE_ID, requirement, CORE_VERSION,
                        )
                        break
                    continue
                dep = available.get(dep_id)
                if dep is None:
                    logger.error(
                        "Plugin %s depends on %s, which is missing or failed to load",
                        plugin_id, dep_id,
                    )
                    break
                if not satisfies(dep.version, requirement):
                    logger.error(
                        "Plugin %s needs %s %s but found %s",
                        plugin_id, dep_id, requirement, dep.version,
                    )
                    break
            else:
                continue
            del available[plugin_id]
            rejected.append(plugin_id)
            changed = True

    ordered: list[str] = []
    visiting: set[str] = set()
    done: set[str] = set()

    def visit(plugin_id: str) -> bool:
        if plugin_id in done:
            return True
        if plugin_id in visiting:
            logger.error("Dependency cycle involving plugin %s", plugin_id)
            return False
        visiting.add(plugin_id)
        for dep_id in available[plugin_id].dependencies:
            if dep_id != CORE_ID and dep_id in available and not visit(dep_id):
                visiting.discard(plugin_id)
                return False
        visiting.discard(plugin_id)
        done.add(plugin_id)
        ordered.append(plugin_id)
        return True

    for plugin_id in sorted(available):
        if not visit(plugin_id):
            rejected.append(plugin_id)

    return [p for p in ordered if p not in rejected], rejected


def _mtime(path: Path) -> float:
    target = path / "__init__.py" if path.is_dir() else path
    try:
        if path.is_dir():
            # Any file in the package changing counts as the plugin changing.
            return max(p.stat().st_mtime for p in path.rglob("*.py"))
        return target.stat().st_mtime
    except (OSError, ValueError):
        return 0.0
