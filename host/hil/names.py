"""Device-name matching for the rig's boards ("HILpad <board>")."""

import re


def name_matches(name, wanted):
    """True if `wanted` occurs in `name` and isn't followed by another word character.

    A plain substring test lets "HILpad esp32dev" match "HILpad esp32dev2", another board; this still matches the
    kernel's per-node names such as "HILpad esp32dev Consumer Control".
    """
    return re.search(re.escape(wanted) + r"(?!\w)", name) is not None
