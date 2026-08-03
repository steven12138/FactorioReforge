"""A calculator for chat: ``==1400/7.5`` and it answers.

Python's own parser does the parsing, and then the tree is walked with an
explicit whitelist of node types. ``eval`` is never called on player input --
not in a sandbox, not with a stripped ``__builtins__``, not at all. Everyone who
has tried that has lost, and the input here comes from anyone who can type in
chat.

What is left after the whitelist is arithmetic, so the remaining ways to hurt
the server are size rather than access: ``9**9**9`` is a valid expression that
takes a very long time and a lot of memory to evaluate. Exponents and operand
magnitudes are bounded for that reason, before the operation runs rather than
after.
"""

from __future__ import annotations

import ast
import math

#: Long enough for a real question, short enough that parsing cannot be a cost.
MAX_LENGTH = 200

#: A parsed expression this big is not something a player typed on purpose.
MAX_NODES = 120

#: ``2 ** 4096`` is instant; ``2 ** 10**9`` is not. Bound the exponent and the
#: size of what is being raised, since either alone can blow up.
MAX_EXPONENT = 4096
MAX_DIGITS = 4096


class CalcError(Exception):
    """The expression could not be evaluated, with a reason worth showing."""


_BINARY = {
    ast.Add: lambda a, b: a + b,
    ast.Sub: lambda a, b: a - b,
    ast.Mult: lambda a, b: a * b,
    ast.Div: lambda a, b: a / b,
    ast.FloorDiv: lambda a, b: a // b,
    ast.Mod: lambda a, b: a % b,
}

_UNARY = {
    ast.UAdd: lambda a: +a,
    ast.USub: lambda a: -a,
}

_CONSTANTS = {
    "pi": math.pi,
    "e": math.e,
    "tau": math.tau,
    "inf": math.inf,
}

_FUNCTIONS = {
    "abs": abs,
    "round": round,
    "min": min,
    "max": max,
    "sum": lambda *args: sum(args),
    "sqrt": math.sqrt,
    "floor": math.floor,
    "ceil": math.ceil,
    "log": math.log,
    "log2": math.log2,
    "log10": math.log10,
    "exp": math.exp,
    "sin": math.sin,
    "cos": math.cos,
    "tan": math.tan,
    "atan": math.atan,
    "atan2": math.atan2,
    "hypot": math.hypot,
    "gcd": math.gcd,
    "lcm": math.lcm,
    "factorial": lambda n: math.factorial(_bounded_factorial(n)),
}


def _bounded_factorial(n):
    if not isinstance(n, int) or n > 1000:
        raise CalcError("factorial takes a whole number up to 1000")
    return n


def evaluate(expression: str) -> float | int:
    """Evaluate an arithmetic expression, or raise :class:`CalcError`."""
    text = expression.strip()
    if not text:
        raise CalcError("nothing to calculate")
    if len(text) > MAX_LENGTH:
        raise CalcError(f"expression is longer than {MAX_LENGTH} characters")

    try:
        tree = ast.parse(text, mode="eval")
    except SyntaxError as exc:
        raise CalcError(f"that is not an expression ({exc.msg})") from exc

    if sum(1 for _ in ast.walk(tree)) > MAX_NODES:
        raise CalcError("expression is too complicated")

    try:
        return _eval(tree.body)
    except CalcError:
        raise
    except ZeroDivisionError:
        raise CalcError("division by zero") from None
    except (OverflowError, ValueError) as exc:
        raise CalcError(str(exc)) from exc
    except RecursionError:
        raise CalcError("expression is nested too deeply") from None


def _eval(node):
    if isinstance(node, ast.Constant):
        if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
            raise CalcError("only numbers are allowed")
        return node.value

    if isinstance(node, ast.BinOp):
        op = _BINARY.get(type(node.op))
        if op is None:
            if isinstance(node.op, ast.Pow):
                return _power(_eval(node.left), _eval(node.right))
            raise CalcError(f"{_op_name(node.op)} is not supported")
        return _check(op(_eval(node.left), _eval(node.right)))

    if isinstance(node, ast.UnaryOp):
        op = _UNARY.get(type(node.op))
        if op is None:
            raise CalcError(f"{_op_name(node.op)} is not supported")
        return _check(op(_eval(node.operand)))

    if isinstance(node, ast.Name):
        if node.id in _CONSTANTS:
            return _CONSTANTS[node.id]
        raise CalcError(f"unknown name {node.id!r}")

    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name):
            raise CalcError("only plain function calls are allowed")
        function = _FUNCTIONS.get(node.func.id)
        if function is None:
            raise CalcError(f"unknown function {node.func.id!r}")
        if node.keywords:
            raise CalcError("keyword arguments are not supported")
        return _check(function(*(_eval(arg) for arg in node.args)))

    raise CalcError("that is not arithmetic")


def _op_name(op) -> str:
    return type(op).__name__.lower()


def _power(base, exponent):
    """``**`` with the two bounds that keep it from becoming a denial of service."""
    if isinstance(exponent, float) and not exponent.is_integer():
        result = base ** exponent
        return _check(result)
    if abs(exponent) > MAX_EXPONENT:
        raise CalcError(f"exponent above {MAX_EXPONENT} is not allowed")
    if isinstance(base, int) and base and len(str(abs(base))) > MAX_DIGITS:
        raise CalcError("that number is too big to raise to a power")
    return _check(base ** exponent)


def _check(value):
    """Reject results that have grown past what anyone meant to ask for."""
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int) and value.bit_length() > MAX_DIGITS * 4:
        raise CalcError("the result is too large")
    if isinstance(value, float) and math.isinf(value):
        raise CalcError("the result is out of range")
    if not isinstance(value, (int, float)):
        raise CalcError("that produced something which is not a number")
    return value


def format_number(value: float | int, places: int = 6) -> str:
    """Render a result the way a person would write it.

    Whole numbers keep no decimal point, everything else is rounded rather than
    shown to seventeen digits, and a value that rounds to nothing keeps enough
    digits to still say something.
    """
    if isinstance(value, int):
        return f"{value:,}"
    if math.isnan(value):
        return "nan"
    if value == int(value) and abs(value) < 1e15:
        return f"{int(value):,}"
    if value and abs(value) < 10 ** -places:
        return f"{value:.{places}g}"
    text = f"{value:,.{places}f}".rstrip("0").rstrip(".")
    return text or "0"
