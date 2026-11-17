import ast
import glob
from pathlib import Path

# Allowlist explícita de handlers silenciosos legítimos: (caminho_posix, nome_funcao)
ALLOWED_SILENT_EXCEPTIONS: dict[tuple[str, str], str] = {
    (
        "app/job_queue.py",
        "_worker_loop",
    ): "Polling com timeout de 0.4s em queue.Queue.get(timeout=0.4) -> except queue.Empty: continue.",
}


def test_zero_swallowed_exceptions_in_codebase():
    """T12: Garante que nenhum bloco 'except ...: pass' ou 'except ...: continue' exista sem log (exceto allowlist)."""
    violations: list[str] = []
    root = Path(__file__).resolve().parent.parent

    for pattern in ["app/**/*.py", "cli.py"]:
        for file_path in glob.glob(str(root / pattern), recursive=True):
            rel_posix = Path(file_path).relative_to(root).as_posix()
            code = Path(file_path).read_text(encoding="utf-8")
            tree = ast.parse(code, filename=file_path)

            func_by_handler: dict[int, str] = {}
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    for child in ast.walk(node):
                        if isinstance(child, ast.ExceptHandler):
                            func_by_handler[id(child)] = node.name

            for node in ast.walk(tree):
                if isinstance(node, ast.ExceptHandler):
                    if len(node.body) == 1 and isinstance(node.body[0], (ast.Pass, ast.Continue)):
                        func_name = func_by_handler.get(id(node), "<module>")
                        if (rel_posix, func_name) in ALLOWED_SILENT_EXCEPTIONS:
                            continue
                        kind = type(node.body[0]).__name__.lower()
                        violations.append(f"{rel_posix}:{node.lineno} ({func_name}: {kind})")

    assert not violations, f"Encontradas exceções silenciosas (pass/continue sem log) em: {violations}"
