"""A second engine, deliberately nothing like Airbyte.

Exists to answer one question with code instead of assertion: does
`IntegrationEngineAdapter` actually abstract an engine, or does it abstract
Airbyte? An interface with one family of implementations behind it has not been
tested as an interface.
"""

from app.adapters.sql_direct.adapter import SqlDirectAdapter

__all__ = ["SqlDirectAdapter"]
