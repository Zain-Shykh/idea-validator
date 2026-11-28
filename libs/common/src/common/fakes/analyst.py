"""M7 stand-in: the niches and gaps it is given."""

from collections.abc import Iterable

from common.contracts.analyst import Gap, GapsResponse, Niche, NicheListResponse


class FakeAnalyst:
    def __init__(self, niches: Iterable[Niche] = (), gaps: Iterable[Gap] = ()) -> None:
        self._niches = list(niches)
        self._gaps = list(gaps)

    async def niches(self) -> NicheListResponse:
        return NicheListResponse(niches=self._niches)

    async def gaps(self, niche_id: str) -> GapsResponse:
        return GapsResponse(gaps=[g for g in self._gaps if g.niche_id == niche_id])
