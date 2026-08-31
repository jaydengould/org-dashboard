import re

TOKEN_RE = re.compile(rb'name="csrf_token" value="([^"]+)"')


def csrf_token(client, path="/login"):
    match = TOKEN_RE.search(client.get(path).data)
    assert match, f"no csrf_token field found on {path}"
    return match.group(1).decode()


def login(client, email, password):
    return client.post(
        "/login",
        data={"email": email, "password": password, "csrf_token": csrf_token(client)},
    )
