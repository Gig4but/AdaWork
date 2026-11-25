"""robots.txt rules as search engines read them (RFC 9309): `*` and `$` wildcards, longest
match wins, Allow wins a tie. Python's urllib.robotparser does neither, and Czech portals use
both (e.g. jenprace.cz `Disallow: /nabidky*page_dbox*`).
"""

import re
from contextlib import suppress
from dataclasses import dataclass, field
from urllib.parse import unquote, urlsplit


@dataclass
class _Rule:
    allow: bool
    path: str
    pattern: re.Pattern[str]


def _compile(path: str) -> re.Pattern[str]:
    anchored = path.endswith("$")
    body = path[:-1] if anchored else path
    regex = ".*".join(re.escape(part) for part in body.split("*"))
    return re.compile(regex + ("$" if anchored else ""))


@dataclass
class RobotsRules:
    rules: list[_Rule] = field(default_factory=list)
    crawl_delay: float | None = None
    sitemaps: list[str] = field(default_factory=list)

    @classmethod
    def allow_all(cls) -> "RobotsRules":
        return cls()

    @classmethod
    def disallow_all(cls) -> "RobotsRules":
        return cls([_Rule(False, "/", _compile("/"))])

    @classmethod
    def parse(cls, text: str, agent: str) -> "RobotsRules":
        """Rules of the group naming `agent` (case-insensitive), else of the `*` group."""
        agent = agent.lower()
        groups: list[tuple[set[str], list[tuple[str, str]]]] = []
        sitemaps: list[str] = []
        current: tuple[set[str], list[tuple[str, str]]] | None = None
        last_was_agent = False
        for raw in text.removeprefix("﻿").splitlines():
            line = raw.split("#", 1)[0].strip()
            if ":" not in line:
                continue
            key, value = (part.strip() for part in line.split(":", 1))
            key = key.lower()
            if key == "user-agent":
                if not last_was_agent or current is None:
                    current = (set(), [])
                    groups.append(current)
                current[0].add(value.lower())
                last_was_agent = True
                continue
            last_was_agent = False
            if key == "sitemap":
                sitemaps.append(value)
            elif current is not None and key in ("allow", "disallow", "crawl-delay"):
                current[1].append((key, value))

        def lines_for(name: str) -> list[tuple[str, str]] | None:
            found = [lines for agents, lines in groups if name in agents]
            return [line for lines in found for line in lines] if found else None

        lines = lines_for(agent)
        if lines is None:
            lines = lines_for("*") or []
        rules, delay = [], None
        for key, value in lines:
            if key == "crawl-delay":
                with suppress(ValueError):
                    delay = float(value)
            elif value:  # an empty Disallow allows everything
                rules.append(_Rule(key == "allow", value, _compile(value)))
        return cls(rules, delay, sitemaps)

    def allowed(self, url: str) -> bool:
        parts = urlsplit(url)
        target = unquote(parts.path or "/") + (f"?{unquote(parts.query)}" if parts.query else "")
        best: _Rule | None = None
        for rule in self.rules:
            if rule.pattern.match(target) and (
                best is None
                or len(rule.path) > len(best.path)
                or (len(rule.path) == len(best.path) and rule.allow)
            ):
                best = rule
        return best is None or best.allow
