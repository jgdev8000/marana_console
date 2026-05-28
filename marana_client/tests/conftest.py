"""Force Qt to use the offscreen platform plugin for headless test runs."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
