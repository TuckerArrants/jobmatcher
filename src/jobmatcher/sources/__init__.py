"""Source adapters. Each ATS module exposes:

* ``NAME``: the value used in ``companies.yaml`` (``ats: greenhouse``)
* ``board_url(token)``: the public endpoint for one company's board
* ``parse(data, company)``: the endpoint's JSON to a list of ``Posting`` (pure, tested offline)
* ``fetch(token, company, http)``: one request, then ``parse``
"""

from __future__ import annotations

from jobmatcher.sources import ashby, greenhouse, lever

ATS = {m.NAME: m for m in (greenhouse, lever, ashby)}

__all__ = ["ATS"]
