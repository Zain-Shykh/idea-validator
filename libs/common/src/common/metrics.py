"""Prometheus metrics that more than one service defines (a name can be registered only once per
process, and tests load several services together)."""

from prometheus_client import Counter

# M10 counts its own fallbacks; M13 counts the ones reported in traces (§15.1)
SCORER_FALLBACKS = Counter("ideaval_scorer_fallbacks_total", "Scorer fallbacks", ["dimension"])
