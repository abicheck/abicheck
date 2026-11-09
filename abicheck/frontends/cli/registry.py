# SPDX-License-Identifier: Apache-2.0
"""Load command implementations on demand, including through Click introspection.

Root help reads declarations from the shipped Python source instead of importing
the analysis pipeline. If a declaration is dynamic or source is unavailable,
load the real command; correctness takes precedence over the startup shortcut.
"""

from __future__ import annotations

import importlib
from collections.abc import Mapping
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from typing import TYPE_CHECKING, Any, TypeVar, cast, overload

import click

from .runtime import _AbicheckGroup

if TYPE_CHECKING:
    from rich_click import RichContext, RichHelpFormatter

_T = TypeVar("_T")
_HELP_ONLY: ContextVar[bool] = ContextVar("abicheck_cli_help_only", default=False)


@dataclass(frozen=True, slots=True)
class CommandSpec:
    name: str
    module: str
    callback: str


ROOT_COMMANDS = (
    CommandSpec("aggregate", "abicheck.cli_aggregate", "aggregate_cmd"),
    CommandSpec("project", "abicheck.cli_project", "project_group"),
    CommandSpec("deps", "abicheck.cli_stack", "deps_group"),
    CommandSpec("compare", "abicheck.frontends.cli.commands.compare", "compare_cmd"),
    CommandSpec("dump", "abicheck.frontends.cli.commands.dump", "dump_cmd"),
)


def _help_command(spec: CommandSpec) -> click.Command | None:
    """Read the actual callback docstring and literal command options."""
    import ast

    path = (
        Path(__file__)
        .resolve()
        .parents[3]
        .joinpath(*spec.module.split("."))
        .with_suffix(".py")
    )
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        callback = next(
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == spec.callback
        )
        for decorator in callback.decorator_list:
            if not (
                isinstance(decorator, ast.Call)
                and isinstance(decorator.func, ast.Attribute)
                and isinstance(decorator.func.value, ast.Name)
                and decorator.func.value.id == "main"
                and decorator.func.attr in {"command", "group"}
            ):
                continue
            if any(kw.arg in {None, "cls"} for kw in decorator.keywords):
                return None
            options = {
                kw.arg: ast.literal_eval(kw.value)
                for kw in decorator.keywords
                if kw.arg in {"help", "short_help", "hidden", "deprecated"}
            }
            cls = click.Group if decorator.func.attr == "group" else click.Command
            options.setdefault("help", ast.get_docstring(callback))
            return cls(spec.name, **options)
    except (OSError, SyntaxError, StopIteration, ValueError, TypeError):
        return None
    return None


class _CommandMap(dict[str, click.Command]):
    """Keep dict views and direct parameter introspection working normally."""

    def __init__(self, owner: LazyGroup, initial: Mapping[str, click.Command]) -> None:
        super().__init__(initial)
        self.owner = owner

    def __getitem__(self, name: str) -> click.Command:
        if name in self.owner.specs:
            if _HELP_ONLY.get():
                return self.owner.command_for_help(name)
            self.owner.load_command(name)
        return super().__getitem__(name)

    def __setitem__(self, name: str, command: click.Command) -> None:
        super().__setitem__(name, command)
        if name in self.owner.specs and name not in self.owner._loading:
            self.owner._loaded.add(name)

    def __iter__(self) -> Any:
        # Also make dict(commands) use the mapping protocol, not CPython's
        # dict-subclass fast copy of still-unloaded placeholders.
        return iter(super().keys())

    @overload
    def get(self, key: str, default: None = None) -> click.Command | None: ...

    @overload
    def get(self, key: str, default: click.Command) -> click.Command: ...

    @overload
    def get(self, key: str, default: _T) -> click.Command | _T: ...

    def get(self, key: str, default: _T | None = None) -> click.Command | _T | None:
        return self[key] if key in self else default

    def _load_all(self) -> None:
        if not _HELP_ONLY.get():
            for name in tuple(super().keys()):
                if name in self.owner.specs:
                    self.owner.load_command(name)

    def items(self) -> Any:
        if _HELP_ONLY.get():
            return {name: self[name] for name in super().keys()}.items()
        self._load_all()
        return super().items()

    def values(self) -> Any:
        if _HELP_ONLY.get():
            return {name: self[name] for name in super().keys()}.values()
        self._load_all()
        return super().values()

    def copy(self) -> dict[str, click.Command]:
        if _HELP_ONLY.get():
            return {name: self[name] for name in super().keys()}
        self._load_all()
        return super().copy()


class LazyGroup(_AbicheckGroup):
    """Only a group with explicit specs is lazy; subgroups remain ordinary."""

    def __init__(
        self, *args: Any, command_specs: tuple[CommandSpec, ...] = (), **kwargs: Any
    ) -> None:
        super().__init__(*args, **kwargs)
        self.specs = {spec.name: spec for spec in command_specs}
        self._loaded: set[str] = set()
        self._loading: set[str] = set()
        self._load_lock = RLock()
        if self.specs:
            self.commands = _CommandMap(self, self.commands)
            for name in self.specs:
                dict.__setitem__(self.commands, name, click.Command(name))

    def load_command(self, name: str) -> click.Command:
        with self._load_lock:
            if name not in self._loaded and name not in self._loading:
                self._loading.add(name)
                try:
                    spec = self.specs[name]
                    module = importlib.import_module(spec.module)
                    command = getattr(module, spec.callback)
                    if not isinstance(command, click.Command):
                        raise TypeError(
                            f"{spec.module}.{spec.callback} is not a Click command"
                        )
                    self.add_command(command, name)
                    self._loaded.add(name)
                finally:
                    self._loading.remove(name)
            return dict.__getitem__(cast(dict[str, click.Command], self.commands), name)

    def get_command(self, ctx: click.Context, cmd_name: str) -> click.Command | None:
        if cmd_name not in self.commands:
            return None
        if cmd_name not in self.specs:
            return super().get_command(ctx, cmd_name)
        if _HELP_ONLY.get():
            return self.command_for_help(cmd_name)
        return self.load_command(cmd_name)

    def command_for_help(self, name: str) -> click.Command:
        if name not in self._loaded:
            metadata = _help_command(self.specs[name])
            if metadata is not None:
                return metadata
        return self.load_command(name)

    def format_help(self, ctx: click.Context, formatter: click.HelpFormatter) -> None:
        token = _HELP_ONLY.set(True)
        try:
            super().format_help(
                cast("RichContext", ctx), cast("RichHelpFormatter", formatter)
            )
        finally:
            _HELP_ONLY.reset(token)
