import json

from .._version import __version__


async def test_config(jp_fetch):
    # When
    response = await jp_fetch("clouder", "config")
    # Then
    assert response.code == 200
    payload = json.loads(response.body)
    assert payload == {
        "extension": "clouder",
        "version": __version__,
    }
