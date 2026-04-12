"""Guardrails that keep runtime config access behind the Config singleton."""

import ast
from dataclasses import dataclass
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_PACKAGE_ROOT = _REPO_ROOT / "shelfmark"
_SETTINGS_SOURCE_ROOTS = (
    _PACKAGE_ROOT / "config",
    _PACKAGE_ROOT / "metadata_providers",
    _PACKAGE_ROOT / "release_sources",
)
_VALUE_FIELD_TYPES = {
    "TextField",
    "PasswordField",
    "NumberField",
    "CheckboxField",
    "SelectField",
    "MultiSelectField",
    "TagListField",
    "OrderableListField",
    "TableField",
}
_BOOTSTRAP_ENV_ACCESS_ALLOWLIST = {
    Path("shelfmark/config/env.py"),
    Path("shelfmark/core/settings_registry.py"),
}
_RAW_CONFIG_READ_ALLOWLIST = {
    Path("shelfmark/config/notifications_settings.py"),
    Path("shelfmark/config/settings.py"),
    Path("shelfmark/core/admin_settings_routes.py"),
    Path("shelfmark/core/settings_registry.py"),
    Path("shelfmark/core/user_settings_overrides.py"),
}


@dataclass(frozen=True)
class GuardrailViolation:
    """A direct config-access violation found in source."""

    path: Path
    line: int
    message: str


class SettingsFieldCollector(ast.NodeVisitor):
    """Collect registered setting keys and env var names from source."""

    def __init__(self) -> None:
        self.registered_keys: set[str] = set()
        self.registered_env_vars: set[str] = set()
        self._constants: dict[str, str | bool] = {}

    def visit_Assign(self, node: ast.Assign) -> None:
        resolved_value = self._resolve_constant(node.value)

        for target in node.targets:
            for name in self._iter_assigned_names(target):
                if resolved_value is None:
                    self._constants.pop(name, None)
                else:
                    self._constants[name] = resolved_value

        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if node.value is None:
            return

        resolved_value = self._resolve_constant(node.value)
        for name in self._iter_assigned_names(node.target):
            if resolved_value is None:
                self._constants.pop(name, None)
            else:
                self._constants[name] = resolved_value

        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        if not self._looks_like_value_field_definition(node):
            self.generic_visit(node)
            return

        key = self._get_keyword_string(node, "key")
        if key is None:
            self.generic_visit(node)
            return

        self.registered_keys.add(key)

        env_supported = self._get_keyword_bool(node, "env_supported")
        if env_supported is not False:
            self.registered_env_vars.add(self._get_keyword_string(node, "env_var") or key)

        self.generic_visit(node)

    def _looks_like_value_field_definition(self, node: ast.Call) -> bool:
        func_name = self._get_callable_name(node.func)
        if func_name in _VALUE_FIELD_TYPES:
            return True

        return any(
            self._get_callable_name(argument) in _VALUE_FIELD_TYPES for argument in node.args
        )

    def _get_keyword_string(self, node: ast.Call, key: str) -> str | None:
        for keyword in node.keywords:
            if keyword.arg == key:
                resolved = self._resolve_constant(keyword.value)
                if isinstance(resolved, str):
                    return resolved
        return None

    def _get_keyword_bool(self, node: ast.Call, key: str) -> bool | None:
        for keyword in node.keywords:
            if keyword.arg == key:
                resolved = self._resolve_constant(keyword.value)
                if isinstance(resolved, bool):
                    return resolved
        return None

    def _resolve_constant(self, node: ast.AST) -> str | bool | None:
        if isinstance(node, ast.Constant) and isinstance(node.value, (str, bool)):
            return node.value

        if isinstance(node, ast.Name):
            resolved = self._constants.get(node.id)
            if isinstance(resolved, (str, bool)):
                return resolved

        return None

    @staticmethod
    def _get_callable_name(node: ast.AST) -> str | None:
        if isinstance(node, ast.Name):
            return node.id

        if isinstance(node, ast.Attribute):
            return node.attr

        return None

    @staticmethod
    def _iter_assigned_names(target: ast.AST) -> list[str]:
        if isinstance(target, ast.Name):
            return [target.id]

        if isinstance(target, (ast.Tuple, ast.List)):
            names: list[str] = []
            for element in target.elts:
                names.extend(SettingsFieldCollector._iter_assigned_names(element))
            return names

        return []


class ConfigAccessVisitor(ast.NodeVisitor):
    """Scan a module AST for config access that bypasses app_config.get(...)."""

    def __init__(
        self,
        *,
        path: Path,
        registered_keys: set[str],
        registered_env_vars: set[str],
    ) -> None:
        self.path = path
        self.registered_keys = registered_keys
        self.registered_env_vars = registered_env_vars
        self.violations: list[GuardrailViolation] = []
        self._allow_bootstrap_env_access = path in _BOOTSTRAP_ENV_ACCESS_ALLOWLIST
        self._allow_raw_config_reads = path in _RAW_CONFIG_READ_ALLOWLIST
        self._string_scopes: list[dict[str, str]] = [{}]
        self._config_alias_scopes: list[set[str]] = [set()]
        self._env_module_aliases: set[str] = set()
        self._load_config_names = {"load_config_file"}

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            if alias.name == "shelfmark.config.env" and alias.asname:
                self._env_module_aliases.add(alias.asname)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module == "shelfmark.core.settings_registry":
            for alias in node.names:
                if alias.name == "load_config_file":
                    self._load_config_names.add(alias.asname or alias.name)
            return

        if node.module == "shelfmark.config":
            for alias in node.names:
                if alias.name == "env":
                    self._env_module_aliases.add(alias.asname or alias.name)
            return

        if node.module == "shelfmark.config.env":
            if self._allow_bootstrap_env_access:
                return
            for alias in node.names:
                imported_name = alias.name
                if imported_name in self.registered_keys:
                    self._record_violation(
                        node,
                        "direct env-module import",
                        imported_name,
                    )

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._push_scope()
        for statement in node.body:
            self.visit(statement)
        self._pop_scope()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_scoped_body(node.body)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_scoped_body(node.body)

    def visit_Assign(self, node: ast.Assign) -> None:
        resolved_string = self._resolve_string(node.value)
        load_config_alias = self._is_load_config_call(node.value)

        for target in node.targets:
            for name in self._iter_assigned_names(target):
                if resolved_string is not None:
                    self._string_scopes[-1][name] = resolved_string
                else:
                    self._string_scopes[-1].pop(name, None)

                if load_config_alias:
                    self._config_alias_scopes[-1].add(name)
                else:
                    self._config_alias_scopes[-1].discard(name)

        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if node.value is None:
            return

        resolved_string = self._resolve_string(node.value)
        load_config_alias = self._is_load_config_call(node.value)

        for name in self._iter_assigned_names(node.target):
            if resolved_string is not None:
                self._string_scopes[-1][name] = resolved_string
            else:
                self._string_scopes[-1].pop(name, None)

            if load_config_alias:
                self._config_alias_scopes[-1].add(name)
            else:
                self._config_alias_scopes[-1].discard(name)

        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if self._allow_bootstrap_env_access:
            return

        if isinstance(node.value, ast.Name):
            if node.value.id in self._env_module_aliases and node.attr in self.registered_keys:
                self._record_violation(node, "direct env-module access", node.attr)

        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        env_var = self._get_direct_env_lookup(node)
        if env_var is not None and not self._allow_bootstrap_env_access:
            self._record_violation(node, "direct env lookup", env_var)

        config_key = self._get_raw_config_lookup(node)
        if config_key is not None and not self._allow_raw_config_reads:
            self._record_violation(node, "raw config lookup", config_key)

        self.generic_visit(node)

    def visit_Subscript(self, node: ast.Subscript) -> None:
        if isinstance(node.ctx, ast.Load):
            env_var = self._get_direct_env_subscript(node)
            if env_var is not None and not self._allow_bootstrap_env_access:
                self._record_violation(node, "direct env lookup", env_var)

            config_key = self._get_raw_config_subscript(node)
            if config_key is not None and not self._allow_raw_config_reads:
                self._record_violation(node, "raw config lookup", config_key)

        self.generic_visit(node)

    def visit_Compare(self, node: ast.Compare) -> None:
        if self._allow_raw_config_reads:
            self.generic_visit(node)
            return

        if len(node.ops) != 1 or len(node.comparators) != 1:
            self.generic_visit(node)
            return

        key = self._resolve_string(node.left)
        comparator = node.comparators[0]
        if (
            isinstance(node.ops[0], ast.In)
            and key in self.registered_keys
            and self._is_load_config_target(comparator)
        ):
            self._record_violation(node, "raw config lookup", key)

        self.generic_visit(node)

    def _visit_scoped_body(self, body: list[ast.stmt]) -> None:
        self._push_scope()
        for statement in body:
            self.visit(statement)
        self._pop_scope()

    def _push_scope(self) -> None:
        self._string_scopes.append({})
        self._config_alias_scopes.append(set())

    def _pop_scope(self) -> None:
        self._string_scopes.pop()
        self._config_alias_scopes.pop()

    def _resolve_string(self, node: ast.AST) -> str | None:
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value

        if isinstance(node, ast.Name):
            for scope in reversed(self._string_scopes):
                if node.id in scope:
                    return scope[node.id]

        return None

    def _is_load_config_call(self, node: ast.AST) -> bool:
        return self._resolve_load_config_call(node) is not None

    def _resolve_load_config_call(self, node: ast.AST) -> str | None:
        if not isinstance(node, ast.Call):
            return None

        func = node.func
        if isinstance(func, ast.Name) and func.id in self._load_config_names and node.args:
            return self._resolve_string(node.args[0])

        if isinstance(func, ast.Attribute) and func.attr == "load_config_file" and node.args:
            return self._resolve_string(node.args[0])

        return None

    def _is_load_config_target(self, node: ast.AST) -> bool:
        if self._resolve_load_config_call(node) is not None:
            return True

        if isinstance(node, ast.Name):
            return any(node.id in scope for scope in reversed(self._config_alias_scopes))

        return False

    def _get_direct_env_lookup(self, node: ast.Call) -> str | None:
        func = node.func
        if isinstance(func, ast.Attribute):
            if (
                isinstance(func.value, ast.Name)
                and func.value.id == "os"
                and func.attr == "getenv"
                and node.args
            ):
                env_var = self._resolve_string(node.args[0])
                if env_var in self.registered_env_vars:
                    return env_var

            if func.attr == "get" and node.args:
                env_var = self._resolve_string(node.args[0])
                if env_var in self.registered_env_vars and self._is_os_environ(func.value):
                    return env_var

        return None

    def _get_raw_config_lookup(self, node: ast.Call) -> str | None:
        func = node.func
        if not isinstance(func, ast.Attribute) or func.attr != "get" or not node.args:
            return None

        key = self._resolve_string(node.args[0])
        if key not in self.registered_keys:
            return None

        if self._is_load_config_target(func.value):
            return key

        return None

    def _get_direct_env_subscript(self, node: ast.Subscript) -> str | None:
        if not self._is_os_environ(node.value):
            return None

        env_var = self._resolve_string(node.slice)
        if env_var in self.registered_env_vars:
            return env_var

        return None

    def _get_raw_config_subscript(self, node: ast.Subscript) -> str | None:
        if not self._is_load_config_target(node.value):
            return None

        key = self._resolve_string(node.slice)
        if key in self.registered_keys:
            return key

        return None

    @staticmethod
    def _is_os_environ(node: ast.AST) -> bool:
        return (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == "os"
            and node.attr == "environ"
        )

    @staticmethod
    def _iter_assigned_names(target: ast.AST) -> list[str]:
        if isinstance(target, ast.Name):
            return [target.id]

        if isinstance(target, (ast.Tuple, ast.List)):
            names: list[str] = []
            for element in target.elts:
                names.extend(ConfigAccessVisitor._iter_assigned_names(element))
            return names

        return []

    def _record_violation(self, node: ast.AST, access_type: str, key: str) -> None:
        self.violations.append(
            GuardrailViolation(
                path=self.path,
                line=node.lineno,
                message=(
                    f"{access_type} for '{key}' bypasses app_config.get(...) "
                    "or the config singleton"
                ),
            )
        )


def _load_registered_settings() -> tuple[set[str], set[str]]:
    collector = SettingsFieldCollector()

    for root in _SETTINGS_SOURCE_ROOTS:
        for file_path in sorted(root.rglob("*.py")):
            module_ast = ast.parse(file_path.read_text(), filename=str(file_path))
            collector.visit(module_ast)

    return collector.registered_keys, collector.registered_env_vars


def _scan_runtime_modules() -> list[GuardrailViolation]:
    registered_keys, registered_env_vars = _load_registered_settings()
    violations: list[GuardrailViolation] = []

    for file_path in sorted(_PACKAGE_ROOT.rglob("*.py")):
        relative_path = file_path.relative_to(_REPO_ROOT)
        module_ast = ast.parse(file_path.read_text(), filename=str(relative_path))
        visitor = ConfigAccessVisitor(
            path=relative_path,
            registered_keys=registered_keys,
            registered_env_vars=registered_env_vars,
        )
        visitor.visit(module_ast)
        violations.extend(visitor.violations)

    return violations


def test_runtime_code_uses_config_singleton_for_registered_settings() -> None:
    violations = _scan_runtime_modules()

    assert not violations, "\n".join(
        f"{violation.path}:{violation.line}: {violation.message}" for violation in violations
    )
