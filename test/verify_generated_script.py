"""Checks that an exported script looks right and produced a real image.

Run after e2e_save_as_script.py has downloaded a script and that script has been
executed, to confirm the export reflects the workflow rather than just being
syntactically valid Python.
"""

import argparse
import ast
import os
import sys

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def check_script(path, expected):
    with open(path, encoding="utf-8") as f:
        source = f.read()

    # A truncated or error-page download would fail to parse
    try:
        ast.parse(source)
    except SyntaxError as e:
        raise SystemExit(f"Exported script is not valid Python: {e}")

    missing = [node for node in expected if node not in source]
    if missing:
        raise SystemExit(
            f"Exported script does not reference {missing}; the workflow may not have "
            "loaded before it was exported."
        )

    print(f"{path}: valid Python, references {', '.join(expected)}")


def check_image(path):
    if not os.path.isfile(path):
        raise SystemExit(f"The generated script did not produce {path}")
    with open(path, "rb") as f:
        header = f.read(len(PNG_MAGIC))
    if header != PNG_MAGIC:
        raise SystemExit(f"{path} is not a PNG (got {header!r})")
    print(f"{path}: valid PNG, {os.path.getsize(path)} bytes")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--script", required=True, help="The exported Python script")
    parser.add_argument("--expect", nargs="*", default=[], help="Node types the script must reference")
    parser.add_argument("--image", default=None, help="PNG the script was expected to write")
    args = parser.parse_args()

    check_script(args.script, args.expect)
    if args.image:
        check_image(args.image)


if __name__ == "__main__":
    main()
