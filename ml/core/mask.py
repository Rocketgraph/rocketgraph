"""
Regex masking — collapses high-cardinality tokens (UUIDs, IPs, numbers, paths…)
into placeholders so Drain3 sees the structural template instead of one
unique log per ID.
"""

import re

_MASKS = [
    (re.compile(r'\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b', re.I), '<UUID>'),
    (re.compile(r'\b(?:\d{1,3}\.){3}\d{1,3}\b'),                                            '<IP>'),
    (re.compile(r'https?://\S+'),                                                            '<URL>'),
    (re.compile(r'/api/[a-zA-Z0-9/_\-.]+'),                                                  '<PATH>'),
    (re.compile(r'\b\d{4}-\d{2}-\d{2}\b'),                                                   '<DATE>'),
    (re.compile(r'\b\d+(?:\.\d+)?(?:ms|s|m|h)\b'),                                           '<DUR>'),
    (re.compile(r'\b[0-9a-f]{7,64}\b', re.I),                                                '<HEX>'),
    (re.compile(r'\b\d+\.\d+\b'),                                                            '<FLOAT>'),
    (re.compile(r'\b\d{4,}\b'),                                                              '<NUM>'),
]


def mask(msg: str) -> str:
    for pat, repl in _MASKS:
        msg = pat.sub(repl, msg)
    return msg
