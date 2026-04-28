"""Connection configuration for the Linear MCP server.

The Connection definition must match the hosted Linear MCP server's
expected credential schema (connection name + secret key name).

SecretValues encrypts the token client-side; it is only decrypted
inside the Dedalus secure enclave at dispatch time.
"""

import os

from dedalus_mcp.auth import Connection, SecretKeys, SecretValues
from dotenv import load_dotenv


load_dotenv()

linear = Connection(
    name="linear-mcp",
    secrets=SecretKeys(token="LINEAR_ACCESS_TOKEN"),
    base_url="https://api.linear.app",
    auth_header_format="Bearer {api_key}",
)

linear_secrets = SecretValues(linear, token=os.getenv("LINEAR_API_KEY", ""))
