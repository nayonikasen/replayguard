"""Resolution of dotted names through a module's import aliases.

Rules match on fully-qualified names ("time.sleep", "temporalio.workflow.defn"),
so every check needs to see through aliasing like ``import temporalio.workflow
as wf`` or ``from time import sleep``.
"""

from __future__ import annotations

import ast


class ImportTable:
    def __init__(self) -> None:
        self._aliases: dict[str, str] = {}

    @classmethod
    def from_module(cls, module: ast.Module) -> ImportTable:
        table = cls()
        for node in ast.walk(module):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.asname:
                        table._aliases[alias.asname] = alias.name
                    else:
                        # ``import a.b`` binds only the root name ``a``
                        root = alias.name.split(".")[0]
                        table._aliases[root] = root
            elif isinstance(node, ast.ImportFrom):
                # Relative imports have no knowable absolute origin from a
                # single file, so leave those names unresolved.
                if node.level or node.module is None:
                    continue
                for alias in node.names:
                    if alias.name == "*":
                        continue
                    local = alias.asname or alias.name
                    table._aliases[local] = f"{node.module}.{alias.name}"
        return table

    def resolve(self, node: ast.expr) -> str | None:
        """Fully-qualified dotted name for a Name/Attribute chain, else None.

        Unresolvable roots keep their bare name (``self.client.messages.create``)
        on purpose: suffix-matching rules rely on seeing the full chain even
        when the root is a local variable. Chains rooted in a call or subscript
        (``get_db().execute``) get a sentinel root for the same reason — the
        sentinel can never satisfy an exact or prefix match, only a suffix.
        """
        parts: list[str] = []
        while True:
            if isinstance(node, ast.Attribute):
                parts.append(node.attr)
                node = node.value
            elif isinstance(node, ast.Await):
                node = node.value
            else:
                break
        if isinstance(node, ast.Name):
            parts.append(self._aliases.get(node.id, node.id))
        elif isinstance(node, ast.Call) and parts:
            parts.append("<call>")
        elif isinstance(node, ast.Subscript) and parts:
            parts.append("<subscript>")
        else:
            return None
        return ".".join(reversed(parts))

    def imports_package(self, package: str) -> bool:
        prefix = package + "."
        return any(
            origin == package or origin.startswith(prefix)
            for origin in self._aliases.values()
        )
