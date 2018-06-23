# This file is part of the three2six project
# https://github.com/mbarkhau/three2six
# (C) 2018 Manuel Barkhau <mbarkhau@gmail.com>
#
# SPDX-License-Identifier:    MIT

import re
import ast
import astor
import typing as typ
import typing_extensions as typext

from . import common
from . import fixers
from . import checkers


DEFAULT_SOURCE_ENCODING_DECLARATION = "# -*- coding: {} -*-"

DEFAULT_SOURCE_ENCODING = "utf-8"

# https://www.python.org/dev/peps/pep-0263/
SOURCE_ENCODING_RE = re.compile(r"""
    ^
    [ \t\v]*
    \#.*?coding[:=][ \t]*
    (?P<coding>[-_.a-zA-Z0-9]+)
""", re.VERBOSE)


def parse_module_header(module_source: typ.Union[bytes, str]) -> typ.Tuple[str, str]:
    shebang = False
    coding_declared = False
    coding = DEFAULT_SOURCE_ENCODING

    header_lines: typ.List[str] = []

    # NOTE (mb 2018-06-23): Sneaky replacement of coding is done during
    #   consumption of the generator.
    source_lines: typ.Iterable[str] = (
        line.decode(coding, errors="ignore") if isinstance(line, bytes) else line
        for line in module_source.splitlines()
    )

    for i, line in enumerate(source_lines):
        if i < 2:
            if i == 0 and line.startswith("#!") and "python" in line:
                shebang = True
            else:
                m = SOURCE_ENCODING_RE.match(line)
                if m:
                    coding = m.group("coding").strip()
                    coding_declared = True

        if not line.rstrip() or line.rstrip().startswith("#"):
            header_lines.append(line)
        else:
            break

    if not coding_declared:
        coding_declaration = DEFAULT_SOURCE_ENCODING_DECLARATION.format(coding)
        if shebang:
            header_lines.insert(1, coding_declaration)
        else:
            header_lines.insert(0, coding_declaration)

    header = "\n".join(header_lines) + "\n"
    return coding, header


class CheckerOrFixer(typext.Protocol):

    __name__: str

    def __call__(
        self, cfg: common.BuildConfig, tree: ast.Module
    ) -> typ.Optional[ast.Module]:
        ...


T = typ.TypeVar("T", CheckerOrFixer, CheckerOrFixer)


def iter_fuzzy_selected_classes(
    names: typ.Union[str, typ.List[str]], module: object, clazz: T
) -> typ.Iterable[T]:
    if isinstance(names, str):
        names = names.split(",")

    names = [name.strip() for name in names if name.strip()]

    assert isinstance(clazz, type)
    clazz_name = clazz.__name__
    assert clazz_name.endswith("Base")
    assert getattr(module, clazz_name) is clazz
    optional_suffix = clazz_name[:-4]

    def normalize_name(name: str) -> str:
        name = name.lower().replace("_", "").replace("-", "")
        if name.endswith(optional_suffix.lower()):
            name = name[:-len(optional_suffix)]
        return name

    maybe_classes = {
        name: getattr(module, name)
        for name in dir(module)
        if not name.endswith(clazz_name)
    }
    available_classes = {
        normalize_name(attr_name): attr
        for attr_name, attr in maybe_classes.items()
        if type(attr) == type and issubclass(attr, clazz)
    }

    # Nothing explicitly selected -> all selected
    if any(names):
        selected_names = [
            normalize_name(name)
            for name in names
        ]
    else:
        selected_names = list(available_classes.keys())

    assert len(selected_names) > 0

    for name in selected_names:
        yield available_classes[name]()


def transpile_module(cfg: common.BuildConfig, module_source: str) -> str:
    checker_names = cfg.get("checkers", "")
    fixer_names = cfg.get("fixers", "")
    module_tree = ast.parse(module_source)

    for checker in iter_fuzzy_selected_classes(checker_names, checkers, checkers.CheckerBase):
        checker(cfg, module_tree)

    for fixer in iter_fuzzy_selected_classes(fixer_names, fixers, fixers.FixerBase):
        maybe_fixed_module = fixer(cfg, module_tree)
        if maybe_fixed_module is None:
            raise Exception(f"Error running fixer {type(fixer).__name__}")
        module_tree = maybe_fixed_module

    coding, header = parse_module_header(module_source)
    return header + "".join(astor.to_source(module_tree))


def transpile_module_data(cfg: common.BuildConfig, module_source_data: bytes) -> bytes:
    coding, header = parse_module_header(module_source_data)
    module_source = module_source_data.decode(coding)
    fixed_module_source = transpile_module(cfg, module_source)
    return fixed_module_source.encode(coding)
