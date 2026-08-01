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
