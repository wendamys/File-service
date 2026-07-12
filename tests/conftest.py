import json as json_module

import requests


def make_response(status_code=200, json_data=None, headers=None, content=None):
    """Build a real requests.Response so .json()/.raise_for_status() behave normally."""
    response = requests.Response()
    response.status_code = status_code
    response.headers = requests.structures.CaseInsensitiveDict(headers or {})
    if content is not None:
        response._content = content
    elif json_data is not None:
        response._content = json_module.dumps(json_data).encode()
    else:
        response._content = b""
    return response
