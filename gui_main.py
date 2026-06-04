#!/usr/bin/env python3
"""Entry point of the GUI executable (windowed binary).

Launches the CustomTkinter graphical interface directly, WITHOUT having to pass
``--gui``: this is the entry point of the windowed "cachet" binary produced by
PyInstaller (see ``cachet.spec``). The console binary "cachet-cli" instead
uses ``sign_pdfs_beid.py`` (the CLI). Double-clicking "cachet" therefore opens
the window directly.

The same flags as the CLI are accepted (notably ``--lib``) and passed on to the
GUI; with no argument, the window opens with the default values.
"""

from __future__ import annotations

import sys

from sign_pdfs_beid import build_arg_parser


def main() -> int:
    args = build_arg_parser().parse_args()
    try:
        from gui import launch_gui
    except Exception as exc:  # noqa: BLE001 - we want a readable message
        # On the windowed binary (console=False), stderr is not visible:
        # we try a native dialog box before falling back to stderr.
        msg = (
            f"Could not load the graphical interface: {exc}\n"
            "This executable bundles Tk/CustomTkinter; "
            "please report this message to support."
        )
        try:
            import tkinter.messagebox as mb

            mb.showerror("Cachet", msg)
        except Exception:  # noqa: BLE001
            print(msg, file=sys.stderr)
        return 1
    return launch_gui(args)


if __name__ == "__main__":
    raise SystemExit(main())
