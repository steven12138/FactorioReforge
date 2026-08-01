"""Translation for everything a person reads.

One translator serves the core and every plugin. Keys are dotted paths into
YAML files; a plugin's keys are namespaced under its id automatically, so two
plugins can both have a ``failed`` message without colliding.

Three deliberate choices:

* **A missing key renders as the key**, not as blank text. A visible
  ``save.restore.confirm`` in chat tells you exactly what to add; an empty line
  tells you nothing.
* **English is always the fallback.** A half-translated language stays usable
  rather than turning into holes.
* **Formatting failures fall back to the raw template.** A translator who drops
  a ``{slot}`` placeholder should produce a slightly wrong message, not an
  exception inside a command handler.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

DEFAULT_LANGUAGE = "en"
LANG_DIR_NAME = "lang"

logger = logging.getLogger("reforge.i18n")


def flatten(data: Any, prefix: str = "") -> dict[str, str]:
    """Turn nested YAML into dotted keys: ``{'a': {'b': 'x'}}`` -> ``{'a.b': 'x'}``."""
    result: dict[str, str] = {}
    if isinstance(data, dict):
        for key, value in data.items():
            result.update(flatten(value, f"{prefix}{key}."))
    elif isinstance(data, list):
        # A list becomes numbered keys, so multi-line help can live in YAML.
        for index, value in enumerate(data):
            result.update(flatten(value, f"{prefix}{index}."))
    elif data is not None:
        result[prefix.rstrip(".")] = str(data)
    return result


class Translator:
    def __init__(self, language: str = DEFAULT_LANGUAGE):
        self.language = language
        #: language -> {key: template}
        self._catalogue: dict[str, dict[str, str]] = {}
        self._missing: set[str] = set()

    # -- loading -------------------------------------------------------------

    def load_directory(self, directory: Path, *, namespace: str = "") -> int:
        """Load every ``<language>.yml`` in a directory. Returns keys added.

        ``namespace`` prefixes the keys, which is how a plugin's translations
        stay separate from the core's without the plugin having to repeat its
        own id in every key.
        """
        directory = Path(directory)
        if not directory.is_dir():
            return 0

        added = 0
        for path in sorted(directory.glob("*.yml")) + sorted(directory.glob("*.yaml")):
            language = path.stem
            try:
                data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            except (OSError, yaml.YAMLError) as exc:
                logger.error("Could not read translations from %s: %s", path, exc)
                continue

            entries = flatten(data)
            if namespace:
                entries = {f"{namespace}.{key}": value for key, value in entries.items()}
            self._catalogue.setdefault(language, {}).update(entries)
            added += len(entries)
        return added

    def unload_namespace(self, namespace: str) -> None:
        """Drop a plugin's keys, so unloading it leaves nothing behind."""
        prefix = f"{namespace}."
        for entries in self._catalogue.values():
            for key in [k for k in entries if k.startswith(prefix)]:
                del entries[key]

    # -- lookup --------------------------------------------------------------

    def has(self, key: str, *, language: str | None = None) -> bool:
        language = language or self.language
        return key in self._catalogue.get(language, {}) or key in self._catalogue.get(
            DEFAULT_LANGUAGE, {}
        )

    def translate(self, key: str, /, *args: Any, **kwargs: Any) -> str:
        """Look up ``key`` and fill in its placeholders."""
        template = self._lookup(key)
        if template is None:
            # Warn once per key: a chat command firing every second must not
            # turn one missing translation into a flood.
            if key not in self._missing:
                self._missing.add(key)
                logger.warning("No translation for %r in %r or English", key, self.language)
            return key

        if not args and not kwargs:
            return template
        try:
            return template.format(*args, **kwargs)
        except (KeyError, IndexError, ValueError) as exc:
            logger.warning("Translation %r does not fit its arguments (%s)", key, exc)
            return template

    #: Short alias, the name plugins actually call.
    tr = translate

    def _lookup(self, key: str) -> str | None:
        for language in (self.language, DEFAULT_LANGUAGE):
            value = self._catalogue.get(language, {}).get(key)
            if value is not None:
                return value
        return None

    # -- introspection -------------------------------------------------------

    def languages(self) -> list[str]:
        return sorted(self._catalogue)

    def key_count(self, language: str | None = None) -> int:
        return len(self._catalogue.get(language or self.language, {}))

    def missing_keys(self, language: str) -> list[str]:
        """Keys English has that ``language`` does not -- what is left to do."""
        english = set(self._catalogue.get(DEFAULT_LANGUAGE, {}))
        target = set(self._catalogue.get(language, {}))
        return sorted(english - target)

    def set_language(self, language: str) -> None:
        if language not in self._catalogue:
            logger.warning(
                "No translations for %r; falling back to English. Available: %s",
                language, ", ".join(self.languages()) or "none",
            )
        self.language = language


class PluginTranslator:
    """A view of the shared translator scoped to one plugin.

    ``server.tr("failed")`` in a plugin looks up ``<plugin_id>.failed``, and
    falls through to the core catalogue for shared strings like ``common.yes``,
    so a plugin does not have to redefine them.
    """

    def __init__(self, translator: Translator, namespace: str):
        self._translator = translator
        self._namespace = namespace

    def translate(self, key: str, /, *args: Any, **kwargs: Any) -> str:
        namespaced = f"{self._namespace}.{key}"
        if self._translator.has(namespaced):
            return self._translator.translate(namespaced, *args, **kwargs)
        return self._translator.translate(key, *args, **kwargs)

    tr = translate

    @property
    def language(self) -> str:
        return self._translator.language
