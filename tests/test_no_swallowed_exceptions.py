import ast
import glob
from pathlib import Path

def test_zero_swallowed_exceptions_in_codebase():
    """Garante que nenhum bloco 'except ...: pass' exista em app/ ou cli.py."""
    violations = []
    root = Path(__file__).resolve().parent.parent

    for pattern in ["app/**/*.py", "cli.py"]:
        for file_path in glob.glob(str(root / pattern), recursive=True):
            code = Path(file_path).read_text(encoding="utf-8")
            tree = ast.parse(code, filename=file_path)
            for node in ast.walk(tree):
                if isinstance(node, ast.ExceptHandler):
                    if len(node.body) == 1 and isinstance(node.body[0], ast.Pass):
                        rel_path = Path(file_path).relative_to(root)
                        violations.append(f"{rel_path}:{node.lineno}")

    assert not violations, f"Encontradas exceções engolidas ('except: pass') em: {violations}"
