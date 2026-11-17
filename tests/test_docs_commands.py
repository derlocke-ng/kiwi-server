"""Every `kiwi-server …` line in the README and docs/ names a command that exists, with flags it
takes: the docs are what a newcomer types."""
import argparse
import glob
import os
import re
import shlex
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "lib"))
from kiwiserver import cli  # noqa: E402


def documented():
    """(doc, command, flags) for each kiwi-server line in a shell block."""
    for doc in ["README.md"] + sorted(glob.glob(os.path.join(ROOT, "docs", "**", "*.md"), recursive=True)):
        with open(os.path.join(ROOT, doc)) as f:
            text = f.read()
        # every fenced block, whatever its language, so that a yaml block's closing fence is never
        # taken for the start of a shell block; then only the shell ones
        for lang, block in re.findall(r"```([A-Za-z]*)\n(.*?)```", text, re.S):
            if lang not in ("", "bash", "sh", "console"):
                continue
            for line in block.splitlines():
                line = line.strip()
                if not line.startswith("kiwi-server "):
                    continue
                try:
                    words = shlex.split(line.split(" #")[0])
                except ValueError:
                    continue
                if len(words) > 1 and not words[1].startswith("-"):
                    yield os.path.relpath(doc, ROOT), words[1], [w.split("=")[0] for w in words[2:] if w.startswith("--")]


def options(parser):
    """The flags of a command and of its own subcommands (kiwi-server vps login …)."""
    out = set()
    for a in parser._actions:
        out.update(a.option_strings)
        if isinstance(a, argparse._SubParsersAction):
            for sub in a.choices.values():
                out |= options(sub)
    return out


class TestDocsCommands(unittest.TestCase):
    def test_the_docs_name_commands_and_flags_that_exist(self):
        parser = cli.build_parser()
        subs = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction)).choices
        found = list(documented())
        self.assertGreater(len(found), 20)
        wrong = []
        for doc, cmd, flags in found:
            if cmd not in subs:
                wrong.append("%s: kiwi-server %s (no such command)" % (doc, cmd))
                continue
            known = options(subs[cmd])
            wrong += ["%s: kiwi-server %s %s (no such flag)" % (doc, cmd, f) for f in flags if f not in known]
        self.assertEqual(wrong, [])


if __name__ == "__main__":
    unittest.main()
