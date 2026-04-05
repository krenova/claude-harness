import yaml
from pathlib import Path

_cache: dict = {}


def load_prompt(yaml_path: str | Path, key: str, **kwargs) -> str:
    """Load a named prompt template from a YAML file and render it."""
    path = Path(yaml_path)
    if path not in _cache:
        with open(path) as f:
            _cache[path] = yaml.safe_load(f)
    return _cache[path][key].format(**kwargs)
