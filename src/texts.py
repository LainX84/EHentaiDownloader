"""Texts shared by the desktop window and the web interface (no GUI imports here)."""

from src.config import ISSUES_URL, PROJECT_URL, UPSTREAM_URL

APP_NAME = "EHentai Downloader"

DISCLAIMER_TITLE = "Before you start"
DISCLAIMER_TEXT = f"""Please read this notice before using {APP_NAME}.

1. No responsibility. The developer takes no responsibility for bans, account restrictions, \
blocks, copyright issues or any other problem that may come from using this program. \
Respect the terms of service of the websites and the copyright of the works you download.

2. As is, free of charge. The program is provided free of charge and "as is", without \
warranty of any kind. You use it at your own risk and under your own responsibility.

3. It may stop working. The websites can change at any moment, so the program may suddenly \
stop working, completely or in part. The source code is open and free (GPL-3.0): bugs and \
problems can be reported on GitHub ({ISSUES_URL}) and will be looked at, but a quick answer \
is not guaranteed. You are free to make your own fork and fix a problem yourself, keeping the \
credits to the original developer ({UPSTREAM_URL}) and to this modified version ({PROJECT_URL}).

4. Made with AI help. This program has been developed and modified with the help of \
Claude AI (Anthropic). Part of the code was not written by hand by the developer, and this \
is stated openly.

If you do not accept these terms, you are strongly advised to delete the program and stop \
using it."""

THIRD_PARTY_TEXT = """Third-party software used by this program (each under its own license):
  • Python and Tkinter – PSF License
  • requests – Apache 2.0
  • Beautiful Soup – MIT
  • Pillow – MIT-CMU (HPND)
  • Rich – MIT
  • ImageMagick (optional, external program) – ImageMagick License
"""
