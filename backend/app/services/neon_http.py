"""Read-only Neon /sql transport; no native database connection or session.

Wire contract: neondatabase/serverless, src/http/index.ts. Compile our shared
SQLAlchemy SELECTs to $n parameters and decode the catalogue's PostgreSQL types.
This adapter is deliberately limited to catalogue reads, not an ORM driver.
"""

import json
import re

import httpx
from sqlalchemy import Select
from sqlalchemy.dialects.postgresql import dialect
from sqlalchemy.engine import make_url

from app.services.catalogue import unavailable


def connection(settings):
    url = make_url(settings.database_url)
    if (
        not re.fullmatch(r"ep-[a-z0-9-]+(?:\.[a-z0-9-]+)*\.neon\.tech", url.host or "")
        or url.port not in (None, 5432)
        or not all((url.username, url.password, url.database))
        or {"host", "hostaddr", "port"}.intersection(url.query)
    ):
        raise ValueError(
            "neon_http requires a Neon DATABASE_URL with user, password, database "
            "and its original PostgreSQL port (5432)."
        )
    return (
        f"https://{url.host}/sql",
        url.set(drivername="postgresql").render_as_string(hide_password=False),
    )


def prepare(statement):
    if not isinstance(statement, Select):
        raise TypeError("Catalogue HTTPS access accepts SELECT statements only.")
    compiled = statement.compile(
        dialect=dialect(paramstyle="numeric_dollar"),
        compile_kwargs={"render_postcompile": True},
    )
    params = []
    for key in compiled.positiontup:
        value = compiled.params[key]
        # Catalogue predicates use text and integer parameters only. Keeping this
        # explicit avoids silently misencoding future JSON/array/date parameters.
        if value is not None and type(value) not in (str, int):
            raise TypeError("Unsupported catalogue query parameter type.")
        params.append(None if value is None else str(value))
    return {"query": str(compiled), "params": params}


def decode(value, oid):
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError("Expected PostgreSQL text output.")
    if oid in (20, 21, 23):
        return int(value)
    if oid in (114, 3802):
        return json.loads(value)
    if oid in (25, 1042, 1043):
        return value
    raise ValueError("Unsupported catalogue result type.")


def rows(result):
    if result["command"] != "SELECT":
        raise ValueError("Unexpected catalogue command result.")
    fields = result["fields"]
    return [
        {
            field["name"]: decode(value, field["dataTypeID"])
            for field, value in zip(fields, row, strict=True)
        }
        for row in result["rows"]
    ]


class NeonHttpQueries:
    def __init__(self, settings, *, transport=None):
        endpoint, connection_string = connection(settings)
        self.endpoint = endpoint
        self.client = httpx.Client(
            headers={
                "Neon-Connection-String": connection_string,
                "Neon-Raw-Text-Output": "true",
                "Neon-Array-Mode": "true",
                "Neon-Batch-Read-Only": "true",
                "Neon-Batch-Isolation-Level": "RepeatableRead",
            },
            timeout=httpx.Timeout(20, connect=10),
            limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
            follow_redirects=False,
            trust_env=False,
            transport=transport,
        )

    def close(self):
        self.client.close()

    def read(self, *statements):
        payload = {"queries": [prepare(statement) for statement in statements]}
        try:
            response = self.client.post(self.endpoint, json=payload)
            response.raise_for_status()
            results = response.json()["results"]
            if len(results) != len(statements):
                raise ValueError("Incomplete catalogue response.")
            return [rows(result) for result in results]
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            # Never expose response bodies, SQL parameters or the credential
            # header in API errors or telemetry exception chains.
            raise unavailable() from None
