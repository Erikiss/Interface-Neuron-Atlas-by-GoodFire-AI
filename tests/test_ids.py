import pytest

from neuron_atlas import ids


@pytest.mark.parametrize(
    ("text", "did", "pole"),
    [
        ("L30_E47_N449", "L30_E47_N449", None),
        ("L30/E47/N449", "L30_E47_N449", None),
        ("l30 e47 n449", "L30_E47_N449", None),
        ("30/47/449", "L30_E47_N449", None),
        ("L9-E196-N344", "L9_E196_N344", None),
        ("L30_E47_N449__pos", "L30_E47_N449", "pos"),
        ("L30_E47_N449__neg pole", "L30_E47_N449", "neg"),
        ("+ L30/E47/N449", "L30_E47_N449", "pos"),
        ("−L30/E47/N449", "L30_E47_N449", "neg"),
        ("L30/E47/N449 -", "L30_E47_N449", "neg"),
        ("layer 30 expert 47 neuron 449 negative", "L30_E47_N449", "neg"),
        ("L4_shared_N126", "L4_shared_N126", None),
    ],
)
def test_parse_reference_accepts_what_people_type(text, did, pole):
    nid, got_pole = ids.parse_reference(text)
    assert nid.did == did
    assert got_pole == pole


@pytest.mark.parametrize("text", ["braille", "1 2 3", "L30", "", "L30_E47", "hash -base64"])
def test_parse_reference_rejects_ordinary_text(text):
    assert ids.parse_reference(text) is None


def test_pid_helpers_round_trip():
    assert ids.make_pid("L4_E1_N10", "+") == "L4_E1_N10__pos"
    assert ids.split_pid("L4_E1_N10__neg") == ("L4_E1_N10", "neg")
    assert ids.other_pole("L4_E1_N10__neg") == "L4_E1_N10__pos"
    assert ids.display_pid("L4_E1_N10__neg") == "− L4/E1/N10"
    assert ids.parse_pid("L4/E1/N10", default_pole="neg") == "L4_E1_N10__neg"
    assert ids.parse_pid("L4/E1/N10") is None
    assert ids.did_parts("L4_shared_N126").expert is None


def test_strict_helpers_reject_loose_spellings():
    assert not ids.is_did("L30/E47/N449")
    assert not ids.is_pid("L30_E47_N449")
    with pytest.raises(ValueError):
        ids.split_pid("L30_E47_N449")
    with pytest.raises(ValueError):
        ids.normalise_pole("sideways")
