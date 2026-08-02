"""Translation lookup, fallback and plugin namespacing."""

from pathlib import Path

import pytest
import yaml

from factorio_reforge.i18n import DEFAULT_LANGUAGE, PluginTranslator, Translator, flatten

CORE_LANG = Path(__file__).resolve().parent.parent / "factorio_reforge" / "lang"


class TestFlatten:
    def test_nested_maps_become_dotted_keys(self):
        assert flatten({"a": {"b": "x"}}) == {"a.b": "x"}

    def test_lists_become_numbered_keys(self):
        assert flatten({"help": ["one", "two"]}) == {"help.0": "one", "help.1": "two"}

    def test_numbers_are_stringified(self):
        assert flatten({"n": 5}) == {"n": "5"}

    def test_nulls_are_dropped(self):
        assert flatten({"a": None}) == {}


@pytest.fixture
def translator(tmp_path):
    (tmp_path / "en.yml").write_text(
        yaml.safe_dump({"greet": "hello {name}", "only_en": "english only"}),
        encoding="utf-8",
    )
    (tmp_path / "zh_cn.yml").write_text(
        yaml.safe_dump({"greet": "你好 {name}"}, allow_unicode=True), encoding="utf-8"
    )
    t = Translator("zh_cn")
    t.load_directory(tmp_path)
    return t


class TestLookup:
    def test_translates_into_the_chosen_language(self, translator):
        assert translator.tr("greet", name="Alice") == "你好 Alice"

    def test_falls_back_to_english_for_an_untranslated_key(self, translator):
        """A half-finished language must stay usable, not develop holes."""
        assert translator.tr("only_en") == "english only"

    def test_a_missing_key_renders_as_the_key(self, translator):
        """Visible beats blank: it says exactly what to add."""
        assert translator.tr("no.such.key") == "no.such.key"

    def test_a_missing_key_is_warned_about_only_once(self, translator, caplog):
        with caplog.at_level("WARNING"):
            translator.tr("no.such.key")
            translator.tr("no.such.key")
        assert caplog.text.count("no.such.key") == 1

    def test_a_template_with_the_wrong_placeholders_does_not_raise(self, translator):
        """A translator's typo should not blow up inside a command handler."""
        assert translator.tr("greet") == "你好 {name}"

    def test_switching_language(self, translator):
        translator.set_language(DEFAULT_LANGUAGE)
        assert translator.tr("greet", name="Bob") == "hello Bob"

    def test_an_unknown_language_warns_and_still_serves_english(self, translator, caplog):
        with caplog.at_level("WARNING"):
            translator.set_language("kl")
        assert "falling back" in caplog.text
        assert translator.tr("greet", name="Bob") == "hello Bob"


class TestPluginNamespace:
    def test_a_plugin_key_wins_over_the_core_one(self, translator, tmp_path):
        plugin_lang = tmp_path / "plugin"
        plugin_lang.mkdir()
        (plugin_lang / "zh_cn.yml").write_text(
            yaml.safe_dump({"greet": "插件的问候 {name}"}, allow_unicode=True),
            encoding="utf-8",
        )
        translator.load_directory(plugin_lang, namespace="myplugin")
        scoped = PluginTranslator(translator, "myplugin")
        assert scoped.tr("greet", name="A") == "插件的问候 A"

    def test_a_plugin_falls_through_to_shared_core_strings(self, translator):
        scoped = PluginTranslator(translator, "myplugin")
        assert scoped.tr("greet", name="A") == "你好 A"

    def test_unloading_a_namespace_removes_only_its_keys(self, translator, tmp_path):
        plugin_lang = tmp_path / "plugin"
        plugin_lang.mkdir()
        (plugin_lang / "zh_cn.yml").write_text(
            yaml.safe_dump({"greet": "插件"}, allow_unicode=True), encoding="utf-8"
        )
        translator.load_directory(plugin_lang, namespace="myplugin")
        translator.unload_namespace("myplugin")
        assert translator.tr("greet", name="A") == "你好 A"


class TestShippedCatalogues:
    """The bundled languages have to stay in step with each other."""

    @pytest.fixture
    def core(self):
        t = Translator()
        t.load_directory(CORE_LANG)
        return t

    def test_english_and_chinese_are_both_present(self, core):
        assert {"en", "zh_cn"} <= set(core.languages())

    def test_chinese_covers_every_english_key(self, core):
        missing = core.missing_keys("zh_cn")
        assert not missing, f"zh_cn is missing: {missing}"

    def test_placeholders_match_between_languages(self, core):
        """A key whose placeholders differ would format wrongly in one language."""
        import re

        english = core._catalogue["en"]
        chinese = core._catalogue["zh_cn"]
        placeholders = lambda text: set(re.findall(r"\{(\w+)\}", text))  # noqa: E731

        mismatched = {
            key: (placeholders(english[key]), placeholders(chinese[key]))
            for key in english
            if key in chinese and placeholders(english[key]) != placeholders(chinese[key])
        }
        assert not mismatched, f"placeholder mismatch: {mismatched}"


class TestPluginInterfaceWiring:
    """The subclass must actually override tr; a silent miss looks like this.

    When PluginServerInterface.tr was absent, plugins fell through to the core
    catalogue and every plugin string rendered as its bare key -- with the
    catalogue loaded and looking correct from the outside.
    """

    def test_the_plugin_interface_overrides_tr(self):
        from factorio_reforge.plugin.interface import PluginServerInterface, ServerInterface

        assert PluginServerInterface.tr is not ServerInterface.tr

    def test_a_plugin_interface_resolves_its_own_namespace(self, tmp_path):
        import yaml

        from factorio_reforge.plugin.interface import PluginServerInterface

        lang = tmp_path / "lang"
        lang.mkdir()
        (lang / "en.yml").write_text(yaml.safe_dump({"greeting": "from the plugin"}))

        translator = Translator()
        translator.load_directory(lang, namespace="demo")

        class FakeCore:
            i18n = translator

        class FakePlugin:
            id = "demo"

        interface = PluginServerInterface(FakeCore(), FakePlugin())
        assert interface.tr("greeting") == "from the plugin"


class TestBundledPluginCatalogues:
    """Every bundled plugin's languages must stay in step with each other.

    This is the check that would have caught a half-translated plugin shipping
    with English holes in the middle of a Chinese session.
    """

    PLUGINS = Path(__file__).resolve().parent.parent / "plugins"

    def catalogues(self):
        """Every plugin's own lang directory."""
        return sorted(
            d / "lang" for d in self.PLUGINS.iterdir()
            if d.is_dir() and not d.name.startswith("_") and (d / "lang").is_dir()
        )

    def test_every_bundled_plugin_ships_translations(self):
        modules = {
            d.name for d in self.PLUGINS.iterdir()
            if d.is_dir() and (d / "__init__.py").is_file()
        }
        translated = {d.parent.name for d in self.catalogues()}
        assert modules <= translated, f"no translations for: {sorted(modules - translated)}"

    def test_every_bundled_plugin_is_a_package(self):
        """A plugin owns its translations, which a solo .py has nowhere to keep."""
        stragglers = [p.name for p in self.PLUGINS.glob("*.py")]
        assert not stragglers, f"still solo files: {stragglers}"

    @pytest.mark.parametrize("language", ["zh_cn"])
    def test_each_plugin_catalogue_matches_english(self, language):
        problems = {}
        for directory in self.catalogues():
            name = directory.parent.name
            translator = Translator()
            translator.load_directory(directory)
            missing = translator.missing_keys(language)
            if missing:
                problems[name] = missing
        assert not problems, f"untranslated keys: {problems}"

    @pytest.mark.parametrize("language", ["zh_cn"])
    def test_placeholders_match_in_every_plugin(self, language):
        import re

        def placeholders(text):
            return set(re.findall(r"\{(\w+)", text))

        problems = {}
        for directory in self.catalogues():
            name = directory.parent.name
            translator = Translator()
            translator.load_directory(directory)
            english = translator._catalogue.get(DEFAULT_LANGUAGE, {})
            other = translator._catalogue.get(language, {})
            for key, template in english.items():
                if key in other and placeholders(template) != placeholders(other[key]):
                    problems[f"{name}.{key}"] = (
                        placeholders(template), placeholders(other[key])
                    )
        assert not problems, f"placeholder mismatch: {problems}"


class TestYamlBooleanKeys:
    """YAML 1.1 reads yes/no/on/off/true/false as booleans, keys included.

    A catalogue with a bare ``yes:`` key silently becomes ``True``, and every
    lookup of ``common.yes`` then renders as the key. It cost one round of
    "why is the server showing common.no in the settings view".
    """

    CATALOGUES = [
        Path(__file__).resolve().parent.parent / "factorio_reforge" / "lang",
        *(
            d / "lang"
            for d in (Path(__file__).resolve().parent.parent / "plugins").iterdir()
            if d.is_dir() and (d / "lang").is_dir()
        ),
    ]

    def test_no_catalogue_has_a_key_yaml_reads_as_a_boolean(self):
        import yaml

        def walk(data, path=""):
            bad = []
            if isinstance(data, dict):
                for key, value in data.items():
                    if isinstance(key, bool):
                        bad.append(f"{path}<{key}>")
                    bad.extend(walk(value, f"{path}{key}."))
            return bad

        offenders = []
        for directory in self.CATALOGUES:
            for path in directory.glob("*.yml"):
                data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
                for problem in walk(data):
                    offenders.append(f"{path.parent.parent.name}/{path.name}: {problem}")
        assert not offenders, (
            "quote these keys, or rename them: " + ", ".join(offenders)
        )
