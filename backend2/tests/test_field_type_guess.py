import pytest

from generation.backend_reconciler import _guess_type


@pytest.mark.parametrize(
    "name",
    ["imageUrl", "image", "message", "page", "language", "package",
     "manager", "country", "account", "feedback", "altText", "phone"],
)
def test_text_like_names_are_never_numbers(name):
    # Regression: substring matching typed these as integer/float
    # ("im-age-Url" contains "age"), so seeding stored 101, 102 ... and the
    # gallery never got image files.
    assert _guess_type(name) == "string"


@pytest.mark.parametrize(
    "name,expected",
    [("price", "float"), ("totalAmount", "float"), ("rating", "float"),
     ("quantity", "integer"), ("stockCount", "integer"), ("age", "integer"),
     ("availableSpots", "integer"), ("isActive", "boolean"), ("tags", "list")],
)
def test_real_numeric_names_still_numeric(name, expected):
    assert _guess_type(name) == expected
