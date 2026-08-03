# Contributing

Bug reports, plugins and patches are welcome. 中文说明见
[CONTRIBUTING_zh.md](CONTRIBUTING_zh.md)。

## Getting set up

```bash
git clone https://github.com/steven12138/FactorioReforge.git
cd FactorioReforge
./scripts/install.sh          # or --no-server if you already have Factorio
.venv/bin/python -m pytest tests/ -q
```

The main branch is **`master`**.

## Before you open a pull request

```bash
.venv/bin/python -m pytest tests/ -q       # all 279 must pass
.venv/bin/ruff check .
.venv/bin/python scripts/check_docs.py     # no dead links or anchors
```

CI runs the same three on Python 3.11 through 3.13.

## What a change should come with

**A test that fails without it.** Most of the tests here exist because something
actually broke: a deadlock on `!!qb make`, a reload that silently reused stale
bytecode, a translation key YAML parsed as a boolean. Each one is a test now so
it stays fixed.

**Both languages, if it is user-visible.** Any string a person reads goes
through the translator, and `en.yml` and `zh_cn.yml` must stay in step. Tests
check that neither has keys the other lacks and that matching keys carry the
same placeholders.

**Documentation, if it changes what an operator does.** [Commands](docs/commands.md)
for a new command, [Plugins](docs/plugins.md) for a new setting,
[Architecture](docs/architecture.md) for anything about how it works inside —
and the `_zh` counterpart of whichever you touched.

## Style

Ruff enforces the mechanical parts (`ruff.toml`); the rest is convention.

**Comments say why, not what.** A comment repeating the code is noise; a comment
explaining why the obvious approach was wrong is the whole reason the line looks
like that. Most of the useful comments in this codebase are of the second kind,
because most of the surprises are Factorio's rather than Python's.

**Measure before you assume.** Three of this project's original design decisions
were wrong about how Factorio behaves — stdout buffering, stdin EOF, headless
screenshots — and each was settled by running the real server, not by reading
the wiki. If you are about to work around something Factorio does, check that it
does it. [Factorio notes](docs/factorio-notes.md) is where those measurements
go, and `scripts/probe_stdout.py` is a template for making one.

**Fail loudly at the boundary.** RCON down and Lua failed each raise, rather
than returning something plausible. A silent degradation to an empty list is
indistinguishable from a genuine empty list.

**Never `/c`.** Everything runs through `/sc` (silent-command). `/c` marks a
save as cheated permanently, and a test greps the whole tree for it.

## Writing a plugin

You do not need to contribute it here — drop a directory into `plugins/` and it
loads. [Writing plugins](docs/writing-plugins.md) covers the API, the events,
storage and translations.

If you do want a plugin bundled, it needs both language catalogues, tests for
its pure logic, and an entry in [Plugins](docs/plugins.md) and its `_zh`
counterpart.

## Reporting a bug

Include your Factorio version (`./bin/x64/factorio --version`), the relevant
part of `logs/reforge.log`, and your `config.yml` **with the RCON password
removed**. The log is deliberately uncoloured and the password is redacted where
FactorioReforge prints it, but `config.yml` holds it in plain text.
