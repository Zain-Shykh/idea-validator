"""Prometheus metrics that more than one service defines (a name can be registered only once per
process, and tests load several services together)."""

from prometheus_client import Counter

# M10 counts its own fallbacks; M13 counts the ones reported in traces (§15.1)
# M12 and M9 count what they block; M13 counts the ones reported in traces
INJECTIONS_BLOCKED = Counter("ideaval_injections_blocked_total", "Blocked injections", ["stage"])
FABRICATED_IDS = Counter("ideaval_fabricated_ids_total", "Cited evidence ids that do not exist")

SCORER_FALLBACKS = Counter("ideaval_scorer_fallbacks_total", "Scorer fallbacks", ["dimension"])
