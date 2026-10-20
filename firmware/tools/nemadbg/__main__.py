import sys

from .cli import main

# 文字コードが合わない端末でも例外にしない
for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(errors="replace")

sys.exit(main())
