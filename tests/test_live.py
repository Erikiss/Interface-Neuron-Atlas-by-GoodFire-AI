"""A handful of real requests against the published atlas.

Deselected by default (``pytest -m live`` runs them).  They check the things
that only the real upstream can tell: that the release is still discoverable,
that the index files still have the shape the catalog expects, and that a
missing file is still answered the way :mod:`neuron_atlas.upstream` detects.
The whole module issues about ten requests.
"""

from __future__ import annotations

import pytest

from neuron_atlas.catalog import Catalog
from neuron_atlas.config import ALLOWED_RELEASE_HOSTS, Settings
from neuron_atlas.upstream import ABSENT, AtlasSource

pytestmark = pytest.mark.live


@pytest.fixture(scope="module")
def live_source(tmp_path_factory):
    settings = Settings(data_dir=tmp_path_factory.mktemp("live"), requests_per_second=2, max_concurrency=2)
    with AtlasSource(settings) as source:
        yield source


def test_release_is_discoverable(live_source):
    url = live_source.release_url
    assert url.startswith("https://") and url.endswith("/")
    assert any(host in url for host in ALLOWED_RELEASE_HOSTS)


def test_catalog_builds_from_the_real_indices(live_source):
    catalog = Catalog.load(live_source)
    stats = catalog.stats()
    assert stats["poles"] > 5000 and stats["modes"] > 10000
    assert stats["poles_in_public_sample"] == catalog.shell.sample_size
    assert set(stats["categories"]) <= {"mono", "fn", "poly", "dark", "none"}
    some = next(p for p in catalog.iter_poles() if p.has_subcircuit)
    assert some.modes and some.description


def test_missing_files_are_recognised_as_absent(live_source):
    assert live_source.get_json("subcircuit_modes_data/L0_E0_N0__pos.json") is None
    assert live_source.status("subcircuit_modes_data/L0_E0_N0__pos.json") == ABSENT
