import ast
import re
from .models import SceneSpec

class ValidationError(Exception):
    pass

BANNED_MANIM_IMPORTS = {"manimlib", "manimgl"}
BANNED_CALLS = {"os.system", "subprocess", "exec", "eval", "__import__"}

def validate(spec: SceneSpec) -> None:
    """Validate generated code. Raises ValidationError if invalid."""
    if spec.visual_engine == "manim":
        _validate_manim(spec.code)
    else:
        _validate_html(spec.code)

def _validate_manim(code: str) -> None:
    # 1. Syntax check
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        raise ValidationError(f"Python syntax error: {e}") from e

    # 2. Check for exactly one Scene subclass
    scene_classes = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            for base in node.bases:
                base_name = ""
                if isinstance(base, ast.Name):
                    base_name = base.id
                elif isinstance(base, ast.Attribute):
                    base_name = base.attr
                if base_name == "Scene" or base_name in ("ThreeDScene", "MovingCameraScene"):
                    scene_classes.append(node.name)

    if not scene_classes:
        raise ValidationError("No Scene subclass found. Code must define a class extending Scene.")

    # 3. Check for construct method
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name in scene_classes:
            method_names = [n.name for n in node.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
            if "construct" not in method_names:
                raise ValidationError(f"Class {node.name} missing construct() method.")

    # 4. Check for banned imports
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in BANNED_MANIM_IMPORTS:
                    raise ValidationError(f"Banned import: {alias.name}. Use 'from manim import *' instead.")
        elif isinstance(node, ast.ImportFrom):
            if node.module and any(node.module.startswith(b) for b in BANNED_MANIM_IMPORTS):
                raise ValidationError(f"Banned import from: {node.module}")

    # 5. Check for dangerous calls
    code_lower = code.lower()
    for banned in BANNED_CALLS:
        if banned in code_lower:
            raise ValidationError(f"Dangerous call detected: {banned}")

def _validate_html(code: str) -> None:
    # 1. Basic structure check
    if "<html" not in code.lower() and "<!doctype" not in code.lower():
        # Allow partial HTML (just body content)
        if "<div" not in code.lower() and "<svg" not in code.lower():
            raise ValidationError("HTML code must contain basic HTML structure or SVG/div elements.")

    # 2. Check for external resource loads
    external_patterns = [
        r'src=["\']https?://',
        r'href=["\']https?://(?!fonts\.googleapis)',  # allow Google Fonts
        r'fetch\s*\(',
        r'XMLHttpRequest',
        r'import\s+.*from\s+["\']https?://',
    ]
    for pattern in external_patterns:
        if re.search(pattern, code):
            raise ValidationError(f"External resource load detected (pattern: {pattern}). HTML must be self-contained.")

    # 3. No dangerous JS
    dangerous_patterns = [r'\beval\s*\(', r'\bFunction\s*\(']
    for pattern in dangerous_patterns:
        if re.search(pattern, code):
            raise ValidationError(f"Dangerous JavaScript pattern: {pattern}")
