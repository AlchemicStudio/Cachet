#!/usr/bin/env python3
"""Point d'entrée de l'exécutable GUI (binaire fenêtré).

Lance directement l'interface graphique CustomTkinter, SANS avoir à passer
``--gui`` : c'est le point d'entrée du binaire fenêtré « signApp » produit par
PyInstaller (voir ``signApp.spec``). Le binaire console « signApp-cli » utilise
au contraire ``sign_pdfs_beid.py`` (la CLI). Double-cliquer sur « signApp »
ouvre donc directement la fenêtre.

Les mêmes drapeaux que la CLI sont acceptés (``--lib`` notamment) et transmis à
la GUI ; sans argument, la fenêtre s'ouvre avec les valeurs par défaut.
"""

from __future__ import annotations

import sys

from sign_pdfs_beid import build_arg_parser


def main() -> int:
    args = build_arg_parser().parse_args()
    try:
        from gui import launch_gui
    except Exception as exc:  # noqa: BLE001 - on veut un message lisible
        # Sur le binaire fenêtré (console=False), stderr n'est pas visible :
        # on tente une boîte de dialogue native avant de retomber sur stderr.
        msg = (
            f"Impossible de charger l'interface graphique : {exc}\n"
            "Cet exécutable embarque pourtant Tk/CustomTkinter ; "
            "signale ce message au support."
        )
        try:
            import tkinter.messagebox as mb

            mb.showerror("signApp", msg)
        except Exception:  # noqa: BLE001
            print(msg, file=sys.stderr)
        return 1
    return launch_gui(args)


if __name__ == "__main__":
    raise SystemExit(main())
