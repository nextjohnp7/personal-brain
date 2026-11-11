"""Minimal PyYAML stub for Python 3.15 (no wheel available).

Provides safe_load() and safe_dump() using JSON as fallback.
huggingface_hub uses these for simple config parsing.
"""

import json
import io
from typing import Any, IO, Optional


class YAMLError(Exception):
    pass


def safe_load(stream: IO[str]) -> Any:
    """Parse YAML stream using JSON-compatible subset."""
    if hasattr(stream, 'read'):
        text = stream.read()
    else:
        text = stream
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        result = {}
        for line in text.strip().split('\n'):
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            if ':' in line:
                key, _, value = line.partition(':')
                key = key.strip().strip('"').strip("'")
                value = value.strip().strip('"').strip("'")
                result[key] = value
        return result


def safe_dump(data: Any, stream: Optional[IO[str]] = None, **kwargs) -> Optional[str]:
    """Dump data to YAML stream using JSON-compatible subset."""
    text = json.dumps(data, indent=2, ensure_ascii=False)
    if stream is not None:
        stream.write(text)
        return None
    return text


def dump(data: Any, stream: Optional[IO[str]] = None, **kwargs) -> Optional[str]:
    return safe_dump(data, stream, **kwargs)


def load(stream: IO[str]) -> Any:
    return safe_load(stream)


__all__ = ['safe_load', 'safe_dump', 'dump', 'load', 'YAMLError']
