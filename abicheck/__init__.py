# Copyright 2026 Nikolay Petrov
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""abicheck — ABI compatibility checker."""

from importlib.metadata import PackageNotFoundError, version as _pkg_version

try:
    __version__: str = _pkg_version("abicheck")
except PackageNotFoundError:
    __version__ = "0.0.0.dev0"  # running from source without install

# Initialize the ``model`` package before any other abicheck module can run.
# ``model`` (its ``__init__`` and several submodules) imports the root-level,
# model-layer ``name_classification`` at module scope, while
# ``name_classification`` -- like every cached helper -- imports
# ``model.execution_cache``, which executes ``model/__init__`` first. Entered
# from ``model`` that is harmless; entered from ``name_classification`` (or any
# module that reaches it before ``model``) the package init found a partially
# initialized ``name_classification`` and raised ``ImportError``. Loading
# ``model`` here makes the import order -- and so whether import succeeds --
# independent of which abicheck module a process imports first
# (tests/test_first_import_order.py).
from . import model as _model  # noqa: E402,F401
