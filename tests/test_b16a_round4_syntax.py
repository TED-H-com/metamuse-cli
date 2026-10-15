"""Additional round-4 names, record shapes, commands and key files."""

import base64
import json

import pytest
from test_b16a_round4 import assert_private, marker


@pytest.mark.parametrize(
    "key",
    [
        "OPENAI_API_KEY",
        "AWS_SECRET_ACCESS_KEY",
        "SECRET_KEY",
        "ssh_private_key",
        "passphrase",
        "sid",
    ],
)
@pytest.mark.parametrize("separator", ["=", ":", "：", "＝"])
def test_r34b_names_and_operators(key, separator):
    value = marker()
    assert_private(f"{key}{separator}{value}", value)


@pytest.mark.parametrize(
    "name",
    ["sid", "Authorization", "Proxy-Authorization", "Cookie", "Set-Cookie", "OPENAI_API_KEY"],
)
@pytest.mark.parametrize("container", ["pair", "cookies"])
def test_r34b_har_records(name, container):
    value = marker()
    record = {"name": name if container == "pair" else "theme", "value": value}
    text = json.dumps(record if container == "pair" else {"cookies": [record]})
    assert_private(text, value)


@pytest.mark.parametrize(
    "template",
    [
        "ALTER USER service IDENTIFIED BY '{v}'",
        "CREATE USER service WITH PASSWORD '{v}'",
        "sshpass -p {v} ssh host",
        "mysql -u service -p{v}",
        "mariadb -p{v}",
        "mysqldump -p{v} database",
    ],
)
def test_r34b_command_credentials(template):
    value = marker()
    assert_private(template.format(v=value), value)


@pytest.mark.parametrize("kind", ["unsigned_jwt", "pgp", "putty", "lowercase_pem"])
def test_r34b_self_identifying_private_values(kind):
    value = marker()
    if kind == "unsigned_jwt":
        header = base64.urlsafe_b64encode(json.dumps({"alg": "none"}).encode()).decode().rstrip("=")
        value = header + "." + base64.urlsafe_b64encode(value.encode()).decode().rstrip("=") + "."
        text = "Found " + value
    elif kind == "putty":
        text = "Pu" + "TTY-User-Key-File-3: ssh-ed25519\nPrivate-Lines: 1\n" + value
    else:
        label = "PGP PRIVATE KEY BLOCK" if kind == "pgp" else "rsa private key"
        text = (
            "-----BE"
            + ("GIN " if kind == "pgp" else "gin ")
            + label
            + "-----\n"
            + value
            + "\n-----E"
            + ("ND " if kind == "pgp" else "nd ")
            + label
            + "-----"
        )
    assert_private(text, value)
