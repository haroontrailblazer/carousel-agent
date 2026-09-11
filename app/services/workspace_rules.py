"""Account-specific learned instructions; shared source files stay read-only."""
import re
from app import tenancy
from app.services import db

_rules = tenancy.ScopedDict()


def without_learned_rules(content: str) -> str:
    return re.sub(r"(?ms)^## Learned rules[^\n]*\n.*?(?=^## |\Z)", "", content)


async def load():
    value = await db.get_config("learned_rules", {})
    _rules.clear()
    _rules.update(value or {})


def content(key: str, base: str) -> str:
    if not tenancy.current():
        return base
    lines = _rules.get(key, [])
    return without_learned_rules(base) + ("\n\n## Learned rules\n\n" + "\n".join(lines) if lines else "")


async def append(key: str, rule: str):
    def update(previous):
        result = dict(previous or {})
        lines = list(result.get(key, []))
        if rule not in lines:
            lines.append(rule)
        result[key] = lines[-50:]
        return result
    saved = await db.update_config("learned_rules", update)
    _rules.clear()
    _rules.update(saved)
    return True, "rule saved to your workspace"
