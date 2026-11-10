"""Command-line versions of the three GUI modes (run them from the project folder).

    python -m src.cli.downloader <gallery URL>   single gallery   (GUI: Single Download)
    python -m src.cli.main                       URLs.txt batch   (GUI: Batch Download)
    python -m src.cli.retry                      retry queue      (GUI: Retry Failed)

src.cli.downloader is also the shared download logic used by the GUI.
"""

from pathlib import Path

# Project folder (the one with EHentaiDownloader.pyw): Downloads/, URLs.txt, cookies.json...
PROJECT_ROOT = Path(__file__).resolve().parents[2]
