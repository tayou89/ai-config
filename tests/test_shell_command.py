#!/usr/bin/env python3
"""Tests for scripts/shell_command.py: which files a command writes, for the hooks that guard file writes.

The in-place editors take scripts, backup suffixes, and script files as arguments;
only the files they edit count as written. macOS sed reads the argument after a
bare `-i` as the backup suffix and stops reading options at its first operand,
while GNU sed takes no separate suffix and reads options anywhere, so when the
flavor is unknown the files of both readings count; an empty or dot-led argument
after `-i` is a suffix in both (GNU sed would fail on such a script and edit
nothing). macOS sed also edits in place with `-I`, which GNU sed lacks, and GNU
sed's `-l` takes a value. The macOS cases were checked against /usr/bin/sed on
2026-11-18.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIRECTORY = REPOSITORY_ROOT / "scripts"
WORK_DIRECTORY = "/tmp"
NO_CONFIG_DIRECTORY = Path("/nonexistent-claude-config")

if str(SCRIPTS_DIRECTORY) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIRECTORY))

from shell_command import expand_shell_command  # noqa: E402

WRITTEN_FILE_CASES: dict[str, tuple[str, list[str]]] = {
    "sed without -i": ("sed 's/A/B/' f", []),
    "sed -i with an empty suffix": ("sed -i '' 's/A/B/' f", ["f"]),
    "sed -i without a suffix": ("sed -i 's/A/B/' f", ["f"]),
    "sed -i with an attached suffix": ("sed -i.bak 's/A/B/' f", ["f"]),
    "sed -i with a separate dot suffix": ("sed -i .bak 's/A/B/' f", ["f"]),
    "sed -i with two -e scripts after an empty suffix": ("sed -i '' -e 's/A/B/' -e 's/C/D/' f g", ["f", "g"]),
    "macOS sed reads everything after its script as files": (
        "sed -i -e 's/A/B/' -e 's/C/D/' f g",
        ["-e", "s/C/D/", "f", "g"],
    ),
    "GNU sed reads -i after the script": ("sed 's/A/B/' -i f", ["f"]),
    "sed -e before -i with an empty suffix": ("sed -e 's/A/B/' -i '' f", ["f"]),
    "sed -i with an attached -e script": ("sed -i -e's/A/B/' f", ["f"]),
    "sed -i with --expression=": ("sed -i --expression='s/A/B/' f", ["f"]),
    "sed -i with --expression and a value": ("sed -i --expression 's/A/B/' f", ["f"]),
    "sed -i with a script file": ("sed -i -f edits.sed f", ["f"]),
    "sed -i with --file=": ("sed -i --file=edits.sed f", ["f"]),
    "sed -n then -i": ("sed -n -i 's/A/B/p' f", ["f"]),
    "sed -E then -i with an empty suffix": ("sed -E -i '' 's/A/B/' f", ["f"]),
    "sed clustered -ni": ("sed -ni 's/A/B/p' f", ["f"]),
    "sed clustered -Ei with an empty suffix": ("sed -Ei '' 's/A/B/' f", ["f"]),
    "sed -ie takes e as the suffix": ("sed -ie 's/A/B/' f", ["f"]),
    "sed --in-place": ("sed --in-place 's/A/B/' f", ["f"]),
    "sed --in-place=": ("sed --in-place=.bak 's/A/B/' f", ["f"]),
    "sed operands after --": ("sed -i 's/A/B/' -- -odd.md", ["-odd.md"]),
    "sed -i with a one-letter GNU script": ("sed -i p README.md", ["README.md"]),
    "sed -i with an empty suffix before -e": ("sed -i '' -e 's/A/B/' f", ["f"]),
    "gsed is sed": ("gsed -i 's/A/B/' f", ["f"]),
    "macOS sed -I with an empty suffix": ("sed -I '' 's/A/B/' f", ["f"]),
    "macOS sed -I with a separate suffix": ("sed -I .bak 's/A/B/' f", ["f"]),
    "macOS sed -I with an attached suffix": ("sed -I.bak 's/A/B/' f", ["f"]),
    "macOS sed clustered -EI": ("sed -EI '' 's/A/B/' f", ["f"]),
    "macOS sed -I before -e": ("sed -I '' -e 's/A/B/' f g", ["f", "g"]),
    "macOS sed -I takes the script as its suffix, GNU sed has no -I": ("sed -I 's/A/B/' f", []),
    "GNU sed -l takes a separate value": ("sed -l 5 -i 's/A/B/' f", ["f"]),
    "GNU sed -l takes an attached value": ("sed -l5 -i 's/A/B/' f", ["f"]),
    "GNU sed abbreviates --in-place": ("sed --in=.bak 's/A/B/' f", ["f"]),
    "GNU sed abbreviates --expression": ("sed --expr 's/A/B/' --in-pl f", ["f"]),
    "sed -f with an attached file name i is not in place": ("sed -fi edits.sed f", []),
    "perl -pi -e": ("perl -pi -e 's/A/B/' f", ["f"]),
    "perl -i -pe with two files": ("perl -i -pe 's/A/B/' f g", ["f", "g"]),
    "perl -pi with an attached suffix": ("perl -pi.bak -e 's/A/B/' f", ["f"]),
    "perl with two -e": ("perl -pi -e 's/A/B/' -e 's/C/D/' f", ["f"]),
    "perl -E": ("perl -pi -E 's/A/B/' f", ["f"]),
    "perl with a program file": ("perl -pi edit.pl f", ["f"]),
    "perl with -M": ("perl -Mstrict -pi -e 's/A/B/' f", ["f"]),
    "perl with a separate -I directory": ("perl -I lib -pi -e 's/A/B/' f", ["f"]),
    "perl without -i": ("perl -ne 'print' f", []),
    "perl -i after digits in a cluster": ("perl -0777pi -e 's/A/B/' f", ["f"]),
    "perl -i after -l digits": ("perl -l0pi -e 's/A/B/' f", ["f"]),
    "tee": ("tee a b", ["a", "b"]),
    "redirection with an in-place edit": ("sed -i 's/A/B/' f > out", ["out", "f"]),
}


class WrittenFilesTest(unittest.TestCase):
    def test_in_place_editors_write_only_the_files_they_edit(self) -> None:
        for name, (command, expected) in WRITTEN_FILE_CASES.items():
            with self.subTest(name, command=command):
                calls = expand_shell_command(command, WORK_DIRECTORY, NO_CONFIG_DIRECTORY)

                self.assertEqual(sorted(calls[0].list_written_files()), sorted(expected))


if __name__ == "__main__":
    unittest.main()
