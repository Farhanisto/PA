"""Allowlisted tool registry.

Only allowlisted tools may run. Model output can never invoke an arbitrary
function: names and arguments are validated here before any side effect.
"""

from pa.tools import registry  # noqa: F401
